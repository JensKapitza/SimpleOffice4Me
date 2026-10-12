"""Persistent account requests. No user account is created before admin approval."""
import sqlite3
import hashlib
import hmac
import time
import secrets
import re
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template_string, request, url_for
from .db import get_db
from .auth import _registration_enabled
from .password_security import hash_password
from .access_control import audit

bp = Blueprint("account_requests", __name__, url_prefix="/auth/account-requests")

FORM = """{% extends 'layout.html' %}{% block title %}Konto beantragen{% endblock %}{% block body %}<main class="auth-shell"><section class="auth-card"><h1>Konto beantragen</h1><form method="post"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><label for="username">Benutzername</label><input class="form-control mb-3" id="username" name="username" maxlength="64" required><label for="email">E-Mail</label><input class="form-control mb-3" id="email" name="email" type="email" maxlength="254" required><label for="password">Passwort (mindestens 12 Zeichen)</label><input class="form-control mb-3" id="password" name="password" type="password" minlength="12" maxlength="128" required><button class="btn btn-primary w-100">Antrag senden</button></form></section></main>{% endblock %}"""
ADMIN = """{% extends 'layout.html' %}{% block title %}Kontoanträge{% endblock %}{% block body %}<main class="container py-4"><h1>Kontoanträge</h1><p><a class="btn btn-primary" href="{{ url_for('account_requests.invite') }}">+ Benutzer</a></p><form method="post" action="{{ url_for('account_requests.registration_toggle') }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><label><input type="checkbox" name="enabled" value="1" {% if registration_enabled %}checked{% endif %}> Registrierung zulassen</label><button class="btn btn-primary">Speichern</button></form>{% for item in items %}<section class="card p-3 mb-3"><strong>{{ item['username'] }}</strong><span>{{ item['email'] }}</span><form method="post" action="{{ url_for('account_requests.decide', request_id=item['id']) }}"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><button class="btn btn-primary" name="decision" value="approve">Freigeben</button><button class="btn btn-outline-danger" name="decision" value="reject">Ablehnen</button></form></section>{% else %}<p>Keine offenen Anträge.</p>{% endfor %}</main>{% endblock %}"""

INVITE_FORM = """{% extends 'layout.html' %}{% block title %}Passwort festlegen{% endblock %}{% block body %}<main class="auth-shell"><section class="auth-card"><h1>Passwort festlegen</h1><form method="post"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><label for="password">Neues Passwort (mindestens 12 Zeichen)</label><input class="form-control" id="password" name="password" type="password" minlength="12" maxlength="128" autocomplete="new-password" required><button class="btn btn-primary mt-3">Konto aktivieren</button></form></section></main>{% endblock %}"""

def _admin_required():
    if g.user is None or not g.user['is_admin'] or g.user['is_disabled']:
        abort(403)


def _valid_email(email: str) -> bool:
    if not email or len(email) > 254 or email.count('@') != 1 or any(ch.isspace() for ch in email):
        return False
    local, domain = email.split('@', 1)
    return bool(local and '.' in domain and all(part for part in domain.split('.')))


def _valid_username(username):
    return 3 <= len(username) <= 64 and all(
        ch.isascii() and (ch.isalnum() or ch in '_.-') for ch in username
    )


def _db():
    return get_db()

def _check_request_rate(db) -> None:
    """Bound expensive password hashing and spam per client without storing raw IPs."""
    client_ip = request.remote_addr or "unknown"
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
    if int(row["attempts"]) > 5:
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
            if db.execute('SELECT 1 FROM user WHERE username = ?',(username,)).fetchone() is None:
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
    return render_template_string(ADMIN,items=items,registration_enabled=_registration_enabled())

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
            db.execute('INSERT INTO user(username,password,email,is_admin,created_at,updated_at) VALUES (?,?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)',(item['username'],item['password_hash'],item['email']))
        db.execute("UPDATE account_request SET status=?,decided_at=CURRENT_TIMESTAMP WHERE id=?",(('approved' if request.form['decision']=='approve' else 'rejected'),request_id))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        abort(409)
    finally:
        if db.in_transaction:
            db.rollback()
    audit('account_request','user',outcome=request.form['decision'])
    return redirect(url_for('account_requests.admin'))

@bp.post('/admin/registration')
def registration_toggle():
    _admin_required()
    db = _db()
    db.execute('UPDATE registration_settings SET enabled=? WHERE id=1',(int(request.form.get('enabled')=='1'),))
    db.commit()
    audit('registration_setting','user',outcome='updated')
    return redirect(url_for('account_requests.admin'))


@bp.route('/admin/invite', methods=['GET', 'POST'])
def invite():
    """Admin-created accounts remain disabled until a one-time secret is redeemed."""
    _admin_required()
    if request.method == 'GET':
        return render_template_string("""{% extends 'layout.html' %}{% block title %}Benutzer einladen{% endblock %}{% block body %}<main class="container py-4"><h1>+ Benutzer</h1><form method="post"><input type="hidden" name="_csrf_token" value="{{ csrf_token() }}"><label for="username">Benutzername</label><input id="username" name="username" class="form-control" maxlength="64" required><label for="email">E-Mail</label><input id="email" name="email" type="email" class="form-control" maxlength="254" required><button class="btn btn-primary mt-3">Einladung erstellen</button></form></main>{% endblock %}""")
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip().lower()
    if not _valid_username(username) or not _valid_email(email):
        abort(400)
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    db = _db()
    db.execute('BEGIN IMMEDIATE')
    try:
        cursor = db.execute(
            """INSERT INTO user(username,password,email,is_admin,is_disabled,created_at,updated_at)
               VALUES (?,?,?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (username, hash_password(secrets.token_urlsafe(40)), email),
        )
        db.execute(
            'INSERT INTO account_invitation(user_id, token_hash, expires_at) VALUES (?,?,?)',
            (cursor.lastrowid, digest, int(time.time()) + 86400),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        abort(409)
    finally:
        if db.in_transaction:
            db.rollback()
    audit('account_invitation', 'user', outcome='created')
    # Token appears only in the authenticated administrator's response.
    # It must be passed to the recipient over a trusted channel.
    link = url_for('account_requests.accept_invite', token=token, _external=True)
    return render_template_string("""{% extends 'layout.html' %}{% block title %}Einladung{% endblock %}{% block body %}<main class="container py-4"><h1>Einladung erstellt</h1><p>Dieser Link wird nur einmal angezeigt. Sicher an den Benutzer übermitteln:</p><code id="invitation-link">{{ link }}</code></main>{% endblock %}""", link=link)


@bp.route('/invite/<token>', methods=['GET', 'POST'])
def accept_invite(token):
    """Atomic one-time token redemption, including concurrent request protection."""
    if len(token) > 128 or not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', token):
        abort(404)
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    db = _db()
    if request.method == 'GET':
        valid = db.execute(
            """SELECT 1 FROM account_invitation AS i JOIN user AS u ON u.id=i.user_id
               WHERE i.token_hash=? AND i.expires_at>? AND i.used_at IS NULL AND u.is_disabled=1""",
            (digest, int(time.time())),
        ).fetchone()
        if valid is None:
            abort(404)
        return render_template_string(INVITE_FORM)
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
    audit('account_invitation', 'user', outcome='accepted')
    return redirect(url_for('auth.login'))
