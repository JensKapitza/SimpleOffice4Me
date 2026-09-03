import sqlite3
import time
from datetime import datetime, timezone

import click
from flask import current_app, g
from flask.cli import with_appcontext


SQLITE_BUSY_TIMEOUT_MS = 30_000


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        )
        g.db.row_factory = sqlite3.Row
        # Keep concurrent browser/DAV/background requests from failing immediately
        # on short writer contention and enforce declared relationships.
        g.db.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        g.db.execute("PRAGMA foreign_keys=ON")

    return g.db


def close_db(e=None):
    db = g.pop('db', None)

    if db is not None:
        db.close()


def _migrate_sensitive_database_values() -> None:
    """Replace legacy plaintext authentication material in SQLite in place."""
    from werkzeug.security import generate_password_hash

    from .security_controls import protect_value

    db = get_db()
    for row in db.execute("SELECT id, password FROM user").fetchall():
        password = str(row["password"] or "")
        if password and not password.startswith(("scrypt:", "pbkdf2:")):
            db.execute("UPDATE user SET password=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (generate_password_hash(password), row["id"]))
    for row in db.execute("SELECT provider,user_id,access_token,refresh_token FROM oauth_token").fetchall():
        access_token = str(row["access_token"] or "")
        refresh_token = str(row["refresh_token"] or "")
        protected_access = access_token if not access_token or access_token.startswith("enc:v1:") else protect_value(access_token, "google-oauth")
        protected_refresh = refresh_token if not refresh_token or refresh_token.startswith("enc:v1:") else protect_value(refresh_token, "google-oauth")
        if (protected_access, protected_refresh) != (access_token, refresh_token):
            db.execute(
                "UPDATE oauth_token SET access_token=?,refresh_token=?,updated_at=CURRENT_TIMESTAMP WHERE provider=? AND user_id=?",
                (protected_access, protected_refresh, row["provider"], row["user_id"]),
            )


def init_db():
    db = get_db()

    with current_app.open_resource('schema.sql') as f:
        db.executescript(f.read().decode('utf8'))


def ensure_auth_database() -> None:
    """Create the login table on first start without replacing existing data."""
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS oauth_identity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            subject TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            email TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(provider, subject),
            FOREIGN KEY (user_id) REFERENCES user (id)
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS oauth_identity_user_id ON oauth_identity(user_id)")
    # Additive migration: existing installations retain their user accounts.
    columns = {row[1] for row in db.execute("PRAGMA table_info(user)").fetchall()}
    for name in ("display_name", "email", "avatar_url", "profile_source", "profile_updated_at", "theme"):
        if name not in columns:
            db.execute(f"ALTER TABLE user ADD COLUMN {name} TEXT")
    additions = {
        "is_admin": "INTEGER NOT NULL DEFAULT 0",
        "is_disabled": "INTEGER NOT NULL DEFAULT 0",
        "auth_version": "INTEGER NOT NULL DEFAULT 1",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE user ADD COLUMN {name} {definition}")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    db.execute("UPDATE user SET created_at = COALESCE(created_at, ?), updated_at = COALESCE(updated_at, ?)", (now, now))
    # Existing installations gain one recoverable administrator without
    # widening any other account. The oldest account is the documented owner.
    if db.execute("SELECT COUNT(*) FROM user WHERE is_admin = 1").fetchone()[0] == 0:
        db.execute("UPDATE user SET is_admin = 1 WHERE id = (SELECT MIN(id) FROM user)")
    db.execute(
        """CREATE TABLE IF NOT EXISTS user_permission (
            user_id INTEGER NOT NULL, feature TEXT NOT NULL, enabled INTEGER NOT NULL,
            updated_at TEXT NOT NULL, updated_by INTEGER,
            PRIMARY KEY(user_id, feature), FOREIGN KEY(user_id) REFERENCES user(id)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS security_event (
            id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
            actor_id INTEGER, actor_name TEXT, action TEXT NOT NULL,
            target_type TEXT NOT NULL, target_id TEXT, outcome TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '{}'
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS application_error (
            id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
            request_id TEXT NOT NULL UNIQUE, actor_id INTEGER, exception_type TEXT NOT NULL,
            endpoint TEXT, method TEXT NOT NULL, path TEXT NOT NULL,
            fingerprint TEXT NOT NULL, frames TEXT NOT NULL DEFAULT '[]',
            resolved_at TEXT, resolved_by INTEGER
        )"""
    )
    error_columns = {row[1] for row in db.execute("PRAGMA table_info(application_error)").fetchall()}
    if "frames" not in error_columns:
        db.execute("ALTER TABLE application_error ADD COLUMN frames TEXT NOT NULL DEFAULT '[]'")
    db.execute("CREATE INDEX IF NOT EXISTS security_event_time ON security_event(occurred_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS security_event_target ON security_event(target_type, target_id, occurred_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS application_error_time ON application_error(occurred_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS application_error_fingerprint ON application_error(fingerprint, occurred_at DESC)")
    db.execute(
        """CREATE TABLE IF NOT EXISTS login_throttle (
            key TEXT PRIMARY KEY,
            failures INTEGER NOT NULL,
            window_started INTEGER NOT NULL,
            blocked_until INTEGER NOT NULL
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS login_throttle_expiry ON login_throttle(blocked_until, window_started)")
    # Throttle rows are short-lived operational state. Clean stale rows during
    # normal startup so long-running installations do not accumulate them.
    timestamp = int(time.time())
    db.execute(
        "DELETE FROM login_throttle WHERE blocked_until < ? AND window_started < ?",
        (timestamp, timestamp - 24 * 60 * 60),
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS oauth_token (
            provider TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            access_token TEXT NOT NULL,
            refresh_token TEXT,
            expires_at TEXT,
            scopes TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(provider, user_id),
            FOREIGN KEY (user_id) REFERENCES user (id)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS mcp_token (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            token_prefix TEXT NOT NULL,
            can_write INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at TEXT,
            FOREIGN KEY (user_id) REFERENCES user (id)
        )"""
    )
    _migrate_sensitive_database_values()
    db.execute("CREATE INDEX IF NOT EXISTS mcp_token_user ON mcp_token(user_id, revoked_at)")
    db.execute(
        """CREATE TABLE IF NOT EXISTS mcp_operation (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL, actor_id INTEGER NOT NULL, token_id INTEGER NOT NULL,
            tool TEXT NOT NULL, target_id TEXT, outcome TEXT NOT NULL, error_type TEXT,
            FOREIGN KEY (actor_id) REFERENCES user(id), FOREIGN KEY (token_id) REFERENCES mcp_token(id)
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS mcp_operation_time ON mcp_operation(occurred_at DESC)")
    db.commit()


@click.command('init-db')
@with_appcontext
def init_db_command():
    """Clear the existing data and create new tables."""
    init_db()
    click.echo('Initialized the database.')


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
