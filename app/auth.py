import functools
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for, current_app
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .google_sync import sync_google_account

bp = Blueprint('auth', __name__, url_prefix='/auth')

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _google_config() -> dict[str, str] | None:
    client_id = current_app.config.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = current_app.config.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    generated_redirect_uri = url_for("auth.google_callback", _external=True)
    explicit_redirect_uri = current_app.config.get("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    allowed_redirect_uris = current_app.config.get("GOOGLE_OAUTH_REDIRECT_URIS", ())
    redirect_uri = explicit_redirect_uri or generated_redirect_uri
    if not explicit_redirect_uri and generated_redirect_uri not in allowed_redirect_uris:
        callback_uris = [value for value in allowed_redirect_uris if value.rstrip("/").endswith("/auth/google/callback")]
        if len(callback_uris) == 1:
            redirect_uri = callback_uris[0]
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }


def _login_user(user) -> None:
    session.clear()
    session['user_id'] = user['id']


def _google_username(db, email: str) -> str:
    base = re.sub(r"[^a-z0-9_.-]+", "-", email.split("@", 1)[0].casefold()).strip("-._") or "google-user"
    candidate = base
    number = 2
    while db.execute("SELECT 1 FROM user WHERE username = ?", (candidate,)).fetchone() is not None:
        candidate = f"{base}-{number}"
        number += 1
    return candidate


@bp.route('/register', methods=('GET', 'POST'))
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        error = None

        if not username:
            error = 'Benutzername fehlt.'
        elif not password:
            error = 'Passwort fehlt.'
        elif len(password) < 8:
            error = 'Das Passwort muss mindestens 8 Zeichen haben.'
        elif db.execute(
            'SELECT id FROM user WHERE username = ?', (username,)
        ).fetchone() is not None:
            error = 'Der Benutzername ist bereits vergeben.'

        if error is None:
            db.execute(
                'INSERT INTO user (username, password) VALUES (?, ?)',
                (username, generate_password_hash(password))
            )
            db.commit()
            return redirect(url_for('auth.login'))

        flash(error)

    return render_template('auth/register.html', google_enabled=_google_config() is not None)


@bp.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        error = None
        user = db.execute(
            'SELECT * FROM user WHERE username = ?', (username,)
        ).fetchone()

        if user is None:
            error = 'Benutzername oder Passwort ist falsch.'
        elif not check_password_hash(user['password'], password):
            error = 'Benutzername oder Passwort ist falsch.'

        if error is None:
            _login_user(user)
            return redirect(url_for('home'))

        flash(error)


    return render_template('auth/login.html', google_enabled=_google_config() is not None)


@bp.get('/google')
def google_login():
    config = _google_config()
    if config is None:
        flash('Google-Anmeldung ist noch nicht konfiguriert.')
        return redirect(url_for('auth.login'))
    state = secrets.token_urlsafe(32)
    session['google_oauth_state'] = state
    parameters = {
        'client_id': config['client_id'], 'redirect_uri': config['redirect_uri'], 'response_type': 'code',
        'scope': 'openid email profile https://www.googleapis.com/auth/contacts.readonly https://www.googleapis.com/auth/calendar.readonly',
        'state': state, 'prompt': 'select_account', 'access_type': 'offline', 'include_granted_scopes': 'true',
    }
    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(parameters)}")


@bp.get('/google/callback')
def google_callback():
    if request.args.get('error'):
        flash('Google-Anmeldung wurde abgebrochen.')
        return redirect(url_for('auth.login'))
    expected_state = session.pop('google_oauth_state', '')
    received_state = request.args.get('state', '')
    code = request.args.get('code', '')
    config = _google_config()
    if not config or not code or not expected_state or not secrets.compare_digest(expected_state, received_state):
        flash('Google-Anmeldung konnte nicht sicher geprüft werden.')
        return redirect(url_for('auth.login'))
    try:
        body = urlencode({
            'code': code, 'client_id': config['client_id'], 'client_secret': config['client_secret'],
            'redirect_uri': config['redirect_uri'], 'grant_type': 'authorization_code',
        }).encode('utf-8')
        token_request = Request(GOOGLE_TOKEN_URL, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with urlopen(token_request, timeout=15) as response:
            token = json.loads(response.read().decode('utf-8'))
        access_token = str(token.get('access_token', ''))
        if not access_token:
            raise ValueError('Google did not return an access token')
        profile_request = Request(GOOGLE_USERINFO_URL, headers={'Authorization': f'Bearer {access_token}'})
        with urlopen(profile_request, timeout=15) as response:
            profile = json.loads(response.read().decode('utf-8'))
        subject = str(profile.get('sub', '')).strip()
        email = str(profile.get('email', '')).strip().casefold()
        if not subject or not email or profile.get('email_verified') is not True:
            raise ValueError('Google account has no verified email address')
    except Exception:
        current_app.logger.exception('Google OAuth callback failed')
        flash('Google-Anmeldung ist fehlgeschlagen. Bitte erneut versuchen.')
        return redirect(url_for('auth.login'))

    db = get_db()
    identity = db.execute('SELECT user.* FROM oauth_identity JOIN user ON user.id = oauth_identity.user_id WHERE provider = ? AND subject = ?', ('google', subject)).fetchone()
    created = False
    if identity is None:
        # Never bind an OAuth identity to a local account merely because a
        # user selected the same text as a username. Explicit linking can be
        # added later from an authenticated account page.
        username = _google_username(db, email)
        db.execute('INSERT INTO user (username, password, display_name, email, avatar_url, profile_source, profile_updated_at) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)', (username, generate_password_hash(secrets.token_urlsafe(32)), str(profile.get('name', '')).strip(), email, str(profile.get('picture', '')).strip(), 'google'))
        identity = db.execute('SELECT * FROM user WHERE username = ?', (username,)).fetchone()
        created = True
        db.execute('INSERT INTO oauth_identity (provider, subject, user_id, email) VALUES (?, ?, ?, ?)', ('google', subject, identity['id'], email))
    db.execute('UPDATE user SET display_name = ?, email = ?, avatar_url = ?, profile_source = ?, profile_updated_at = CURRENT_TIMESTAMP WHERE id = ?', (str(profile.get('name', '')).strip(), email, str(profile.get('picture', '')).strip(), 'google', identity['id']))
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(token.get('expires_in', 0) or 0))).isoformat()
    previous = db.execute('SELECT refresh_token FROM oauth_token WHERE provider = ? AND user_id = ?', ('google', identity['id'])).fetchone()
    refresh_token = str(token.get('refresh_token', '')).strip() or (previous['refresh_token'] if previous else '')
    db.execute('INSERT INTO oauth_token (provider, user_id, access_token, refresh_token, expires_at, scopes, updated_at) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(provider, user_id) DO UPDATE SET access_token = excluded.access_token, refresh_token = excluded.refresh_token, expires_at = excluded.expires_at, scopes = excluded.scopes, updated_at = CURRENT_TIMESTAMP', ('google', identity['id'], access_token, refresh_token, expires_at, str(token.get('scope', ''))))
    db.commit()
    try:
        synced = sync_google_account(access_token, identity['username'], subject)
        flash(f"Google-Daten abgeglichen: {synced['contacts']} Kontakte, {synced['events']} Termine aus {synced['calendars']} Kalendern.")
    except Exception:
        current_app.logger.exception('Google data sync failed')
        flash('Google-Anmeldung erfolgreich; Kontakt- und Kalenderimport wird beim nächsten Google-Login erneut versucht.')
    _login_user(identity)
    flash('Konto mit Google erstellt und angemeldet.' if created else 'Mit Google angemeldet.')
    return redirect(url_for('home'))

@bp.before_app_request
def load_logged_in_user():
    user_id = session.get('user_id')

    if user_id is None:
        g.user = None
    else:
        g.user = get_db().execute(
            'SELECT * FROM user WHERE id = ?', (user_id,)
        ).fetchone()


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('auth.login'))

        return view(**kwargs)

    return wrapped_view


@bp.route('/profile', methods=('GET', 'POST'))
@login_required
def profile():
    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        if not display_name:
            flash('Anzeigename fehlt.')
        else:
            get_db().execute('UPDATE user SET display_name = ?, profile_source = ?, profile_updated_at = CURRENT_TIMESTAMP WHERE id = ?', (display_name, 'manual', g.user['id']))
            get_db().commit()
            flash('Benutzerprofil gespeichert.')
            return redirect(url_for('auth.profile'))
    return render_template('auth/profile.html')
