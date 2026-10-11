"""Persistent account requests. No user account is created before admin approval."""
import re
import sqlite3
from flask import Blueprint, abort, flash, g, redirect, render_template_string, request, url_for
from .db import get_db
from .password_security import hash_password
from .access_control import audit

bp = Blueprint("account_requests", __name__, url_prefix="/auth/account-requests")

FORM = """{% extends 'layout.html' %}{% block title %}Konto beantragen{% endblock %}{% block body %}<main class="auth-shell"><section class="auth-card"><h1>Konto beantragen</h1><form method="post"><label for="username">Benutzername</label><input class="form-control mb-3" id="username" name="username" maxlength="64" required><label for="email">E-Mail</label><input class="form-control mb-3" id="email" name="email" type="email" maxlength="254" required><label for="password">Passwort (mindestens 12 Zeichen)</label><input class="form-control mb-3" id="password" name="password" type="password" minlength="12" maxlength="128" required><button class="btn btn-primary w-100">Antrag senden</button></form></section></main>{% endblock %}"""
ADMIN = """{% extends 'layout.html' %}{% block title %}Kontoanträge{% endblock %}{% block body %}<main class="container py-4"><h1>Kontoanträge</h1>{% for item in items %}<section class="card p-3 mb-3"><strong>{{ item['username'] }}</strong><span>{{ item['email'] }}</span><form method="post" action="{{ url_for('account_requests.decide', request_id=item['id']) }}"><button class="btn btn-primary" name="decision" value="approve">Freigeben</button><button class="btn btn-outline-danger" name="decision" value="reject">Ablehnen</button></form></section>{% else %}<p>Keine offenen Anträge.</p>{% endfor %}</main>{% endblock %}"""

def _db():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS account_request (id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE, email TEXT NOT NULL, password_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, decided_at TEXT)""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS account_request_pending_username ON account_request(username) WHERE status = 'pending'")
    db.commit()
    return db

@bp.route('/new', methods=['GET','POST'])
def new():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', username) or len(email)>254 or not re.fullmatch(r'[^@\\s]+@[^@\\s]+\\.[^@\\s]+', email) or not 12<=len(password)<=128:
            abort(400)
        db = _db()
        if db.execute('SELECT 1 FROM user WHERE username = ?',(username,)).fetchone() is None:
            try:
                db.execute('INSERT INTO account_request(username,email,password_hash) VALUES (?,?,?)',(username,email,hash_password(password)))
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
        flash('Falls noch kein Antrag vorliegt, wurde er zur Prüfung eingereicht.')
        return redirect(url_for('auth.login'))
    return render_template_string(FORM)

@bp.get('/admin')
def admin():
    if g.user is None or not g.user['is_admin']:
        abort(403)
    items = _db().execute("SELECT id,username,email FROM account_request WHERE status='pending' ORDER BY id").fetchall()
    return render_template_string(ADMIN,items=items)

@bp.post('/admin/<int:request_id>')
def decide(request_id):
    if g.user is None or not g.user['is_admin']:
        abort(403)
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
