"""Invited standard accounts are least-privileged before and after activation."""
import re
import tempfile
import unittest
from pathlib import Path

from app import app
from app.access_control import FEATURES
from app.db import ensure_auth_database, get_db


class InvitationDefaultAclTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {k: app.config.get(k) for k in ("DATABASE", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
                          TESTING=True, TEST_CSRF_PROTECTION=False)
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute("INSERT INTO user(username,password,is_admin,auth_version) VALUES ('admin','hash',1,1)")
            db.commit()
            self.admin_id = db.execute("SELECT id FROM user WHERE username='admin'").fetchone()[0]

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_standard_invitation_is_least_privileged(self):
        admin = app.test_client()
        with admin.session_transaction() as session:
            session["user_id"] = self.admin_id
            session["auth_version"] = 1
        result = admin.post("/auth/account-requests/admin/invite",
                            data={"username": "invited", "email": "invited@example.test"})
        self.assertEqual(200, result.status_code)
        match = re.search(rb"/auth/account-requests/invite/([A-Za-z0-9_-]{20,128})", result.data)
        self.assertIsNotNone(match)
        with app.app_context():
            db = get_db()
            user = db.execute("SELECT id,is_admin,is_disabled FROM user WHERE username='invited'").fetchone()
            self.assertEqual((0, 1), (user["is_admin"], user["is_disabled"]))
            permissions = db.execute("SELECT feature,enabled FROM user_permission WHERE user_id=?",
                                     (user["id"],)).fetchall()
            self.assertEqual(set(FEATURES), {row["feature"] for row in permissions})
            self.assertTrue(all(row["enabled"] == 0 for row in permissions))
        guest = app.test_client()
        token_path = "/auth/account-requests/invite/" + match.group(1).decode()
        accepted = guest.post(token_path, data={"password": "secure-invitation-passphrase"})
        self.assertEqual(302, accepted.status_code)
        with app.app_context():
            db = get_db()
            user = db.execute("SELECT id,is_disabled FROM user WHERE username='invited'").fetchone()
            self.assertEqual(0, user["is_disabled"])
            rows = db.execute("SELECT enabled FROM user_permission WHERE user_id=?",
                              (user["id"],)).fetchall()
            self.assertEqual(len(FEATURES), len(rows))
            self.assertTrue(all(row["enabled"] == 0 for row in rows))


if __name__ == "__main__":
    unittest.main()
