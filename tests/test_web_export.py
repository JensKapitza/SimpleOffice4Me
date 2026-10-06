import hashlib
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import app
from app.db import ensure_auth_database, get_db
from app.web_export import _consume_token


@pytest.fixture()
def export_app(tmp_path):
    app.config.update(TESTING=True, DATABASE=str(tmp_path / "test.sqlite"), TEST_CSRF_PROTECTION=False)
    with app.app_context():
        ensure_auth_database()
        get_db().execute(
            "INSERT INTO user(id,username,password,is_admin,is_disabled,auth_version,created_at,updated_at) "
            "VALUES(1,'u','x',0,0,7,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
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


def test_export_token_concurrent_consumption_allows_only_one(export_app):
    secret = "so_export_" + "d" * 40
    with export_app.app_context():
        _insert(secret, uses=1)
    barrier = threading.Barrier(2)
    results = []
    lock = threading.Lock()

    def consume():
        with export_app.app_context():
            barrier.wait()
            result = _consume_token(secret) is not None
            with lock:
                results.append(result)

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(results) == [False, True]
