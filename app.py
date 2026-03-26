import logging
import os
import tempfile
from pathlib import Path

from flask import Flask, request, jsonify
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

@app.route('/generate-qr-email', methods=['POST'])
def generate_qr_email():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400

    fullname = data.get('fullname', '').strip()
    email = data.get('email', '').strip()
    phone = data.get('phone', '').strip()

    if not fullname or not email or not phone:
        return jsonify({"error": "Missing required fields: fullname, email, phone"}), 400

    contact = Contact(fullname=fullname, email=email, phone=phone)

    try:
        # Use temp directory for QR codes
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            qr_path = make_qr(contact, output_dir)

            subject = data.get('subject', 'Your QR Code')
            body_template = data.get('body', 'Hello {fullname},\n\nPlease find your QR code attached.\n\nCheers,\nTeam')

            message = build_message(contact, qr_path, FROM_EMAIL, subject, body_template)
            send_email(message, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, use_tls=(SMTP_PORT == 587))

        return jsonify({"success": True, "message": f"QR code sent to {email}"}), 200

    except Exception as e:
        logging.error("Error processing request: %s", str(e))
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))