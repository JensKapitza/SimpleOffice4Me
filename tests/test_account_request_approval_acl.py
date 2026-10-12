"""Regression tests: approved account requests must not inherit implicit privileges."""
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.access_control import FEATURES


class ApprovalAclTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = {key: app.config.get(key) for key in ("DATABASE", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
                          TESTING=True, TEST_CSRF_PROTECTION=False)
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute("INSERT INTO user(username,password,is_admin,auth_version) VALUES ('admin','hash',1,1)")
            db.execute("INSERT INTO account_request(username,email,password_hash,status) VALUES (?,?,?,'pending')",
                       ("candidate", "candidate@example.test", "stored-hash"))
            db.commit()
            self.admin_id = db.execute("SELECT id FROM user WHERE username='admin'").fetchone()[0]
            self.request_id = db.execute("SELECT id FROM account_request WHERE username='candidate'").fetchone()[0]

    def tearDown(self):
        app.config.update(self.original)
        self.temp.cleanup()

    def test_approval_disables_all_features_and_erases_duplicate_hash(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.admin_id
            session["auth_version"] = 1
        response = client.post(f"/auth/account-requests/admin/{self.request_id}",
                               data={"decision": "approve"})
        self.assertEqual(302, response.status_code)
        with app.app_context():
            db = get_db()
            user = db.execute("SELECT id,password,is_admin FROM user WHERE username='candidate'").fetchone()
            self.assertIsNotNone(user)
            self.assertEqual("stored-hash", user["password"])
            self.assertEqual(0, user["is_admin"])
            rows = db.execute("SELECT feature,enabled FROM user_permission WHERE user_id=?",
                              (user["id"],)).fetchall()
            self.assertEqual(set(FEATURES), {row["feature"] for row in rows})
            self.assertTrue(all(row["enabled"] == 0 for row in rows))
            stored = db.execute("SELECT status,password_hash FROM account_request WHERE id=?",
                                (self.request_id,)).fetchone()
            self.assertEqual(("approved", ""), tuple(stored))


if __name__ == "__main__":
    unittest.main()
