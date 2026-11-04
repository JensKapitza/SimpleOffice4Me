"""SMTP send settings in the disk store + admin UI (no environment)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask
from werkzeug.security import generate_password_hash

from app import app, app_config_store, db as database
from app import mail_smtp as smtp


class MailSmtpStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.flask = Flask(__name__)
        self.flask.config.update(SECRET_KEY="mail-smtp-test-secret")
        self.context = self.flask.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        patcher = patch.object(
            app_config_store, "settings_path", return_value=self.root / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_are_unconfigured(self):
        self.assertFalse(smtp.configured())
        self.assertEqual(587, smtp.port())
        self.assertTrue(smtp.starttls())
        self.assertFalse(smtp.password_configured())

    def test_settings_roundtrip(self):
        values = {
            "host": "mail.example",
            "sender": "office@example",
            "port": "2525",
            "user": "office",
            "password": "secret-password",
        }
        with self.assertRaisesRegex(ValueError, "STARTTLS"):
            smtp.save({**values, "starttls": ""})
        smtp.save({**values, "starttls": "1"})
        self.assertTrue(smtp.configured())
        self.assertEqual("mail.example", smtp.host())
        self.assertEqual("office@example", smtp.sender())
        self.assertEqual(2525, smtp.port())
        self.assertTrue(smtp.starttls())
        self.assertEqual("office", smtp.user())
        self.assertEqual("secret-password", smtp.password())
        status = smtp.status()
        self.assertTrue(status["password_configured"])
        self.assertNotIn("secret-password", str(status))

    def test_legacy_plain_smtp_setting_is_forced_to_starttls(self):
        app_config_store.put("smtp", {"starttls": ""})
        self.assertTrue(smtp.starttls())
        with self.assertRaisesRegex(ValueError, "STARTTLS"):
            smtp.validate({"host": "mail.example", "sender": "office@example", "starttls": False})

    def test_empty_password_keeps_stored_secret(self):
        smtp.save({"host": "mail.example", "sender": "office@example", "password": "secret-password"})
        smtp.save({"host": "mail.example", "sender": "office@example", "password": ""})
        self.assertEqual("secret-password", smtp.password())

    def test_invalid_values_fail_bilingual(self):
        with self.assertRaises(ValueError) as german:
            smtp.validate({"host": "mail.example", "sender": "not-an-address"})
        self.assertIn("SMTP", str(german.exception))
        with self.assertRaises(ValueError) as english:
            smtp.validate({"host": "mail.example", "sender": "office@example", "port": "99999"}, "en")
        self.assertIn("SMTP", str(english.exception))
        with self.assertRaises(ValueError):
            smtp.validate({"host": "", "sender": "", "password": "has\nnewline"})

    def test_login_uses_store_without_sending_mail(self):
        smtp.save({
            "host": "mail.example",
            "sender": "office@example",
            "user": "office",
            "password": "secret-password",
        })
        server = MagicMock()
        server.__enter__.return_value = server
        with patch("app.mail_smtp.smtplib.SMTP", return_value=server) as connect:
            smtp.test_login()
        connect.assert_called_once_with("mail.example", 587, timeout=20)
        server.starttls.assert_called_once_with()
        server.login.assert_called_once_with("office", "secret-password")
        server.send_message.assert_not_called()

    def test_login_without_configuration_fails(self):
        with self.assertRaises(ValueError):
            smtp.test_login()


class MailSmtpAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        patcher = patch.object(
            app_config_store, "settings_path", return_value=base / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("smtp-admin", generate_password_hash("smtp-admin-password")),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("smtp-user", generate_password_hash("smtp-user-password")),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "smtp-admin", "password": "smtp-admin-password"},
        )
        self.assertLess(response.status_code, 400)
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_page_shows_settings_without_password(self):
        with app.app_context():
            smtp.save({"host": "mail.example", "sender": "office@example", "password": "hidden-secret"})
        response = self.client.get("/admin/mail/smtp")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("mail.example", body)
        self.assertIn("Hinterlegt", body)
        self.assertIn("STARTTLS erforderlich", body)
        self.assertNotIn("hidden-secret", body)

    def test_save_settings(self):
        response = self.client.post(
            "/admin/mail/smtp",
            data={
                "host": "mail.example",
                "sender": "office@example",
                "port": "2525",
                "starttls": "1",
                "user": "office",
                "password": "saved-secret",
            },
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertEqual("mail.example", smtp.host())
            self.assertEqual(2525, smtp.port())
            self.assertEqual("saved-secret", smtp.password())

    def test_save_settings_rejects_invalid(self):
        response = self.client.post(
            "/admin/mail/smtp",
            data={"host": "mail.example", "sender": "not-an-address"},
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertFalse(smtp.configured())

    def test_smtp_routes_need_admin(self):
        user_client = app.test_client()
        user_client.post(
            "/auth/login",
            data={"username": "smtp-user", "password": "smtp-user-password"},
        )
        self.assertEqual(403, user_client.get("/admin/mail/smtp").status_code)
        self.assertEqual(403, user_client.post("/admin/mail/smtp", data={}).status_code)
        self.assertEqual(403, user_client.post("/admin/mail/smtp/test", data={}).status_code)


if __name__ == "__main__":
    unittest.main()
