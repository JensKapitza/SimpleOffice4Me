import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from flask import Flask

from app.contact_store import ContactStore
from app.federation_contacts_http import bp


class FederationContactsHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root), SECRET_KEY="test-secret")
        self.app.register_blueprint(bp)
        self.previous = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN")
        os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = "fed-token"
        with self.app.app_context():
            self.contacts = ContactStore(self.root)
            self.contact = self.contacts.upsert(
                {"display_name": "Ada Example", "email": "ada@example.test", "phone": "+491234"},
                "admin",
            )
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Bearer fed-token"}

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SIMPLEOFFICE_FEDERATION_TOKEN", None)
        else:
            os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = self.previous
        self.temp.cleanup()

    def test_export_plain_text_contains_vcard(self):
        response = self.client.get("/federation/v1/contacts/export.txt", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith("text/plain"))
        self.assertIn(b"BEGIN:VCARD", response.data)
        self.assertIn(b"FN:Ada Example", response.data)

    def test_export_zip_contains_vcf(self):
        response = self.client.get("/federation/v1/contacts/export.zip", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.data), "r") as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 1)
            self.assertTrue(names[0].endswith(".vcf"))
            self.assertIn(b"BEGIN:VCARD", archive.read(names[0]))

    def test_import_text_plain_vcard(self):
        card = (
            "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:remote-1\r\n"
            "FN:Remote Person\r\nEMAIL:remote@example.test\r\nEND:VCARD\r\n"
        )
        response = self.client.post(
            "/federation/v1/contacts/import",
            headers=self.auth,
            data=card.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["imported"], 1)
        with self.app.app_context():
            imported = ContactStore(self.root).get("remote-1")
        self.assertEqual(imported["fields"]["email"], "remote@example.test")

    def test_import_zip_rejects_path_traversal(self):
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, "w") as archive:
            archive.writestr("../escape.vcf", "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:X\r\nEND:VCARD\r\n")
        response = self.client.post(
            "/federation/v1/contacts/import",
            headers=self.auth,
            data=memory.getvalue(),
            content_type="application/zip",
        )
        self.assertEqual(response.status_code, 400)

    def test_requires_bearer(self):
        response = self.client.get("/federation/v1/contacts/export.vcf")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
