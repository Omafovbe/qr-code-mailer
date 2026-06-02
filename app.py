import logging
import os
import tempfile
import random
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from click import echo
import psycopg2
from psycopg2.extras import RealDictCursor
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
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
COOKIE_NAME = os.getenv('ADMIN_COOKIE_NAME', 'admin_session')
COOKIE_MAX_AGE = int(os.getenv('ADMIN_COOKIE_MAX_AGE', 28800))
FORCE_SECURE_COOKIE = os.getenv('FORCE_SECURE_COOKIE', 'auto')

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
DATABASE_URL = os.getenv("DATABASE_URL")


def _serialize_row(row):
    if row is None:
        return None
    data = dict(row)
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return data


def _serialize_rows(rows):
    return [_serialize_row(row) for row in rows]


def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is required")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)



def nanoid(size=12):
    chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
    return ''.join(random.choice(chars) for _ in range(size))

def generate_html_body(fullName):
    htmlBody = f"""
        <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; border: 1px solid #eee; padding: 20px; border-radius: 10px;">
          <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px;">Registration Confirmed</h2>
          
          <p>Dear <strong>{fullName}</strong>,</p>
          
          <p>Thank you very much for Registering for <strong>RESEARCH CAPACITY BUILDING SERIES</strong>.

Thanks again and be punctual to avoid attendant delays.</p>
          
          <div style="background-color: #f9f9f9; border-left: 5px solid #3498db; padding: 15px; margin: 20px 0;">
            <p style="margin: 0;"><strong>Important:</strong> Please you are required to show your QR code on any device for scanning as attendance at the entrance.</p>
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

def init_db():
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS contacts (
                    id SERIAL PRIMARY KEY,
                    unique_id TEXT UNIQUE NOT NULL,
                    fullname TEXT NOT NULL,
                    phone TEXT,
                    email TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS rcbs (
                    id SERIAL PRIMARY KEY,
                    unique_id TEXT UNIQUE NOT NULL,
                    surname TEXT NOT NULL,
                    first_name TEXT NOT NULL,
                    other_name TEXT,
                    fullname TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone TEXT,
                    department TEXT,
                    faculty TEXT,
                    research_focus TEXT,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS scans (
                    id SERIAL PRIMARY KEY,
                    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    scanned_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    scan_count INTEGER NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_tokens (
                    id SERIAL PRIMARY KEY,
                    token_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    revoked BOOLEAN NOT NULL DEFAULT FALSE,
                    revoked_at TIMESTAMP WITHOUT TIME ZONE
                )
                """
            )
    conn.close()


def save_contact(unique_id, fullname, phone, email):
    """Save or update contact in contacts table, return contact_id."""
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO contacts (unique_id, fullname, phone, email) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (unique_id) DO UPDATE SET fullname = EXCLUDED.fullname, phone = EXCLUDED.phone, email = EXCLUDED.email",
                    (unique_id, fullname, phone, email),
                )
                cur.execute(
                    "SELECT id FROM contacts WHERE unique_id = %s",
                    (unique_id,),
                )
                contact = cur.fetchone()
                return contact["id"] if contact else None
    finally:
        conn.close()


def save_rcbs(unique_id, surname, first_name, other_name, fullname, phone, email, department, faculty, research_focus):
    """Save or update form data in rcbs table, return rcbs_id."""
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO rcbs (unique_id, surname, first_name, other_name, fullname, email, phone, department, faculty, research_focus) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (unique_id) DO UPDATE SET "
                    "surname = EXCLUDED.surname, first_name = EXCLUDED.first_name, other_name = EXCLUDED.other_name, fullname = EXCLUDED.fullname, "
                    "email = EXCLUDED.email, phone = EXCLUDED.phone, department = EXCLUDED.department, faculty = EXCLUDED.faculty, research_focus = EXCLUDED.research_focus",
                    (unique_id, surname, first_name, other_name, fullname, email, phone, department, faculty, research_focus),
                )
                cur.execute(
                    "SELECT id FROM rcbs WHERE unique_id = %s",
                    (unique_id,),
                )
                rcbs = cur.fetchone()
                return rcbs["id"] if rcbs else None
    finally:
        conn.close()


def record_scan(contact_id):
    """Record a scan for a contact, increment scan_count."""
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(scan_count) as max_count FROM scans WHERE contact_id = %s",
                    (contact_id,),
                )
                row = cur.fetchone()
                last_count = row["max_count"] or 0
                next_count = last_count + 1
                cur.execute(
                    "INSERT INTO scans (contact_id, scanned_at, scan_count) VALUES (%s, %s, %s)",
                    (contact_id, datetime.utcnow(), next_count),
                )
                return next_count
    finally:
        conn.close()


def hash_password(password):
    return generate_password_hash(password)


def verify_password(stored_hash, password):
    return check_password_hash(stored_hash, password)


def create_user(name, email, password, role='member'):
    role = role if role in {'admin', 'member'} else 'member'
    password_hash = hash_password(password)
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (name, email, role, password_hash, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (name, email, role, password_hash, datetime.utcnow()),
                )
        return get_user_by_email(email)
    finally:
        conn.close()


def get_user_by_email(email):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, role, password_hash, created_at FROM users WHERE email = %s",
                (email,),
            )
            return _serialize_row(cur.fetchone())
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, role, created_at FROM users WHERE id = %s",
                (user_id,),
            )
            return _serialize_row(cur.fetchone())
    finally:
        conn.close()


def fetch_all_users():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email, role, created_at FROM users ORDER BY id DESC")
            return _serialize_rows(cur.fetchall())
    finally:
        conn.close()


def is_admin_user(user):
    return user and user.get('role') == 'admin'


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    # Update session role if it's missing (fallback)
    if user and 'user_role' not in session:
        session['user_role'] = user.get('role')
        session.modified = True
    return user


def create_admin_token(user_id, duration_seconds=COOKIE_MAX_AGE):
    token_id = secrets.token_urlsafe(24)
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=duration_seconds)
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO admin_tokens (token_id, user_id, created_at, expires_at, revoked) VALUES (%s, %s, %s, %s, FALSE)",
                    (token_id, user_id, now, expires_at),
                )
        return token_id
    finally:
        conn.close()


def get_admin_token(token_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT token_id, user_id, created_at, expires_at, revoked FROM admin_tokens WHERE token_id = %s",
                (token_id,),
            )
            return _serialize_row(cur.fetchone())
    finally:
        conn.close()


def revoke_admin_token(token_id):
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE admin_tokens SET revoked = TRUE, revoked_at = %s WHERE token_id = %s",
                    (datetime.utcnow(), token_id),
                )
    finally:
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
        session['user_role'] = user['role']  # Store role in session as fallback
        session.permanent = True

        # Prepare response and set secure admin cookie for quick camera-phone access
        if request.is_json:
            response = make_response(jsonify({"success": True, "message": "Logged in"}), 200)
        else:
            response = make_response(redirect(url_for('index')))

        # Set admin cookie AFTER session is created
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
        if request.is_json:
            return jsonify({"error": "role must be 'admin' or 'member'"}), 400
        return redirect(url_for('admin_users', error="Role must be admin or member"))

    if not name or not email or not password:
        if request.is_json:
            return jsonify({"error": "name, email, and password are required"}), 400
        return redirect(url_for('admin_users', error="Name, email, and password are required"))

    try:
        user = create_user(name, email, password, role)
        if request.is_json:
            return jsonify({"success": True, "user": user}), 201
        return redirect(url_for('admin_users', message=f"Created user {user['email']}"))
    except psycopg2.IntegrityError:
        if request.is_json:
            return jsonify({"error": "A user with that email already exists"}), 409
        return redirect(url_for('admin_users', error="A user with that email already exists"))


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


@app.route('/admin/users', methods=['GET'])
def admin_users():
    admin = ensure_admin_user()
    if not admin:
        return redirect(url_for('login'))

    users = fetch_all_users()
    status_message = request.args.get('message', '')
    error_message = request.args.get('error', '')
    return render_template('admin_users.html', user=admin, users=users, status_message=status_message, error_message=error_message)


@app.route('/admin/users/<int:user_id>/role', methods=['POST'])
def update_user_role(user_id):
    admin = ensure_admin_user()
    if not admin:
        return redirect(url_for('login'))

    role = (request.form.get('role') or '').strip().lower()
    if role not in {'admin', 'member'}:
        return redirect(url_for('admin_users', error="Invalid role selected"))

    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET role = %s WHERE id = %s",
                    (role, user_id),
                )
        return redirect(url_for('admin_users', message=f"Updated user role to {role}"))
    finally:
        conn.close()


def ensure_admin_user():
    # First check session role (fast path for repeated requests)
    if session.get('user_role') == 'admin':
        user = current_user()
        if user:
            return user
    
    # Fallback to full check
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
            "(t.token_id LIKE %s OR u.email LIKE %s OR u.name LIKE %s OR CAST(t.id AS TEXT) = %s)",
        )
        query_params.extend([like, like, like, q])

    where_clause = " WHERE " + " AND ".join(query_filters) if query_filters else ""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) as count FROM admin_tokens t JOIN users u ON t.user_id = u.id{where_clause}",
                tuple(query_params),
            )
            total = cur.fetchone()["count"]
            offset = (page - 1) * per_page
            cur.execute(
                f"SELECT t.token_id, t.user_id, u.email, u.name, t.created_at, t.expires_at, t.revoked, t.revoked_at "
                f"FROM admin_tokens t JOIN users u ON t.user_id = u.id{where_clause} "
                f"ORDER BY t.id DESC LIMIT %s OFFSET %s",
                tuple(query_params + [per_page, offset]),
            )
            rows = cur.fetchall()
            return _serialize_rows(rows), total
    finally:
        conn.close()


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
            "(c.fullname LIKE %s OR c.email LIKE %s OR c.unique_id LIKE %s OR CAST(s.id AS TEXT) = %s)",
        )
        query_params.extend([like, like, like, q])

    where_clause = " WHERE " + " AND ".join(query_filters) if query_filters else ""

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) as count FROM scans s JOIN contacts c ON s.contact_id = c.id{where_clause}",
                tuple(query_params),
            )
            total = cur.fetchone()["count"]
            cur.execute(
                f"SELECT s.id, c.unique_id, c.fullname, c.email, s.scanned_at, s.scan_count "
                f"FROM scans s "
                f"JOIN contacts c ON s.contact_id = c.id{where_clause} "
                f"ORDER BY s.id DESC LIMIT %s OFFSET %s",
                tuple(query_params + [per_page, offset]),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    scans = _serialize_rows(rows)
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
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, unique_id, fullname, email, phone FROM contacts WHERE unique_id = %s",
                (uid,),
            )
            contact = _serialize_row(cur.fetchone())
    finally:
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
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400

    fields = data.get("data", {}).get("fields", [])
    logging.info("Extracted fields: %s", fields)

    # Convert fields into a dictionary {label: value}
    field_map = {f.get("label"): f.get("value") for f in fields if f.get("label")}

    surname = (field_map.get('surname') or '').strip()
    first_name = (field_map.get('firstname') or '').strip()
    other_name = (field_map.get('othername') or '').strip()
    email = (field_map.get('email') or '').strip()
    phone = (field_map.get('phone') or '').strip()
    u_id = nanoid()
    department = (field_map.get('Department') or '').strip()
    faculty = (field_map.get('Faculty') or '').strip()
    research_focus = (field_map.get('researchFocus') or field_map.get('reseachFocus') or '').strip()

    fullname = ' '.join(part for part in [surname, first_name, other_name] if part).strip()
    if not fullname:
        fullname = 'Test User'

    htmlBody = generate_html_body(fullname)

    if not u_id or not fullname or not email or not phone:
        return jsonify({"error": "Missing required fields: surname, firstName, email, phone"}), 400

    contact = Contact(unique_id=u_id, fullname=fullname, email=email, phone=phone)
    contact_id = save_contact(u_id, fullname, phone, email)
    logging.info("Saved contact with ID: %s", contact_id)
    rcbs_id = save_rcbs(
        unique_id=u_id,
        surname=surname,
        first_name=first_name,
        other_name=other_name,
        fullname=fullname,
        phone=phone,
        email=email,
        department=department,
        faculty=faculty,
        research_focus=research_focus,
    )

    try:
        # Use temp directory for QR codes
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            qr_path = make_qr(contact, output_dir)

            subject = data.get('subject', 'Research Series Registration Confirmation')
            body_template = data.get('body', 'Hello {fullname},\n\nPlease find your QR code attached.\n\nCheers,\nTeam')
            html_body_template = htmlBody

            message = build_message(contact, qr_path, FROM_EMAIL, subject, body_template, html_body_template)
            send_email(message, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, use_tls=(SMTP_PORT == 587))

        return jsonify({"success": True, "message": f"QR code sent to {email}", "rcbs_id": rcbs_id}), 200

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
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, fullname, email FROM contacts WHERE unique_id = %s",
                (uid,),
            )
            contact = _serialize_row(cur.fetchone())
    finally:
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
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT scan_count FROM scans WHERE contact_id = (SELECT id FROM contacts WHERE unique_id = %s) ORDER BY id DESC LIMIT 1",
                (uid,),
            )
            scan_row = cur.fetchone()
    finally:
        conn.close()

    scan_count = scan_row["scan_count"] if scan_row else 0

    return render_template('thanks.html', fullname=fullname, scan_count=scan_count, email=email)


@app.route('/scans', methods=['GET'])
def scans():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.id, c.unique_id, c.fullname, c.email, s.scanned_at, s.scan_count 
                   FROM scans s 
                   JOIN contacts c ON s.contact_id = c.id 
                   ORDER BY s.id DESC LIMIT 100"""
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify(_serialize_rows(rows))


@app.route('/scanner', methods=['GET'])
def scanner():
    return render_template('scanner.html')


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))