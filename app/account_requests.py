"""Persistent account requests. No user account is created before admin approval."""
import sqlite3
import hashlib
import hmac
import time
import secrets
import re
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template_string, request, url_for, make_response, render_template
from .db import get_db
from .auth import _registration_enabled
from .password_security import hash_password
from .access_control import FEATURES, audit, is_admin

bp = Blueprint("account_requests", __name__, url_prefix="/auth/account-requests")

FORM = """{% extends 'layout.html' %}{% block title %}Konto beantragen{% endblock %}{% block body %}<main class="auth-shell"><section class="auth-card"><h1>Konto beantragen</h1><form method="post"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><label for="username">Benutzername</label><input class="form-control mb-3" id="username" name="username" maxlength="64" required><label for="email">E-Mail</label><input class="form-control mb-3" id="email" name="email" type="email" maxlength="254" required><label for="password">Passwort (mindestens 12 Zeichen)</label><input class="form-control mb-3" id="password" name="password" type="password" minlength="12" maxlength="128" required><button class="btn btn-primary w-100">Antrag senden</button></form></section></main>{% endblock %}"""



def _admin_required():
    if not is_admin():
        abort(403)


def _valid_email(email: str) -> bool:
    if not email or len(email) > 254 or email.count('@') != 1 or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in email):
        return False
    local, domain = email.split('@', 1)
    return bool(local and '.' in domain and all(part for part in domain.split('.')))


def _valid_username(username):
    return 3 <= len(username) <= 64 and all(
        ch.isascii() and (ch.isalnum() or ch in '_.-') for ch in username
    )


def _parent_user(db):
    value = request.form.get("parent_user_id", "").strip()
    if not value:
        return None
    if not value.isascii() or not value.isdigit() or len(value) > 12:
        abort(400)
    parent = db.execute("SELECT id FROM user WHERE id=? AND is_disabled=0", (int(value),)).fetchone()
    if parent is None:
        abort(404)
    return parent["id"]


def _db():
    return get_db()

def _check_request_rate(db, *, namespace="requests", limit=None) -> None:
    """Bound expensive password hashing and spam per client without storing raw IPs."""
    client_ip = request.remote_addr or "unknown"
    if namespace != "requests":
        client_ip = namespace + ":" + client_ip
    if limit is None:
        limit = int(db.execute("SELECT requests_per_hour FROM registration_settings WHERE id=1").fetchone()[0])
    key = hmac.new(
        str(current_app.config["SECRET_KEY"]).encode("utf-8"),
        client_ip.encode("utf-8", "replace"),
        hashlib.sha256,
    ).hexdigest()
    now = int(time.time())
    # Expire stale client keys so an internet-facing instance does not grow
    # the throttle table indefinitely. The current window is preserved.
    db.execute("DELETE FROM account_request_throttle WHERE window_started < ?", (now - 7200,))
    db.execute(
        """INSERT INTO account_request_throttle (client_key, window_started, attempts)
           VALUES (?, ?, 1)
           ON CONFLICT(client_key) DO UPDATE SET
             attempts = CASE WHEN ? - window_started >= 3600 THEN 1 ELSE attempts + 1 END,
             window_started = CASE WHEN ? - window_started >= 3600 THEN ? ELSE window_started END""",
        (key, now, now, now, now),
    )
    row = db.execute(
        "SELECT attempts, window_started FROM account_request_throttle WHERE client_key = ?", (key,)
    ).fetchone()
    db.commit()
    if int(row["attempts"]) > limit:
        abort(429, description="Zu viele Kontoanträge. Bitte später erneut versuchen.")


@bp.route('/new', methods=['GET','POST'])
def new():
    if not _registration_enabled():
        abort(403)
    if request.method == 'POST':
        # Apply the throttle before parsing potentially attacker-controlled fields.
        # Even malformed requests must count towards the public endpoint quota.
        db = _db()
        _check_request_rate(db)
        username = request.form.get('username','').strip()
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        if not _valid_username(username) or not _valid_email(email) or not 12<=len(password)<=128:
            abort(400)
        # Hash regardless of account existence to reduce username-enumeration
        # timing differences. Rate limiting above bounds this expensive step.
        password_digest = hash_password(password)
        # Serialize account admission with the registration setting and admin approval.
        db.execute("BEGIN IMMEDIATE")
        try:
            enabled = db.execute("SELECT enabled FROM registration_settings WHERE id=1").fetchone()
            if enabled is None or not bool(enabled[0]):
                abort(403)
            if (db.execute('SELECT 1 FROM user WHERE username = ? COLLATE NOCASE',(username,)).fetchone() is None
                    and db.execute("SELECT 1 FROM account_request WHERE username = ? COLLATE NOCASE AND status='pending'", (username,)).fetchone() is None):
                db.execute('INSERT OR IGNORE INTO account_request(username,email,password_hash) VALUES (?,?,?)',
                           (username,email,password_digest))
            db.commit()
        finally:
            if db.in_transaction:
                db.rollback()
        flash('Falls noch kein Antrag vorliegt, wurde er zur Prüfung eingereicht.')
        return redirect(url_for('auth.login'))
    return render_template_string(FORM)

@bp.get('/admin')
def admin():
    _admin_required()
    items = _db().execute("SELECT id,username,email FROM account_request WHERE status='pending' ORDER BY id").fetchall()
    users = _db().execute("SELECT id,username FROM user WHERE is_disabled=0 ORDER BY username").fetchall()
    invitations = _db().execute("SELECT u.id,u.username,u.email,i.expires_at FROM account_invitation i JOIN user u ON u.id=i.user_id WHERE i.used_at IS NULL AND u.is_disabled=1 ORDER BY u.username").fetchall()
    settings = _db().execute("SELECT * FROM registration_settings WHERE id=1").fetchone()
    return render_template("admin/account_requests.html", items=items, account_users=users,
                           invitations=invitations, settings=settings, now=int(time.time()))

@bp.post('/admin/<int:request_id>')
def decide(request_id):
    _admin_required()
    if request.form.get('decision') not in ('approve','reject'):
        abort(400)
    db = _db()
    db.execute('BEGIN IMMEDIATE')
    try:
        item = db.execute("SELECT * FROM account_request WHERE id=? AND status='pending'",(request_id,)).fetchone()
        if item is None:
            abort(404)
        if request.form['decision']=='approve':
            if db.execute('SELECT 1 FROM user WHERE username=? COLLATE NOCASE', (item['username'],)).fetchone():
                abort(409)
            admin_role = request.form.get('is_admin') == '1'
            parent_user = _parent_user(db)
            created = db.execute('INSERT INTO user(username,password,email,is_admin,parent_user_id,created_at,updated_at) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)',(item['username'],item['password_hash'],item['email'],int(admin_role),parent_user))
            if not admin_role:
                db.executemany('INSERT INTO user_permission(user_id,feature,enabled,updated_at) VALUES (?,?,0,CURRENT_TIMESTAMP)',[(created.lastrowid, feature) for feature in FEATURES])
        db.execute("UPDATE account_request SET status=?,password_hash='',decided_at=CURRENT_TIMESTAMP WHERE id=?",(('approved' if request.form['decision']=='approve' else 'rejected'),request_id))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        abort(409)
    finally:
        if db.in_transaction:
            db.rollback()
    audit('account_request_decided','account_request',str(request_id),
          detail={'decision': request.form['decision'], 'user_id': created.lastrowid if request.form['decision']=='approve' else None,
                  'administrator': admin_role if request.form['decision']=='approve' else False})
    return redirect(url_for('account_requests.admin'))

@bp.post('/admin/registration')
def registration_toggle():
    _admin_required()
    db = _db()
    previous = db.execute("SELECT * FROM registration_settings WHERE id=1").fetchone()
    try:
        minutes = int(request.form.get("invitation_minutes", previous["invitation_minutes"]))
        quota = int(request.form.get("requests_per_hour", previous["requests_per_hour"]))
    except (ValueError, TypeError):
        abort(400)
    if not 1 <= minutes <= 10080 or not 1 <= quota <= 100:
        abort(400)
    db.execute('UPDATE registration_settings SET enabled=?,invitation_minutes=?,requests_per_hour=? WHERE id=1',
               (int(request.form.get('enabled')=='1'), minutes, quota))
    db.commit()
    audit('registration_setting_updated','settings','registration',detail={'enabled':request.form.get('enabled')=='1', 'invitation_minutes':minutes,'requests_per_hour':quota})
    return redirect(url_for('account_requests.admin'))


@bp.route('/admin/invite', methods=['GET', 'POST'])
def invite():
    """Admin-created accounts remain disabled until a one-time secret is redeemed."""
    _admin_required()
    if request.method == 'GET':
        users = _db().execute("SELECT id,username FROM user WHERE is_disabled=0 ORDER BY username").fetchall()
        from .mail_routes import _store as mail_store
        accounts = mail_store().accounts(g.user["username"])
        return render_template("admin/account_invite.html", account_users=users, mail_accounts=accounts)

    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip().lower()
    if not _valid_username(username) or not _valid_email(email):
        abort(400)
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    db = _db()
    db.execute('BEGIN IMMEDIATE')
    try:
        parent_user = _parent_user(db)
        if db.execute('SELECT 1 FROM user WHERE username=? COLLATE NOCASE', (username,)).fetchone():
            abort(409)
        if db.execute("SELECT 1 FROM account_request WHERE username=? COLLATE NOCASE AND status='pending'", (username,)).fetchone():
            abort(409)
        cursor = db.execute(
            """INSERT INTO user(username,password,email,is_admin,is_disabled,parent_user_id,created_at,updated_at)
               VALUES (?,?,?,?,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (username, hash_password(secrets.token_urlsafe(40)), email, int(request.form.get("is_admin") == "1"), parent_user),
        )
        if request.form.get('is_admin') != '1':
            db.executemany('INSERT INTO user_permission(user_id,feature,enabled,updated_at) VALUES (?,?,0,CURRENT_TIMESTAMP)',[(cursor.lastrowid, feature) for feature in FEATURES])
        db.execute(
            'INSERT INTO account_invitation(user_id, token_hash, expires_at) VALUES (?,?,?)',
            (cursor.lastrowid, digest, int(time.time()) + 60 * int(db.execute('SELECT invitation_minutes FROM registration_settings WHERE id=1').fetchone()[0])),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        abort(409)
    finally:
        if db.in_transaction:
            db.rollback()
    audit('account_invitation_created', 'user', str(cursor.lastrowid), detail={'parent_user_id':parent_user})
    return _invitation_response(token, cursor.lastrowid, email)


def _invitation_response(token, user_id, email):
    # Fragments never reach HTTP access logs or Referer headers.
    link = url_for('account_requests.accept_invite', _external=True) + "#token=" + token
    mail_status = ""
    if request.form.get("send_email") == "1":
        from .mail_client import SmtpSubmission
        from .mail_routes import _store as mail_store
        from .mail_webclient import MailAccountPolicy
        store = mail_store()
        try:
            account_id = request.form.get("mail_account_id", "")
            MailAccountPolicy(store).require_writable(g.user["username"], account_id)
            account = store.smtp_account(g.user["username"], account_id)
            SmtpSubmission(store).send(g.user["username"], account, email,
                                       "Einladung zu SimpleOffice4Me",
                                       "Bitte öffne die Einladung und lege dein Passwort fest:\n" + link)
            mail_status = "Einladung versandt."
            audit("account_invitation_email", "user", str(user_id))
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            current_app.logger.warning("Account invitation email failed: %s", type(exc).__name__)
            audit("account_invitation_email", "user", str(user_id), outcome="failure",
                  detail={"error_type": type(exc).__name__})
            mail_status = "E-Mail nicht bestätigt. Nicht automatisch erneut senden; Zustellung prüfen oder Link sicher übermitteln."
    return render_template("admin/account_invitation_created.html", link=link, mail_status=mail_status)


@bp.route('/invite', methods=['GET', 'POST'])
def accept_invite():
    """Atomic one-time token redemption, including concurrent request protection."""
    if request.method == 'GET':
        return render_template("auth/account_invite_accept.html")
    token = request.form.get("invitation_token", "")
    if len(token) > 128 or not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', token):
        abort(404)
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    db = _db()
    _check_request_rate(db, namespace="invite", limit=30)
    # Reject invalid, expired or already used tokens before expensive password
    # hashing. The authoritative check remains inside BEGIN IMMEDIATE below.
    existing = db.execute(
        """SELECT 1 FROM account_invitation AS i JOIN user AS u ON u.id=i.user_id
           WHERE i.token_hash=? AND i.expires_at>? AND i.used_at IS NULL AND u.is_disabled=1""",
        (digest, int(time.time())),
    ).fetchone()
    if existing is None:
        abort(404)
    password = request.form.get('password', '')
    if not 12 <= len(password) <= 128:
        abort(400)
    password_digest = hash_password(password)
    db.execute('BEGIN IMMEDIATE')
    try:
        record = db.execute(
            """SELECT i.user_id FROM account_invitation AS i JOIN user AS u ON u.id=i.user_id
               WHERE i.token_hash=? AND i.expires_at>? AND i.used_at IS NULL AND u.is_disabled=1""",
            (digest, int(time.time())),
        ).fetchone()
        if record is None:
            abort(404)
        db.execute(
            'UPDATE user SET password=?,is_disabled=0,auth_version=auth_version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (password_digest, record['user_id']),
        )
        db.execute(
            'UPDATE account_invitation SET used_at=? WHERE token_hash=?',
            (int(time.time()), digest),
        )
        db.commit()
    finally:
        if db.in_transaction:
            db.rollback()
    audit('account_invitation_accepted', 'user', str(record['user_id']))
    return redirect(url_for('auth.login'))


@bp.post('/admin/invitations/<int:user_id>')
def manage_invitation(user_id):
    _admin_required()
    action = request.form.get("action", "")
    if action not in {"reissue", "revoke"}:
        abort(400)
    db = _db()
    db.execute("BEGIN IMMEDIATE")
    try:
        target = db.execute("SELECT u.email FROM account_invitation i JOIN user u ON u.id=i.user_id WHERE i.user_id=? AND i.used_at IS NULL AND u.is_disabled=1", (user_id,)).fetchone()
        if target is None:
            abort(404)
        if action == "revoke":
            db.execute("DELETE FROM account_invitation WHERE user_id=?", (user_id,))
        else:
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode()).hexdigest()
            minutes = db.execute("SELECT invitation_minutes FROM registration_settings WHERE id=1").fetchone()[0]
            db.execute("UPDATE account_invitation SET token_hash=?,expires_at=? WHERE user_id=?", (digest, int(time.time()) + 60*minutes, user_id))
        db.commit()
    finally:
        if db.in_transaction:
            db.rollback()
    audit("account_invitation_" + action, "user", str(user_id))
    if action == "reissue":
        return _invitation_response(token, user_id, target["email"])
    flash("Einladung widerrufen. Das Konto bleibt gesperrt.")
    return redirect(url_for("account_requests.admin"))


@bp.after_request
def protect_account_responses(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.app_context_processor
def account_request_notice():
    if not is_admin():
        return {"pending_account_requests": 0}
    count = get_db().execute("SELECT COUNT(*) FROM account_request WHERE status='pending'").fetchone()[0]
    return {"pending_account_requests": count}
