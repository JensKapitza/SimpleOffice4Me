"""Regression coverage for default-deny account admission."""
import tempfile
import unittest
from pathlib import Path

from app import app
from app.access_control import FEATURES
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class AccountRequestPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.saved = {k: app.config.get(k) for k in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        self.addCleanup(lambda: app.config.update(self.saved))
        app.config.update(DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
                          DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
                          TESTING=True, TEST_CSRF_PROTECTION=True)
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute("INSERT INTO user(username,password,is_admin,is_disabled,auth_version) VALUES (?, ?, 1, 0, 1)",
                       ("administrator", hash_password("strong-administrator-password")))
            db.commit()
            self.admin_id = db.execute("SELECT id FROM user WHERE username='administrator'").fetchone()[0]
        self.admin = app.test_client()
        self.admin.get("/auth/login")
        with self.admin.session_transaction() as session:
            session["user_id"] = self.admin_id
            session["auth_version"] = 1

    def csrf(self):
        with self.admin.session_transaction() as session:
            return session["_csrf_token"]

    def pending(self, username):
        with app.app_context():
            db = get_db()
            cursor = db.execute("INSERT INTO account_request(username,email,password_hash) VALUES (?, ?, ?)",
                                (username, username + "@example.test", "hashed-secret"))
            db.commit()
            return cursor.lastrowid

    def test_approval_denies_all_member_features_and_discards_hash(self):
        request_id = self.pending("pending")
        self.admin.get("/auth/account-requests/admin")
        result = self.admin.post(f"/auth/account-requests/admin/{request_id}",
                                 data={"decision": "approve", "_csrf_token": self.csrf()})
        self.assertEqual(302, result.status_code)
        with app.app_context():
            db = get_db()
            item = db.execute("SELECT id,is_admin FROM user WHERE username='pending'").fetchone()
            self.assertEqual(0, item["is_admin"])
            permissions = db.execute("SELECT feature,enabled FROM user_permission WHERE user_id=?",
                                     (item["id"],)).fetchall()
            self.assertEqual(set(FEATURES), {p["feature"] for p in permissions})
            self.assertTrue(all(p["enabled"] == 0 for p in permissions))
            decision = db.execute("SELECT status,password_hash FROM account_request WHERE id=?",
                                  (request_id,)).fetchone()
            self.assertEqual(("approved", ""), tuple(decision))

    def test_rejection_creates_no_user_and_discards_hash(self):
        request_id = self.pending("rejected")
        self.admin.get("/auth/account-requests/admin")
        result = self.admin.post(f"/auth/account-requests/admin/{request_id}",
                                 data={"decision": "reject", "_csrf_token": self.csrf()})
        self.assertEqual(302, result.status_code)
        with app.app_context():
            db = get_db()
            self.assertIsNone(db.execute("SELECT id FROM user WHERE username='rejected'").fetchone())
            decision = db.execute("SELECT status,password_hash FROM account_request WHERE id=?",
                                  (request_id,)).fetchone()
            self.assertEqual(("rejected", ""), tuple(decision))


if __name__ == "__main__":
    unittest.main()
