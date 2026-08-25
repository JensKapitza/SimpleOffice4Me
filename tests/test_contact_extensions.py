import tempfile
import unittest
from pathlib import Path

from app.contact_extensions import ContactCRMStore, _eml_preview
from app.contact_store import ContactStore
from app.document_store import DocumentStore


class ContactExtensionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_crm_data_is_separate_from_carddav_contact(self):
        contacts = ContactStore(self.root)
        contact = contacts.upsert({"display_name": "Kunde GmbH", "email": "old@example.test"}, "admin")
        crm = ContactCRMStore(self.root)
        crm.save(contact["contact_id"], {
            "roles": ["customer", "supplier"], "status": "active",
            "customer_number": "K-100", "supplier_number": "L-9",
            "discount": "5%", "payment_terms": "netto 30", "payment_days": "30",
            "currency": "EUR", "tax_number": "T-1", "vat_id": "DE123",
            "bank_accounts": [{"holder": "Kunde GmbH", "iban": "DE00", "bic": "TEST", "bank": "Bank"}],
            "addresses": [], "communications": [], "relations": [], "notes": "CRM only",
        }, "admin")
        contacts.upsert({"display_name": "Kunde GmbH", "email": "new@example.test"}, "carddav:admin", contact["contact_id"])
        stored = crm.record(contact["contact_id"])
        self.assertEqual("K-100", stored["customer_number"])
        self.assertEqual("DE00", stored["bank_accounts"][0]["iban"])
        self.assertEqual("new@example.test", contacts.get(contact["contact_id"], "admin")["fields"]["email"])

    def test_external_update_creates_pending_proposal(self):
        contacts = ContactStore(self.root)
        contact = contacts.upsert({"display_name": "Person", "email": "a@example.test"}, "admin")
        crm = ContactCRMStore(self.root)
        token = crm.create_update_token(contact["contact_id"], "admin")
        proposal_id = crm.submit_proposal(token, {"email": "b@example.test", "phone": "123"}, "127.0.0.1")
        proposal = next(item for item in crm.proposals() if item["proposal_id"] == proposal_id)
        self.assertEqual("pending", proposal["status"])
        self.assertEqual("a@example.test", contacts.get(contact["contact_id"], "admin")["fields"]["email"])

    def test_eml_preview_parses_headers_body_and_attachments(self):
        store = DocumentStore(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        eml = self.root / "mail.eml"
        eml.write_bytes(
            b"From: sender@example.test\r\nTo: receiver@example.test\r\nCc: copy@example.test\r\n"
            b"Subject: Test mail\r\nDate: Tue, 25 Aug 2026 20:00:00 +0200\r\n"
            b"Message-ID: <id@example.test>\r\nMIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=x\r\n\r\n--x\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\nHello world\r\n--x\r\n"
            b"Content-Type: text/plain\r\nContent-Disposition: attachment; filename=note.txt\r\n\r\nattachment\r\n--x--\r\n"
        )
        store.scan()
        document = store.get_document(eml)
        preview = _eml_preview(self.root, document["document_id"])
        self.assertEqual("Test mail", preview["subject"])
        self.assertIn("sender@example.test", preview["from"])
        self.assertIn("receiver@example.test", preview["to"])
        self.assertIn("Hello world", preview["text"])
        self.assertEqual("note.txt", preview["attachments"][0]["name"])


if __name__ == "__main__":
    unittest.main()
