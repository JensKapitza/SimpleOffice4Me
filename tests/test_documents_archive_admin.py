"""External archives are server administration: admin-only routes (#1154).

`register_external_archive` writes a marker into an arbitrary server
directory and `offload_old_versions` copies version files there. Both
routes must refuse non-admin users before touching the filesystem.
"""
from __future__ import annotations

from pathlib import Path

from app import app as flask_app
from app.db import get_db
from app.password_security import hash_password
from tests.http_api_fixture import PublicHttpTestCase


class ArchiveAdminGateTests(PublicHttpTestCase):
    def _login_as(self, username, *, admin=False):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO user(username, password, is_admin) VALUES (?, ?, ?)",
                (username, hash_password("test-only-api-password"), 1 if admin else 0))
            db.commit()
        client = flask_app.test_client()
        self._login(client, username)
        return client

    def test_register_archive_needs_admin(self):
        marker = Path("/tmp/.simpleoffice-archive.json")
        if marker.exists():
            marker.unlink()
        self.addCleanup(lambda: marker.exists() and marker.unlink())
        member = self._login_as("member")
        headers = self._csrf_headers(member, "/auth/login")
        response = member.post(
            "/documents/archives/register",
            data={"path": "/tmp", "label": "x", "tags": ""},
            headers=headers)
        self.assertEqual(403, response.status_code)
        self.assertFalse(marker.exists())

    def test_offload_versions_needs_admin(self):
        member = self._login_as("member")
        headers = self._csrf_headers(member, "/auth/login")
        response = member.post(
            "/documents/no-such-doc/offload-versions",
            data={"archive_path": "/tmp", "confirm": "AUSLAGERN"},
            headers=headers)
        self.assertEqual(403, response.status_code)

    def test_admin_can_still_register_archive(self):
        admin = self._login_as("rooty", admin=True)
        headers = self._csrf_headers(admin, "/auth/login")
        from app import app as flask_app

        target = Path(flask_app.config["DOCUMENT_ROOT"]) / "ext-archive"
        target.mkdir(parents=True, exist_ok=True)
        response = admin.post(
            "/documents/archives/register",
            data={"path": str(target), "label": "USB", "tags": ""},
            headers=headers)
        self.assertEqual(302, response.status_code)
        self.assertTrue((target / ".simpleoffice-archive.json").is_file())


if __name__ == "__main__":
    import unittest

    unittest.main()
