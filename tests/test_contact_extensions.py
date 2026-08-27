import tempfile
import unittest
from pathlib import Path

from app.contact_extensions import ContactCRMStore, _eml_preview
from app.contact_store import ContactStore
from app.document_store import DocumentStore
from app.settings_store import ui_literal_translations


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

    def test_crm_overview_searches_and_filters_combined_contact_data(self):
        contacts = ContactStore(self.root)
        customer = contacts.upsert({"display_name": "Kunde Nord", "company": "Beispiel GmbH"}, "admin")
        supplier = contacts.upsert({"display_name": "Lieferant Süd"}, "admin")
        crm = ContactCRMStore(self.root)
        crm.save(customer["contact_id"], {"roles": ["customer"], "status": "active", "customer_number": "K-100", "communications": [{"type": "email", "value": "einkauf@example.test"}]}, "admin")
        crm.save(supplier["contact_id"], {"roles": ["supplier"], "status": "blocked", "supplier_number": "L-9"}, "admin")
        crm.add_activity(customer["contact_id"], {"kind": "phone", "direction": "incoming", "subject": "Sonderbestellung"}, "admin")

        by_query = crm.overview(contacts.contacts("admin"), query="einkauf@example.test")
        by_activity = crm.overview(contacts.contacts("admin"), query="Sonderbestellung")
        customers = crm.overview(contacts.contacts("admin"), status="active", role="customer")
        recent = crm.overview(contacts.contacts("admin"), sort="recent")
        without_activity = crm.overview(contacts.contacts("admin"), without_activity=True)

        self.assertEqual([customer["contact_id"]], [row["contact"]["contact_id"] for row in by_query])
        self.assertEqual([customer["contact_id"]], [row["contact"]["contact_id"] for row in by_activity])
        self.assertEqual([customer["contact_id"]], [row["contact"]["contact_id"] for row in customers])
        self.assertEqual(customer["contact_id"], recent[0]["contact"]["contact_id"])
        self.assertEqual([supplier["contact_id"]], [row["contact"]["contact_id"] for row in without_activity])

    def test_communication_and_changes_share_one_timeline(self):
        contacts = ContactStore(self.root)
        contact = contacts.upsert({"display_name": "Person", "email": "alt@example.test"}, "admin")
        crm = ContactCRMStore(self.root)
        crm.save(contact["contact_id"], {"roles": ["customer"], "status": "active", "notes": "Start"}, "admin")
        activity = crm.add_activity(contact["contact_id"], {"kind": "phone", "direction": "incoming", "subject": "Rückfrage", "note": "Rückruf vereinbart"}, "admin")
        contacts.upsert({"display_name": "Person", "email": "neu@example.test"}, "admin", contact["contact_id"])

        timeline = crm.timeline(contacts.get(contact["contact_id"], "admin"))

        self.assertIn(activity["activity_id"], {entry.get("activity_id") for entry in timeline})
        self.assertIn("crm_change", {entry.get("type") for entry in timeline})
        self.assertIn("contact_change", {entry.get("type") for entry in timeline})

    def test_crm_save_preserves_existing_activities(self):
        crm = ContactCRMStore(self.root)
        crm.add_activity("contact-1", {"kind": "email", "direction": "outgoing", "subject": "Angebot"}, "admin")
        saved = crm.save("contact-1", {"roles": ["customer"], "status": "prospect", "notes": "Offen"}, "admin")
        self.assertEqual("Angebot", saved["activities"][0]["subject"])

    def test_new_crm_labels_have_english_translations(self):
        translations = ui_literal_translations("en")
        self.assertEqual("CRM contact overview", translations["CRM-Kontaktübersicht"])
        self.assertEqual("Communication and change history", translations["Kommunikations- und Änderungshistorie"])
        self.assertEqual("Only contacts without activity", translations["Nur Kontakte ohne Aktivität"])

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
