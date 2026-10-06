import hashlib
import threading
import unittest
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from app import app
from app.db import ensure_auth_database, get_db
from app.web_export import _consume_token


class WebExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        app.config.update(TESTING=True, DATABASE=self.tmp.name + "/test.sqlite", TEST_CSRF_PROTECTION=False)
        with app.app_context():
            ensure_auth_database()
            get_db().execute(
                "INSERT INTO user(id,username,password,is_admin,is_disabled,auth_version,created_at,updated_at) "
                "VALUES(1,'u','x',0,0,7,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
            get_db().commit()

    def tearDown(self):
        self.tmp.cleanup()

    def insert_token(self, secret, *, uses=1, minutes=5, revoked=False, auth_version=7, disabled=False):
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

    def test_export_token_is_single_atomic_consumption(self):
        secret = "so_export_" + "a" * 40
        with app.app_context():
            self.insert_token(secret)
            self.assertIsNotNone(_consume_token(secret))
            self.assertIsNone(_consume_token(secret))

    def test_export_token_rejects_expired_revoked_changed_or_disabled(self):
        cases = (
            {"minutes": -1},
            {"revoked": True},
            {"auth_version": 6},
            {"disabled": True},
        )
        for index, kwargs in enumerate(cases):
            with self.subTest(kwargs=kwargs), app.app_context():
                secret = "so_export_" + chr(98 + index) * 40
                self.insert_token(secret, **kwargs)
                self.assertIsNone(_consume_token(secret))
                get_db().execute("UPDATE user SET is_disabled=0 WHERE id=1")
                get_db().commit()

    def test_renderer_session_is_read_only(self):
        secret = "so_export_" + "f" * 40
        with app.app_context():
            self.insert_token(secret, uses=2)
        client = app.test_client()
        response = client.get("/web-export/session", headers={"X-SimpleOffice-Export-Token": secret})
        self.assertEqual(response.status_code, 204)
        response = client.post("/web-export/revoke")
        self.assertEqual(response.status_code, 403)

    def test_export_token_concurrent_consumption_allows_only_one(self):
        secret = "so_export_" + "g" * 40
        with app.app_context():
            self.insert_token(secret, uses=1)
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def consume():
            with app.app_context():
                barrier.wait()
                result = _consume_token(secret) is not None
                with lock:
                    results.append(result)

        threads = [threading.Thread(target=consume) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sorted(results), [False, True])


if __name__ == "__main__":
    unittest.main()
