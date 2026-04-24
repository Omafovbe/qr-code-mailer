import logging
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv

from generate_and_send import Contact, make_qr, build_message, send_email, configure_logging

load_dotenv()
configure_logging()

app = Flask(__name__)

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
SCAN_DB_PATH = os.getenv("SCAN_DB_PATH", "scan_tracking.db")


def get_db_connection():
    conn = sqlite3.connect(SCAN_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unique_id TEXT UNIQUE NOT NULL,
                fullname TEXT NOT NULL,
                phone TEXT,
                email TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id INTEGER NOT NULL,
                scanned_at TEXT NOT NULL,
                scan_count INTEGER NOT NULL,
                FOREIGN KEY (contact_id) REFERENCES contacts(id)
            )
            """
        )
    conn.close()


def save_contact(unique_id, fullname, phone, email):
    """Save or update contact in contacts table, return contact_id."""
    conn = get_db_connection()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO contacts (unique_id, fullname, phone, email) VALUES (?, ?, ?, ?)",
            (unique_id, fullname, phone, email),
        )
    # Fetch the contact_id after insert/update
    cur = conn.execute(
        "SELECT id FROM contacts WHERE unique_id = ?",
        (unique_id,),
    )
    contact = cur.fetchone()
    conn.close()
    return contact["id"] if contact else None


def record_scan(contact_id):
    """Record a scan for a contact, increment scan_count."""
    conn = get_db_connection()
    cur = conn.execute(
        "SELECT MAX(scan_count) as max_count FROM scans WHERE contact_id = ?",
        (contact_id,),
    )
    row = cur.fetchone()
    last_count = row["max_count"] or 0
    next_count = last_count + 1
    scanned_at = datetime.utcnow().isoformat()
    with conn:
        conn.execute(
            "INSERT INTO scans (contact_id, scanned_at, scan_count) VALUES (?, ?, ?)",
            (contact_id, scanned_at, next_count),
        )
    conn.close()
    return next_count


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate-qr-email', methods=['POST'])
def generate_qr_email():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400

    unique_id = data.get('unique_id', '').strip()
    fullname = data.get('fullname', '').strip()
    email = data.get('email', '').strip()
    phone = data.get('phone', '').strip()

    if not unique_id or not fullname or not email or not phone:
        return jsonify({"error": "Missing required fields: unique_id, fullname, email, phone"}), 400

    contact = Contact(unique_id=unique_id, fullname=fullname, email=email, phone=phone)
    contact_id = save_contact(unique_id, fullname, phone, email)

    try:
        # Use temp directory for QR codes
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            qr_path = make_qr(contact, output_dir)

            subject = data.get('subject', 'Your QR Code')
            body_template = data.get('body', 'Hello {fullname},\n\nPlease find your QR code attached.\n\nCheers,\nTeam')
            html_body_template = data.get('html_body')

            message = build_message(contact, qr_path, FROM_EMAIL, subject, body_template, html_body_template)
            send_email(message, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, use_tls=(SMTP_PORT == 587))

        return jsonify({"success": True, "message": f"QR code sent to {email}"}), 200

    except Exception as e:
        logging.error("Error processing request: %s", str(e))
        return jsonify({"error": str(e)}), 500


@app.route('/scan', methods=['GET'])
def scan():
    uid = request.args.get('uid', '').strip()
    if not uid:
        return jsonify({"error": "Missing required query parameter: uid"}), 400

    # Look up contact_id from unique_id
    conn = get_db_connection()
    contact = conn.execute(
        "SELECT id, fullname, email FROM contacts WHERE unique_id = ?",
        (uid,),
    ).fetchone()
    conn.close()

    if not contact:
        return jsonify({"error": "Contact not found"}), 404

    contact_id = contact["id"]
    fullname = contact["fullname"]
    scan_count = record_scan(contact_id)

    return redirect(url_for('thanks', uid=uid, fullname=fullname, email=contact["email"]))


@app.route('/thanks', methods=['GET'])
def thanks():
    uid = request.args.get('uid', '').strip()
    fullname = request.args.get('fullname', 'Guest').strip()
    email = request.args.get('email', '').strip()

    if not uid:
        return jsonify({"error": "Missing uid"}), 400

    conn = get_db_connection()
    scan_row = conn.execute(
        "SELECT scan_count FROM scans WHERE contact_id = (SELECT id FROM contacts WHERE unique_id = ?) ORDER BY id DESC LIMIT 1",
        (uid,),
    ).fetchone()
    conn.close()

    scan_count = scan_row["scan_count"] if scan_row else 0

    return render_template('thanks.html', fullname=fullname, scan_count=scan_count, email=email)


@app.route('/scans', methods=['GET'])
def scans():
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT s.id, c.unique_id, c.fullname, c.email, s.scanned_at, s.scan_count 
           FROM scans s 
           JOIN contacts c ON s.contact_id = c.id 
           ORDER BY s.id DESC LIMIT 100"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/scanner', methods=['GET'])
def scanner():
    return render_template('scanner.html')


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))