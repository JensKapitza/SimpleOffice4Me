"""Security regression tests for admin-created invitation accounts."""
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class InvitationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = {key: app.config.get(key) for key in ("DATABASE", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
                          TESTING=True, TEST_CSRF_PROTECTION=True)
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                """INSERT INTO user(username,password,is_admin,is_disabled,auth_version)
                   VALUES (?, ?, 1, 0, 1)""",
                ("admin", hash_password("long-administrator-password")),
            )
            db.commit()
            self.admin_id = db.execute("SELECT id FROM user WHERE username='admin'").fetchone()[0]

    def tearDown(self):
        app.config.update(self.original)
        self.temp.cleanup()

    def _csrf(self, client):
        with client.session_transaction() as session:
            return session["_csrf_token"]

    def _admin(self):
        client = app.test_client()
        client.get("/auth/login")
        with client.session_transaction() as session:
            session["user_id"] = self.admin_id
            session["auth_version"] = 1
        return client

    def test_invitation_requires_admin_and_csrf(self):
        guest = app.test_client()
        self.assertEqual(403, guest.get("/auth/account-requests/admin/invite").status_code)
        self.assertEqual(403, guest.post("/auth/account-requests/admin/invite",
                                         data={"username": "member", "email": "member@example.test"}).status_code)
        admin = self._admin()
        self.assertEqual(403, admin.post("/auth/account-requests/admin/invite",
                                         data={"username": "member", "email": "member@example.test"}).status_code)

    def test_invitation_is_single_use(self):
        admin = self._admin()
        admin.get("/auth/account-requests/admin/invite")
        response = admin.post("/auth/account-requests/admin/invite", data={
            "username": "newmember", "email": "newmember@example.test",
            "_csrf_token": self._csrf(admin),
        })
        self.assertEqual(200, response.status_code)
        match = re.search(rb'/auth/account-requests/invite/([A-Za-z0-9_-]{20,128})', response.data)
        self.assertIsNotNone(match)
        path = "/auth/account-requests/invite/" + match.group(1).decode()
        with app.app_context():
            row = get_db().execute("SELECT is_disabled,is_admin FROM user WHERE username='newmember'").fetchone()
            self.assertEqual((1, 0), tuple(row))
        guest = app.test_client()
        self.assertEqual(200, guest.get(path).status_code)
        self.assertEqual(403, guest.post(path, data={"password": "a-secure-long-password"}).status_code)
        self.assertEqual(302, guest.post(path, data={
            "password": "a-secure-long-password", "_csrf_token": self._csrf(guest)
        }).status_code)
        self.assertEqual(404, guest.get(path).status_code)
        with app.app_context():
            row = get_db().execute("SELECT is_disabled,is_admin FROM user WHERE username='newmember'").fetchone()
            self.assertEqual((0, 0), tuple(row))


if __name__ == "__main__":
    unittest.main()
