"""Feature gate for the split mail blueprints (fail-closed license check)."""
from __future__ import annotations

from app import app as flask_app
from app.db import get_db
from app.license_metering import feature_for_endpoint
from app.password_security import hash_password
from tests.http_api_fixture import PublicHttpTestCase

URLS = (
    "/documents/mail/reader/message/keywords",
    "/documents/mail/reader/handover/task",
    "/documents/mail/reader/handover/followup",
)


class MailFeatureGateTests(PublicHttpTestCase):
    def _denied_client(self):
        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO user(username, password, is_admin) VALUES (?, ?, 0)",
                ("nogmail", hash_password("test-only-api-password")))
            user_id = db.execute(
                "SELECT id FROM user WHERE username='nogmail'").fetchone()[0]
            db.execute(
                """INSERT INTO user_permission(user_id, feature, enabled, updated_at)
                   VALUES (?, 'mail', 0, CURRENT_TIMESTAMP)""",
                (user_id,))
            db.commit()
        client = flask_app.test_client()
        self._login(client, "nogmail")
        return client

    def test_split_blueprints_are_metered_as_mail(self):
        self.assertEqual("mail", feature_for_endpoint("mail_keywords.set_keywords"))
        self.assertEqual("mail", feature_for_endpoint("mail_handover.handover_task"))
        self.assertEqual("mail", feature_for_endpoint("mail_reader_cases.create_case"))

    def test_user_without_mail_feature_is_denied(self):
        denied = self._denied_client()
        headers = self._csrf_headers(denied, "/auth/login")
        for url in URLS:
            with self.subTest(url=url):
                response = denied.post(url, data={}, headers=headers)
                self.assertEqual(403, response.status_code)
                self.assertIn("gesperrt", response.get_data(as_text=True))

    def test_user_with_mail_feature_passes_the_gate(self):
        headers = self._csrf_headers(self.client, "/auth/login")
        for url in URLS:
            with self.subTest(url=url):
                response = self.client.post(url, data={}, headers=headers)
                self.assertNotEqual(403, response.status_code)


if __name__ == "__main__":
    import unittest

    unittest.main()
