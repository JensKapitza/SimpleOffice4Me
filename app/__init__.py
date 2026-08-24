#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#export PYTHONIOENCODING=utf8
from pathlib import Path

import os
import json
import sys
import datetime
import locale
import secrets
import traceback

from .applogging import initlogging
from .secret_key import load_or_create_secret_key

from .bs4 import download_file, renderwithbs4

from flask import Flask, send_from_directory, \
    render_template_string, render_template, \
    request, session, redirect, abort, send_file, \
    g, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.exceptions import HTTPException
from flask.sessions import SecureCookieSessionInterface


MIB = 1024 * 1024
DEFAULT_UPLOAD_LIMIT_MIB = 512
MAX_UPLOAD_LIMIT_MIB = 4096
MAX_WEBDAV_QUOTA_MIB = 1024 * 1024
DEFAULT_WEBDAV_QUARANTINE_MIB = 1024
MAX_WEBDAV_QUARANTINE_MIB = 64 * 1024


def configured_upload_limit_bytes() -> int:
    """Return a bounded total request limit for file imports."""
    requested = os.environ.get(
        "SIMPLEOFFICE_MAX_UPLOAD_MIB",
        str(DEFAULT_UPLOAD_LIMIT_MIB),
    ).strip()
    try:
        limit_mib = int(requested)
    except ValueError:
        limit_mib = DEFAULT_UPLOAD_LIMIT_MIB
    return max(1, min(limit_mib, MAX_UPLOAD_LIMIT_MIB)) * MIB


def configured_webdav_quota_bytes() -> int:
    """Return the optional managed-tree WebDAV quota in bytes.

    Zero keeps the historic unlimited behavior.  Invalid or negative values
    fail safely to that backwards-compatible default instead of introducing
    a surprising storage outage during startup.
    """
    requested = os.environ.get("SIMPLEOFFICE_WEBDAV_QUOTA_MIB", "0").strip()
    try:
        limit_mib = int(requested)
    except ValueError:
        return 0
    if limit_mib <= 0:
        return 0
    return min(limit_mib, MAX_WEBDAV_QUOTA_MIB) * MIB


def configured_webdav_upload_scan() -> bool:
    """Return whether WebDAV PUT bodies require a clean ClamAV verdict."""
    return os.environ.get("SIMPLEOFFICE_WEBDAV_CLAMAV", "0").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def configured_webdav_quarantine_bytes() -> int:
    """Return the bounded private capacity for failed WebDAV upload scans."""
    requested = os.environ.get(
        "SIMPLEOFFICE_WEBDAV_QUARANTINE_MIB",
        str(DEFAULT_WEBDAV_QUARANTINE_MIB),
    ).strip()
    try:
        limit_mib = int(requested)
    except ValueError:
        limit_mib = DEFAULT_WEBDAV_QUARANTINE_MIB
    return max(1, min(limit_mib, MAX_WEBDAV_QUARANTINE_MIB)) * MIB


def google_oauth_web_config() -> dict[str, object]:
    """Return the ``web`` block from an optional Google OAuth JSON file."""
    credentials_file = os.environ.get("SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE", "").strip()
    if not credentials_file:
        return {}
    try:
        credentials = json.loads(Path(credentials_file).read_text(encoding="utf-8"))
        web = credentials["web"]
        if not isinstance(web, dict):
            raise TypeError("web must be an object")
        return web
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Invalid SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE; expected Google Web OAuth JSON") from exc


def google_oauth_credentials() -> tuple[str, str]:
    """Read Google OAuth client credentials from environment or a protected file.

    The file format is the JSON download created by Google Cloud for a
    "Web application" OAuth client.  It intentionally only supplies the
    client credentials; the redirect URI remains explicit server configuration.
    """
    client_id = os.environ.get("SIMPLEOFFICE_GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SIMPLEOFFICE_GOOGLE_CLIENT_SECRET", "").strip()
    web = google_oauth_web_config()
    if not web:
        return client_id, client_secret
    try:
        file_client_id = str(web["client_id"]).strip()
        file_client_secret = str(web["client_secret"]).strip()
    except (KeyError, TypeError) as exc:
        raise RuntimeError("Google OAuth JSON requires web.client_id and web.client_secret") from exc
    return client_id or file_client_id, client_secret or file_client_secret


def google_oauth_redirect_uris() -> tuple[str, ...]:
    """Return callback URIs declared in the Google OAuth JSON file."""
    values = google_oauth_web_config().get("redirect_uris", [])
    if not isinstance(values, list):
        return ()
    return tuple(str(value).strip() for value in values if isinstance(value, str) and value.strip())


class SchemeAwareSessionInterface(SecureCookieSessionInterface):
    """Set Secure cookies only for the scheme of the active request."""

    def get_cookie_secure(self, app):
        return request.is_secure


# ensure the environment uses UTF-8 encoding
if str(locale.getpreferredencoding()).lower() not in ["utf-8", "utf8"]:
    raise BaseException("Wrong encoding use utf8")

if sys.version_info < (3,):
    raise BaseException("Wrong Python Version")

script_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.join(script_dir, "..")

database_dir = os.path.join(app_dir, "database")
filebase_dir = os.path.join(database_dir, "files")
template_dir = os.path.join(app_dir, "templates")
static_dir = os.path.join(app_dir, "static")

for p in [database_dir, filebase_dir]:
    x = Path(p)
    if not x.exists():
        x.mkdir()

initlogging()

#see here 4mail logging
#https://flask.palletsprojects.com/en/1.1.x/logging/
app = Flask(__name__,template_folder=template_dir,static_folder=static_dir)
app.jinja_env.globals["now"] = datetime.datetime.now
app.session_interface = SchemeAwareSessionInterface()
app.config['DATABASE_FILEDIR'] = filebase_dir
app.config['DATABASE'] = os.path.join(database_dir, "my.sqlite")
app.config['DATABASE_TRANSLATION'] = os.path.join(database_dir, "translation.sqlite")
app.config['DOCUMENT_ROOT'] = os.environ.get('SIMPLEOFFICE_DOCUMENT_ROOT', os.path.join(database_dir, "documents"))
app.config['MAX_CONTENT_LENGTH'] = configured_upload_limit_bytes()
app.config['WEBDAV_QUOTA_BYTES'] = configured_webdav_quota_bytes()
app.config['WEBDAV_UPLOAD_SCAN'] = configured_webdav_upload_scan()
app.config['WEBDAV_QUARANTINE_BYTES'] = configured_webdav_quarantine_bytes()
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SECRET_KEY'] = load_or_create_secret_key(Path(app.instance_path) / "session-secret")
google_client_id, google_client_secret = google_oauth_credentials()
app.config['GOOGLE_OAUTH_CLIENT_ID'] = google_client_id
app.config['GOOGLE_OAUTH_CLIENT_SECRET'] = google_client_secret
app.config['GOOGLE_OAUTH_REDIRECT_URI'] = os.environ.get('SIMPLEOFFICE_GOOGLE_REDIRECT_URI', '')
app.config['GOOGLE_OAUTH_REDIRECT_URIS'] = google_oauth_redirect_uris()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['MCP_ENABLED'] = os.environ.get('SIMPLEOFFICE_MCP', '1').strip().casefold() in {'1', 'true', 'yes', 'on'}


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(_error):
    limit_mib = int(app.config['MAX_CONTENT_LENGTH']) // MIB
    return (
        f"Anfrage zu groß. Das konfigurierte Upload-Limit beträgt {limit_mib} MiB.\n",
        413,
        {"Content-Type": "text/plain; charset=utf-8"},
    )

# Trust forwarded headers only when an administrator explicitly configures the
# number of reverse proxies. This keeps externally generated CardDAV/share URLs
# correct without accepting spoofed headers in the default local installation.
try:
    trusted_proxy_hops = int(os.environ.get('SIMPLEOFFICE_TRUSTED_PROXY_HOPS', '0'))
except ValueError:
    trusted_proxy_hops = 0
if trusted_proxy_hops > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxy_hops, x_proto=trusted_proxy_hops, x_host=trusted_proxy_hops, x_prefix=trusted_proxy_hops)


# blueprints

from . import auth
app.register_blueprint(auth.bp)

from . import db
db.init_app(app)
with app.app_context():
    db.ensure_auth_database()

from . import translationdb as tdb
tdb.init_app(app)

from . import filedb as fdb
fdb.init_app(app)

from . import document_store as document_store
document_store.init_app(app)

from . import replication_store as replication_store
replication_store.init_app(app)

from . import documents
app.register_blueprint(documents.bp)

from . import carddav
app.register_blueprint(carddav.bp)

from . import caldav
app.register_blueprint(caldav.bp)

from . import webdav
app.register_blueprint(webdav.bp)

from . import contact_audit
app.register_blueprint(contact_audit.bp)

from . import mail_routes
app.register_blueprint(mail_routes.bp)

from . import admin
app.register_blueprint(admin.bp)

from . import mcp
app.register_blueprint(mcp.bp)

from .settings_store import SettingsStore, translate, ui_literal_translations


@app.before_request
def assign_request_id():
    g.request_id = secrets.token_hex(8)


@app.after_request
def publish_request_id(response):
    response.headers["X-Request-ID"] = getattr(g, "request_id", "")
    return response


@app.errorhandler(403)
def forbidden(error):
    return render_template("errors/403.html", message=getattr(error, "description", "Zugriff verweigert.")), 403


@app.errorhandler(Exception)
def unhandled_application_error(error):
    if isinstance(error, HTTPException):
        return error
    if app.testing and app.config.get("PROPAGATE_EXCEPTIONS", True):
        raise error
    request_id = getattr(g, "request_id", secrets.token_hex(8))
    exception_type = type(error).__name__[:120]
    endpoint = (request.endpoint or "")[:160]
    path = request.path[:500]
    frames = [
        {"file": Path(frame.filename).name[:160], "line": frame.lineno, "function": frame.name[:160]}
        for frame in traceback.extract_tb(error.__traceback__)[-12:]
    ]
    try:
        from .access_control import error_fingerprint, utc_now
        from .db import get_db
        actor = getattr(g, "user", None)
        dbh = get_db()
        dbh.execute(
            """INSERT OR IGNORE INTO application_error(
                   occurred_at, request_id, actor_id, exception_type, endpoint,
                   method, path, fingerprint, frames
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (utc_now(), request_id, actor["id"] if actor else None, exception_type,
             endpoint, request.method[:12], path,
             error_fingerprint(exception_type, endpoint, request.method[:12], path),
             json.dumps(frames, ensure_ascii=False)),
        )
        dbh.commit()
    except Exception:
        pass
    app.logger.error("Unhandled application error request_id=%s type=%s endpoint=%s", request_id, exception_type, endpoint)
    return render_template("errors/500.html", request_id=request_id), 500


@app.before_request
def load_interface_preferences():
    settings = SettingsStore(app.config["DOCUMENT_ROOT"]).settings()
    language = session.get("simpleoffice_language", settings["interface"]["default_language"])
    g.language = language if language in ("de", "en") else settings["interface"]["default_language"]
    g.app_settings = settings


@app.context_processor
def template_preferences():
    language = getattr(g, "language", "de")
    return {"tr": lambda key: translate(language, key), "ui_literal_translations": ui_literal_translations(language)}


@app.template_filter('datetime')
def format_datetime(value, format='%Y-%m-%d'):
    return value.strftime(format)


@app.template_filter("calendar_input_datetime")
def calendar_input_datetime(value):
    """Format an ISO instant for datetime-local without changing wall time."""
    if not value:
        return ""
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


@app.template_filter("calendar_display_datetime")
def calendar_display_datetime(value):
    if not value:
        return ""
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        suffix = parsed.strftime(" %Z") if parsed.tzname() else ""
        return parsed.strftime("%d.%m.%Y, %H:%M") + suffix
    except ValueError:
        return str(value)


@app.template_filter("safe_calendar_html")
def safe_calendar_html(value):
    from markupsafe import Markup
    from .calendar_description import sanitize_calendar_html
    return Markup(sanitize_calendar_html(str(value or "")))


@app.after_request
def add_header(response):
    """Add caching headers for static assets when not in debug mode."""
    app.logger.debug(f"debugging ist {app.debug}")
    if not app.debug and (
        "text/css" in str(response.content_type)
        or "application/javascript" in str(response.content_type)
    ):
        then = datetime.datetime.now() + datetime.timedelta(minutes=5)
        response.headers["Cache-Control"] = "public,max-age=1000"
        response.headers["Expires"] = then.strftime("%a, %d %b %Y %H:%M:%S GMT")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    # The application serves all assets itself. Inline Bootstrap/Jinja helpers
    # still require unsafe-inline; removing that needs a dedicated nonce pass.
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; object-src 'none'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
    return response



@app.route('/favicon.ico')
@app.route('/static/<path:dirname>/<path:filename>')
def staticfile(dirname="", filename=""):
    return download_file(static_dir,dirname,filename)


@app.route('/<myFile>', methods=['GET', 'POST'])
@app.route('/<lang>/<myFile>', methods=['GET', 'POST'])
def index(myFile="index.html",lang=None):
    if lang is not None:
        g.current_lang = lang

    return renderwithbs4(myFile)


@app.get('/')
def home():
    """Use the application dashboard as the entrypoint after authentication."""
    if getattr(g, "user", None) is None:
        return redirect(url_for("auth.login"))
    return redirect(url_for("documents.dashboard"))



if __name__ == '__main__':
    print("startup using flask internal or gunicorn3 -b :80 app ")
    #app.run(host="0.0.0.0", debug=True)
