"""The CRM contact CSV route protects private data and emits safe cells."""
import csv
import io
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app, db
from app.contact_extensions import ContactCRMStore
from app.contact_store import ContactStore


class CRMCSVExportRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "documents"
        self.root.mkdir()
        keys = ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        previous = {key: app.config.get(key) for key in keys}
        self.addCleanup(lambda: app.config.update(previous))
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(self.root.parent / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            db.ensure_auth_database()
            connection = db.get_db()
            for username in ("alice", "bob"):
                connection.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash("synthetic-crm-password")),
                )
            connection.commit()
        contacts = ContactStore(self.root)
        self.alice_contact = contacts.upsert({
            "display_name": "=1+1",
            "company": "@SUM(1,1)",
            "email": "+customer@example.test",
            "phone": "-123",
        }, "alice")
        self.bob_contact = contacts.upsert({"display_name": "Private Bob Contact"}, "bob")
        crm = ContactCRMStore(self.root)
        crm.save(self.alice_contact["contact_id"], {
            "roles": ["=customer"],
            "status": "active",
            "customer_number": "@K-1",
            "supplier_number": "+S-1",
        }, "alice")
        crm.save(self.bob_contact["contact_id"], {
            "roles": ["customer"], "status": "active", "customer_number": "B-1",
        }, "bob")
        self.client = app.test_client()
        self.client.post("/auth/login", data={"username": "alice", "password": "synthetic-crm-password"})
        self.url = "/documents/contacts/crm.csv"

    def test_authenticated_export_is_private_utf8_and_formula_safe(self):
        response = self.client.get(self.url)
        try:
            self.assertEqual(200, response.status_code)
            self.assertEqual("text/csv; charset=utf-8", response.content_type)
            self.assertIn("attachment; filename=crm-kontakte.csv", response.headers["Content-Disposition"])
            self.assertEqual("private, no-store", response.headers["Cache-Control"])
            self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
            self.assertTrue(response.data.startswith(b"\xef\xbb\xbf"))
            rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"), newline=""), delimiter=";"))
            self.assertEqual(2, len(rows))
            self.assertEqual(
                ["'=1+1", "'@SUM(1,1)", "'+customer@example.test", "'-123", "active",
                 "'=customer", "'@K-1", "'+S-1"],
                rows[1][:8],
            )
            self.assertNotIn("Private Bob Contact", response.get_data(as_text=True))
        finally:
            response.close()

    def test_export_requires_authentication(self):
        anonymous = app.test_client()
        self.assertEqual(302, anonymous.get(self.url).status_code)


if __name__ == "__main__":
    unittest.main()
