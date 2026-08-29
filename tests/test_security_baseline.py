import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.security_controls import login_retry_after, record_login_failure
from tools.generate_sbom import build_sbom


class SecurityBaselineTests(unittest.TestCase):
    def test_security_headers_are_present(self):
        response = app.test_client().get("/")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        self.assertIn("camera=()", response.headers["Permissions-Policy"])
        secure = app.test_client().get("/", base_url="https://office.example.test")
        self.assertIn("max-age=31536000", secure.headers["Strict-Transport-Security"])
        self.assertEqual("same-origin", secure.headers["Cross-Origin-Opener-Policy"])

    def test_browser_mutations_require_session_csrf_token(self):
        previous = {key: app.config.get(key) for key in ("TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True)
        try:
            client = app.test_client()
            client.get("/auth/login")
            self.assertEqual(403, client.post("/auth/login", data={"username": "nobody", "password": "wrong"}).status_code)
            self.assertEqual(403, client.post("/settings/mcp").status_code)
            self.assertNotEqual(403, client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code)
            with client.session_transaction() as browser_session:
                token = browser_session["_csrf_token"]
            response = client.post("/auth/login", data={"username": "nobody", "password": "wrong", "_csrf_token": token})
            self.assertNotEqual(403, response.status_code)
        finally:
            app.config.update(previous)

    def test_login_throttle_is_persistent_and_identifier_is_hashed(self):
        with tempfile.TemporaryDirectory() as temp:
            previous = app.config["DATABASE"]
            app.config["DATABASE"] = str(Path(temp) / "security.sqlite")
            try:
                with app.app_context():
                    database.ensure_auth_database()
                    db = database.get_db()
                    for _ in range(5):
                        record_login_failure(db, "alice@example.test", "192.0.2.5", now=1000)
                    self.assertEqual(900, login_retry_after(db, "alice@example.test", "192.0.2.5", now=1000))
                    keys = [row[0] for row in db.execute("SELECT key FROM login_throttle")]
                    self.assertFalse(any("alice" in key or "192.0.2.5" in key for key in keys))
            finally:
                app.config["DATABASE"] = previous

    def test_sbom_contains_application_dependencies(self):
        sbom = build_sbom()
        self.assertEqual("CycloneDX", sbom["bomFormat"])
        self.assertTrue(any(item["name"].casefold() == "flask" for item in sbom["components"]))
        self.assertNotEqual("urn:uuid:00000000-0000-4000-8000-000000000001", sbom["serialNumber"])
