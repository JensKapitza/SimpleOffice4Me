import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app import app
from app.db import get_db
from app.web_export import _consume_token


@pytest.fixture()
def export_app(tmp_path):
    app.config.update(TESTING=True, DATABASE=str(tmp_path / "test.sqlite"), TEST_CSRF_PROTECTION=False)
    with app.app_context():
        get_db().executescript("""
        CREATE TABLE IF NOT EXISTS user (
          id INTEGER PRIMARY KEY, username TEXT, password TEXT, is_admin INTEGER DEFAULT 0,
          is_disabled INTEGER DEFAULT 0, auth_version INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS web_export_token (
          id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,token_hash TEXT UNIQUE NOT NULL,
          token_prefix TEXT NOT NULL,auth_version INTEGER NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
          remaining_uses INTEGER NOT NULL,last_used_at TEXT,revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS security_event (
          id INTEGER PRIMARY KEY AUTOINCREMENT,occurred_at TEXT NOT NULL,actor_id INTEGER,actor_name TEXT,
          action TEXT NOT NULL,target_type TEXT NOT NULL,target_id TEXT,outcome TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '{}'
        );
        """)
        get_db().execute("INSERT OR REPLACE INTO user(id,username,password,is_admin,is_disabled,auth_version) VALUES(1,'u','x',0,0,7)")
        get_db().commit()
    yield app


def _insert(secret, *, uses=1, minutes=5, revoked=False, auth_version=7, disabled=False):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db = get_db()
    db.execute("UPDATE user SET is_disabled=? WHERE id=1", (int(disabled),))
    db.execute(
        """INSERT INTO web_export_token(user_id,token_hash,token_prefix,auth_version,created_at,expires_at,remaining_uses,revoked_at)
           VALUES(1,?,?,?,?,?,?,?)""",
        (hashlib.sha256(secret.encode()).hexdigest(), secret[:16], auth_version, now.isoformat(),
         (now + timedelta(minutes=minutes)).isoformat(), uses, now.isoformat() if revoked else None),
    )
    db.commit()


def test_export_token_is_single_atomic_consumption(export_app):
    secret = "so_export_" + "a" * 40
    with export_app.app_context():
        _insert(secret)
        assert _consume_token(secret) is not None
        assert _consume_token(secret) is None


@pytest.mark.parametrize("kwargs", [
    {"minutes": -1}, {"revoked": True}, {"auth_version": 6}, {"disabled": True},
])
def test_export_token_rejects_invalid_account_or_token(export_app, kwargs):
    secret = "so_export_" + "b" * 40
    with export_app.app_context():
        _insert(secret, **kwargs)
        assert _consume_token(secret) is None


def test_renderer_session_is_read_only(export_app):
    secret = "so_export_" + "c" * 40
    with export_app.app_context():
        _insert(secret, uses=2)
    client = export_app.test_client()
    response = client.get("/web-export/session", headers={"X-SimpleOffice-Export-Token": secret})
    assert response.status_code == 204
    response = client.post("/web-export/revoke")
    assert response.status_code == 403
