import logging
import os
import sqlite3
import tempfile
import random
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, make_response
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash
from itsdangerous import URLSafeSerializer, BadSignature

from generate_and_send import Contact, make_qr, build_message, send_email, configure_logging

load_dotenv()
configure_logging()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
COOKIE_NAME = os.getenv('ADMIN_COOKIE_NAME', 'admin_session')
COOKIE_MAX_AGE = int(os.getenv('ADMIN_COOKIE_MAX_AGE', 28800))
FORCE_SECURE_COOKIE = os.getenv('FORCE_SECURE_COOKIE', 'auto')

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
SCAN_DB_PATH = os.getenv("SCAN_DB_PATH", "scan_tracking.db")



def nanoid(size=12):
    chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
    return ''.join(random.choice(chars) for _ in range(size))

def generate_html_body(fullName):
    htmlBody = f"""
        <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; border: 1px solid #eee; padding: 20px; border-radius: 10px;">
          <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px;">Registration Confirmed</h2>
          
          <p>Dear <strong>{fullName}</strong>,</p>
          
          <p>Thank you for completing your registration. We are pleased to confirm that your details have been successfully processed.</p>
          
          <div style="background-color: #f9f9f9; border-left: 5px solid #3498db; padding: 15px; margin: 20px 0;">
            <p style="margin: 0;"><strong>Important:</strong> Your unique QR code is attached to this email. Please keep this digital copy accessible, as it will be required for attendance verification and access during the event.</p>
          </div>
          
          <p>If you encounter any issues viewing the attachment or have questions regarding your registration, please reply to this email.</p>
          
          <br>
          <p style="margin-bottom: 0;">Best regards,</p>
          <p style="margin-top: 5px;"><strong>Administration Team</strong><br>
          </p>
          
          <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
          <p style="font-size: 11px; color: #999; text-align: center;">This is an automated message. Please do not reply directly if you require immediate technical assistance.</p>
        </div>
    """
    return htmlBody

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
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


def hash_password(password):
    return generate_password_hash(password)


def verify_password(stored_hash, password):
    return check_password_hash(stored_hash, password)


def create_user(name, email, password, role='member'):
    role = role if role in {'admin', 'member'} else 'member'
    password_hash = hash_password(password)
    created_at = datetime.utcnow().isoformat()
    conn = get_db_connection()
    with conn:
        conn.execute(
            "INSERT INTO users (name, email, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, email, role, password_hash, created_at),
        )
    cur = conn.execute("SELECT id, name, email, role, created_at FROM users WHERE email = ?", (email,))
    user = cur.fetchone()
    conn.close()
    return dict(user) if user else None


def get_user_by_email(email):
    conn = get_db_connection()
    user = conn.execute(
        "SELECT id, name, email, role, password_hash, created_at FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    conn.close()
    return dict(user) if user else None


def get_user_by_id(user_id):
    conn = get_db_connection()
    user = conn.execute(
        "SELECT id, name, email, role, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(user) if user else None


def fetch_all_users():
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, name, email, role, created_at FROM users ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def is_admin_user(user):
    return user and user.get('role') == 'admin'


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    return get_user_by_id(user_id)


def create_admin_token(user_id, duration_seconds=COOKIE_MAX_AGE):
    token_id = secrets.token_urlsafe(24)
    now = datetime.utcnow()
    expires_at = (now + timedelta(seconds=duration_seconds)).isoformat()
    conn = get_db_connection()
    with conn:
        conn.execute(
            "INSERT INTO admin_tokens (token_id, user_id, created_at, expires_at, revoked) VALUES (?, ?, ?, ?, 0)",
            (token_id, user_id, now.isoformat(), expires_at),
        )
    conn.close()
    return token_id


def get_admin_token(token_id):
    conn = get_db_connection()
    token = conn.execute(
        "SELECT token_id, user_id, created_at, expires_at, revoked FROM admin_tokens WHERE token_id = ?",
        (token_id,),
    ).fetchone()
    conn.close()
    return dict(token) if token else None


def revoke_admin_token(token_id):
    conn = get_db_connection()
    with conn:
        conn.execute(
            "UPDATE admin_tokens SET revoked = 1, revoked_at = ? WHERE token_id = ?",
            (datetime.utcnow().isoformat(), token_id),
        )
    conn.close()


def _get_serializer():
    return URLSafeSerializer(app.secret_key, salt='admin-session')


def set_admin_cookie(response, user_id):
    token_id = create_admin_token(user_id)
    s = _get_serializer()
    token = s.dumps({'token_id': token_id, 'user_id': user_id, 'ts': datetime.utcnow().isoformat()})
    # decide secure flag: True if request is secure or forced
    if FORCE_SECURE_COOKIE == 'true':
        secure_flag = True
    elif FORCE_SECURE_COOKIE == 'false':
        secure_flag = False
    else:
        secure_flag = request.is_secure

    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=secure_flag,
        samesite='Lax',
        max_age=COOKIE_MAX_AGE,
    )


def verify_admin_cookie():
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    s = _get_serializer()
    try:
        data = s.loads(token)
    except BadSignature:
        return None
    token_id = data.get('token_id')
    user_id = data.get('user_id')
    if not token_id or not user_id:
        return None

    record = get_admin_token(token_id)
    if not record or record['revoked']:
        return None

    expires_at = datetime.fromisoformat(record['expires_at'])
    if expires_at < datetime.utcnow():
        return None

    if record['user_id'] != user_id:
        return None

    return get_user_by_id(user_id)


@app.route('/')
def index():
    return render_template('index.html', user=current_user())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        email = (data.get('email') or '').strip()
        password = (data.get('password') or '').strip()

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        user = get_user_by_email(email)
        if not user or not verify_password(user['password_hash'], password):
            return jsonify({"error": "Invalid credentials"}), 401

        session['user_id'] = user['id']
        session['user_email'] = user['email']
        session['user_name'] = user['name']

        # Prepare response and set secure admin cookie for quick camera-phone access
        if request.is_json:
            response = make_response(jsonify({"success": True, "message": "Logged in"}), 200)
        else:
            response = make_response(redirect(url_for('index')))

        set_admin_cookie(response, user['id'])
        return response

    return render_template('login.html')


@app.route('/logout', methods=['GET'])
def logout():
    session.clear()
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value:
        s = _get_serializer()
        try:
            data = s.loads(cookie_value)
            token_id = data.get('token_id')
            if token_id:
                revoke_admin_token(token_id)
        except BadSignature:
            pass

    response = make_response(redirect(url_for('index')))
    response.set_cookie(COOKIE_NAME, '', expires=0)
    return response


@app.route('/users/create', methods=['POST'])
def create_user_route():
    admin = ensure_admin_user()
    if not admin:
        return redirect(url_for('login'))

    data = request.get_json() if request.is_json else request.form
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    password = (data.get('password') or '').strip()
    role = (data.get('role') or 'member').strip().lower()

    if role not in {'admin', 'member'}:
        return jsonify({"error": "role must be 'admin' or 'member'"}), 400

    if not name or not email or not password:
        return jsonify({"error": "name, email, and password are required"}), 400

    try:
        user = create_user(name, email, password, role)
        return jsonify({"success": True, "user": user}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "A user with that email already exists"}), 409


@app.route('/users', methods=['GET'])
def list_users():
    admin = ensure_admin_user()
    if not admin:
        return redirect(url_for('login'))
    return jsonify(fetch_all_users()), 200


@app.route('/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    admin = ensure_admin_user()
    if not admin:
        return redirect(url_for('login'))
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify(user), 200


def ensure_admin_user():
    user = current_user() or verify_admin_cookie()
    if not user or not is_admin_user(user):
        return None
    return user


def fetch_admin_tokens(q='', page=1, per_page=25):
    query_filters = []
    query_params = []
    if q:
        like = f"%{q}%"
        query_filters.append(
            "(t.token_id LIKE ? OR u.email LIKE ? OR u.name LIKE ? OR CAST(t.id AS TEXT) = ?)",
        )
        query_params.extend([like, like, like, q])

    where_clause = " WHERE " + " AND ".join(query_filters) if query_filters else ""
    conn = get_db_connection()
    total = conn.execute(
        f"SELECT COUNT(*) as count FROM admin_tokens t JOIN users u ON t.user_id = u.id{where_clause}",
        tuple(query_params),
    ).fetchone()["count"]
    offset = (page - 1) * per_page
    rows = conn.execute(
        f"SELECT t.token_id, t.user_id, u.email, u.name, t.created_at, t.expires_at, t.revoked, t.revoked_at "
        f"FROM admin_tokens t JOIN users u ON t.user_id = u.id{where_clause} "
        f"ORDER BY t.id DESC LIMIT ? OFFSET ?",
        tuple(query_params + [per_page, offset]),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


@app.route('/admin', methods=['GET'])
def admin_dashboard():
    user = ensure_admin_user()
    if not user:
        return redirect(url_for('login'))

    q = (request.args.get('q') or '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = max(10, min(100, int(request.args.get('per_page', 25))))
    offset = (page - 1) * per_page

    query_filters = []
    query_params = []
    if q:
        like = f"%{q}%"
        query_filters.append(
            "(c.fullname LIKE ? OR c.email LIKE ? OR c.unique_id LIKE ? OR CAST(s.id AS TEXT) = ?)",
        )
        query_params.extend([like, like, like, q])

    where_clause = " WHERE " + " AND ".join(query_filters) if query_filters else ""

    conn = get_db_connection()
    total = conn.execute(
        f"SELECT COUNT(*) as count FROM scans s JOIN contacts c ON s.contact_id = c.id{where_clause}",
        tuple(query_params),
    ).fetchone()["count"]

    rows = conn.execute(
        f"SELECT s.id, c.unique_id, c.fullname, c.email, s.scanned_at, s.scan_count \
           FROM scans s \
           JOIN contacts c ON s.contact_id = c.id{where_clause} \
           ORDER BY s.id DESC LIMIT ? OFFSET ?",
        tuple(query_params + [per_page, offset]),
    ).fetchall()
    conn.close()

    scans = [dict(r) for r in rows]
    total_pages = (total + per_page - 1) // per_page
    return render_template(
        'admin.html',
        user=user,
        scans=scans,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


@app.route('/admin/tokens', methods=['GET'])
def admin_tokens():
    user = ensure_admin_user()
    if not user:
        return redirect(url_for('login'))

    q = (request.args.get('q') or '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = max(10, min(100, int(request.args.get('per_page', 25))))

    tokens, total = fetch_admin_tokens(q=q, page=page, per_page=per_page)
    total_pages = (total + per_page - 1) // per_page
    return render_template(
        'admin_tokens.html',
        user=user,
        tokens=tokens,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


@app.route('/admin/tokens/revoke', methods=['POST'])
def revoke_admin_token_route():
    user = ensure_admin_user()
    if not user:
        return redirect(url_for('login'))

    token_id = (request.form.get('token_id') or '').strip()
    if not token_id:
        return jsonify({"error": "token_id is required"}), 400

    revoke_admin_token(token_id)
    return redirect(url_for('admin_tokens'))


@app.route('/screening', methods=['GET'])
def screening():
    uid = request.args.get('uid', '').strip()
    if not uid:
        return jsonify({"error": "Missing required query parameter: uid"}), 400

    conn = get_db_connection()
    contact = conn.execute(
        "SELECT id, unique_id, fullname, email, phone FROM contacts WHERE unique_id = ?",
        (uid,),
    ).fetchone()
    conn.close()

    if not contact:
        return jsonify({"error": "Contact not found"}), 404

    if current_user() or verify_admin_cookie():
        record_scan(contact['id'])
        return redirect(url_for('thanks', uid=uid, fullname=contact['fullname'], email=contact['email']))

    return render_template(
        'screening.html',
        fullname=contact['fullname'],
        email=contact['email'],
        phone=contact['phone'],
        uid=contact['unique_id'],
        logged_in=False,
    )


@app.route('/generate-qr-email', methods=['POST'])
def generate_qr_email():
    data = request.get_json()
    fields = data["data"].get("fields", [])
    # logging.info("Received data: %s", data)
    logging.info("Extracted fields: %s", fields)
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400

    # Convert fields into a dictionary {label: value}
    field_map = {f.get("label"): f.get("value") for f in fields if f.get("label")}

    # unique_id = data.get('unique_id', '').strip()
    unique_id = nanoid()
    # fullname = data.get('fullname', '').strip()
    # email = data.get('email', '').strip()
    # phone = data.get('phone', '').strip()
    fullname = field_map.get('fullname','Test User').strip()
    print(f"Extracted fullname: '{fullname}'")
    email = field_map.get('email','test@example.com').strip()
    phone = field_map.get('phoneNumber','123-456-7890').strip()
    htmlBody = generate_html_body(fullname)

    if not unique_id or not fullname or not email or not phone:
        return jsonify({"error": "Missing required fields: unique_id, fullname, email, phone"}), 400

    contact = Contact(unique_id=unique_id, fullname=fullname, email=email, phone=phone)
    contact_id = save_contact(unique_id, fullname, phone, email)

    try:
        # Use temp directory for QR codes
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            qr_path = make_qr(contact, output_dir)

            subject = data.get('subject', 'Your Event QR Code')
            body_template = data.get('body', 'Hello {fullname},\n\nPlease find your QR code attached.\n\nCheers,\nTeam')
            html_body_template = htmlBody

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