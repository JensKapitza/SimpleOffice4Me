import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.contact_store import ContactStore
from app.document_store import CONTROL_DIR
from app.business_document_generation import _invoice_content_pdf, invoice
from app.invoice_relations import InvoiceRelationStore
from app.password_security import hash_password
from pypdf import PdfReader


class InvoiceRelationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = InvoiceRelationStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_typed_relations_are_bidirectional_unique_acyclic_and_soft_removed(self):
        relation = self.store.add_relationship("invoice-a", "invoice-b", "follow_up", "alice")
        self.assertEqual("follow_up", relation["relation_type"])
        self.assertEqual(relation["relation_id"], self.store.relationships_for("invoice-a")[0]["relation_id"])
        self.assertEqual(relation["relation_id"], self.store.relationships_for("invoice-b")[0]["relation_id"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.add_relationship("invoice-a", "invoice-b", "follow_up", "alice")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.add_relationship("invoice-a", "invoice-b", "supplement", "alice")
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.store.add_relationship("invoice-b", "invoice-a", "supplement", "alice")

        removed = self.store.remove_relationship("invoice-b", relation["relation_id"], "alice")

        self.assertEqual("alice", removed["removed_by"])
        self.assertEqual([], self.store.relationships_for("invoice-a"))
        history = self.store.relationships_for("invoice-a", include_removed=True)
        self.assertEqual(1, len(history))
        self.assertTrue(history[0]["removed_at"])
        self.assertEqual("removed", self.store.audit_events_for("invoice-a")[0]["event_type"])
        self.assertEqual("removed", self.store.audit_events_for("invoice-b")[0]["event_type"])

    def test_internal_annotation_revisions_keep_history_and_reject_stale_edits(self):
        created = self.store.add_annotation("invoice-a", "Interne Rückfrage zum Leistungsumfang.", "alice")
        updated = self.store.revise_annotation(
            "invoice-a", created["annotation_id"], "Rückfrage wurde geklärt.", "bob", 1,
        )
        self.assertEqual(2, updated["version"])
        with self.assertRaisesRegex(ValueError, "changed"):
            self.store.revise_annotation(
                "invoice-a", created["annotation_id"], "Veralteter Text", "alice", 1,
            )

        deleted = self.store.revise_annotation(
            "invoice-a", created["annotation_id"], "", "alice", 2, deleted=True,
        )
        visible = self.store.annotations_for("invoice-a")
        self.assertTrue(deleted["deleted"])
        self.assertEqual(1, len(visible))
        self.assertTrue(visible[0]["deleted"])
        self.assertEqual(3, len(visible[0]["history"]))
        self.assertEqual("annotation_deleted", self.store.audit_events_for("invoice-a")[0]["event_type"])

    def test_identifiers_and_annotation_content_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "itself"):
            self.store.add_relationship("invoice-a", "invoice-a", "follow_up", "alice")
        with self.assertRaisesRegex(ValueError, "identifier"):
            self.store.add_relationship("../invoice", "invoice-a", "follow_up", "alice")
        with self.assertRaisesRegex(ValueError, "required"):
            self.store.add_annotation("invoice-a", "   ", "alice")
        with self.assertRaisesRegex(ValueError, "too long"):
            self.store.add_annotation("invoice-a", "x" * 5001, "alice")

    def test_concurrent_duplicate_relationships_create_one_active_link(self):
        barrier = threading.Barrier(2)

        def add_link(actor: str) -> str:
            barrier.wait()
            try:
                self.store.add_relationship("invoice-a", "invoice-b", "follow_up", actor)
            except ValueError:
                return "duplicate"
            return "created"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(add_link, ("alice", "bob")))

        self.assertCountEqual(["created", "duplicate"], results)
        self.assertEqual(1, len(self.store.relationships_for("invoice-a")))


class InvoiceRelationshipRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.previous = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(self.base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            ensure_auth_database()
        self.client = self._login("invoice-owner")
        with app.app_context():
            get_db().execute(
                "INSERT INTO user(username,password,is_admin) VALUES(?,?,0)",
                ("invoice-outsider", hash_password("synthetic-invoice-password")),
            )
            get_db().commit()
        self.outsider = self._login("invoice-outsider", register=False)
        self.contact = ContactStore(self.root).upsert(
            {"display_name": "Invoice Customer"}, "invoice-owner",
        )
        self.invoice_a = self._save_invoice("invoice-a", self.contact["contact_id"], "2026-0001")
        self.invoice_b = self._save_invoice("invoice-b", self.contact["contact_id"], "2026-0002")
        self.invoice_pdf_path = self.root / "customer-invoices" / "invoice-a.pdf"
        self.invoice_pdf_path.parent.mkdir(parents=True)
        self.invoice_pdf_path.write_bytes(b"%PDF-1.4\nimmutable issued invoice\n%%EOF\n")
        self.invoice_a.update({"document_id": "document-invoice-a", "last_path": "customer-invoices/invoice-a.pdf"})
        (self.root / CONTROL_DIR / "invoices" / "invoice-a.json").write_text(
            json.dumps(self.invoice_a), encoding="utf-8",
        )
        self.other_contact = ContactStore(self.root).upsert(
            {"display_name": "Other Customer"}, "invoice-owner",
        )
        self.invoice_c = self._save_invoice("invoice-c", self.other_contact["contact_id"], "2026-0003")

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    @staticmethod
    def _login(username: str, *, register: bool = True):
        client = app.test_client()
        password = "synthetic-invoice-password"
        if register:
            client.post("/auth/register", data={"username": username, "password": password})
        client.post("/auth/login", data={"username": username, "password": password})
        return client

    def _save_invoice(self, invoice_id: str, contact_id: str, number: str) -> dict:
        row = {
            "invoice_id": invoice_id,
            "invoice_number": number,
            "contact_id": contact_id,
            "status": "open",
            "issue_date": "2026-10-01",
            "service_date": "2026-10-01",
            "due_date": "2026-10-15",
            "currency": "EUR",
            "payment_terms": "14 Tage",
            "buyer": {"name": "Invoice Customer", "label": "Invoice Customer\nExample Street 1"},
            "seller": {"name": "Example Ltd", "street": "Street 1", "postal": "12345", "city": "Berlin", "country": "DE", "iban": ""},
            "lines": [],
            "totals": {"net": "100.00", "tax": "19.00", "gross": "119.00", "due": "119.00", "vat_groups": {"19": {"basis": "100.00", "tax": "19.00"}}},
            "payments": [],
            "write_offs": [],
            "credit_notes": [],
            "zugferd": {"status": "validated", "version": "2.5.2", "profile": "EN16931", "pdfa_pipeline": "pdfa3_created", "validation": {"details": []}},
        }
        directory = self.root / CONTROL_DIR / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{invoice_id}.json").write_text(json.dumps(row), encoding="utf-8")
        return row

    def test_relationships_and_annotations_are_private_sidecar_data(self):
        path = self.root / CONTROL_DIR / "invoices" / "invoice-a.json"
        before = path.read_bytes()
        pdf_digest = hashlib.sha256(self.invoice_pdf_path.read_bytes()).hexdigest()
        linked = self.client.post(
            "/documents/business/invoices/invoice-a/relationships",
            data={"related_invoice_id": "invoice-b", "relationship_type": "follow_up"},
        )
        self.assertEqual(302, linked.status_code)
        self.assertEqual(before, path.read_bytes())
        relationships = InvoiceRelationStore(self.root).relationships_for("invoice-a")
        self.assertEqual(1, len(relationships))
        self.assertEqual("invoice-b", relationships[0]["target_invoice_id"])

        note = "Interne Prüfung; nicht auf den Kundenbeleg übernehmen."
        noted = self.client.post(
            "/documents/business/invoices/invoice-a/annotations",
            data={"body": note},
        )
        self.assertEqual(302, noted.status_code)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(pdf_digest, hashlib.sha256(self.invoice_pdf_path.read_bytes()).hexdigest())
        self.assertNotIn(note, path.read_text(encoding="utf-8"))
        pdf = _invoice_content_pdf(invoice(self.root, "invoice-a"))
        self.assertNotIn(note, PdfReader(BytesIO(pdf)).pages[0].extract_text())
        detail = self.client.get("/documents/business/invoices/invoice-a")
        self.assertEqual(200, detail.status_code)
        self.assertIn("invoice-b", detail.get_data(as_text=True))
        self.assertIn(note, detail.get_data(as_text=True))
        reverse_detail = self.client.get("/documents/business/invoices/invoice-b")
        self.assertEqual(200, reverse_detail.status_code)
        self.assertIn("invoice-a", reverse_detail.get_data(as_text=True))

        removed = self.client.post(
            f"/documents/business/invoices/invoice-a/relationships/{relationships[0]['relation_id']}/remove",
        )
        self.assertEqual(302, removed.status_code)
        history = InvoiceRelationStore(self.root).relationships_for("invoice-a", include_removed=True)
        self.assertTrue(history[0]["removed_at"])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(pdf_digest, hashlib.sha256(self.invoice_pdf_path.read_bytes()).hexdigest())

    def test_relationship_routes_enforce_customer_scope_and_invoice_permissions(self):
        cross_customer = self.client.post(
            "/documents/business/invoices/invoice-a/relationships",
            data={"related_invoice_id": self.invoice_c["invoice_id"], "relationship_type": "supplement"},
        )
        self.assertEqual(404, cross_customer.status_code)
        foreign = self.outsider.post(
            "/documents/business/invoices/invoice-a/annotations",
            data={"body": "must not be stored"},
        )
        self.assertEqual(404, foreign.status_code)
        self.assertEqual([], InvoiceRelationStore(self.root).relationships_for("invoice-a"))
        self.assertEqual([], InvoiceRelationStore(self.root).annotations_for("invoice-a"))

    def test_new_invoice_form_offers_only_same_customer_finalized_reference_invoices(self):
        response = self.client.get(
            f"/documents/business/contacts/{self.contact['contact_id']}/invoice"
        )
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('name="reference_invoice_id"', body)
        self.assertIn("2026-0001", body)
        self.assertIn("2026-0002", body)
        self.assertNotIn("2026-0003", body)

    def test_optional_draft_reference_does_not_allow_foreign_or_draft_invoice(self):
        draft = self._save_invoice("invoice-draft", self.contact["contact_id"], "DRAFT-2026-0004")
        draft["status"] = "draft"
        draft["reference_invoice_id"] = "invoice-a"
        draft["reference_invoice_number"] = self.invoice_a["invoice_number"]
        (self.root / CONTROL_DIR / "invoices" / "invoice-draft.json").write_text(json.dumps(draft), encoding="utf-8")
        detail = self.client.get("/documents/business/invoices/invoice-draft")
        self.assertEqual(200, detail.status_code)
        self.assertIn(self.invoice_a["invoice_number"], detail.get_data(as_text=True))
        relation = self.client.post(
            "/documents/business/invoices/invoice-a/relationships",
            data={"related_invoice_id": "invoice-draft", "relationship_type": "follow_up"},
        )
        self.assertEqual(302, relation.status_code)
        self.assertEqual([], InvoiceRelationStore(self.root).relationships_for("invoice-a"))

    def test_project_relationship_requires_a_shared_billing_project(self):
        path_a = self.root / CONTROL_DIR / "invoices" / "invoice-a.json"
        path_b = self.root / CONTROL_DIR / "invoices" / "invoice-b.json"
        self.invoice_a["lines"] = [{"project_id": "project-a"}]
        self.invoice_b["lines"] = [{"project_id": "project-b"}]
        path_a.write_text(json.dumps(self.invoice_a), encoding="utf-8")
        path_b.write_text(json.dumps(self.invoice_b), encoding="utf-8")

        rejected = self.client.post(
            "/documents/business/invoices/invoice-a/relationships",
            data={"related_invoice_id": "invoice-b", "relationship_type": "project_related"},
        )
        self.assertEqual(302, rejected.status_code)
        self.assertEqual([], InvoiceRelationStore(self.root).relationships_for("invoice-a"))

        self.invoice_b["lines"] = [{"project_id": "project-a"}]
        path_b.write_text(json.dumps(self.invoice_b), encoding="utf-8")
        accepted = self.client.post(
            "/documents/business/invoices/invoice-a/relationships",
            data={"related_invoice_id": "invoice-b", "relationship_type": "project_related"},
        )
        self.assertEqual(302, accepted.status_code)
        self.assertEqual("project_related", InvoiceRelationStore(self.root).relationships_for("invoice-a")[0]["relation_type"])

    def test_invoice_overview_filters_by_project_and_related_invoice(self):
        path_a = self.root / CONTROL_DIR / "invoices" / "invoice-a.json"
        path_b = self.root / CONTROL_DIR / "invoices" / "invoice-b.json"
        self.invoice_a["lines"] = [{"project_id": "project-a"}]
        self.invoice_b["lines"] = [{"project_id": "project-b"}]
        path_a.write_text(json.dumps(self.invoice_a), encoding="utf-8")
        path_b.write_text(json.dumps(self.invoice_b), encoding="utf-8")
        InvoiceRelationStore(self.root).add_relationship(
            "invoice-a", "invoice-b", "follow_up", "invoice-owner",
        )

        project_response = self.client.get("/documents/business/invoices?project_id=project-a")
        self.assertEqual(200, project_response.status_code)
        project_rows = project_response.get_data(as_text=True).partition("<tbody>")[2].partition("</tbody>")[0]
        self.assertIn("2026-0001", project_rows)
        self.assertNotIn("2026-0002", project_rows)

        related_response = self.client.get(
            "/documents/business/invoices?related_invoice_id=invoice-b"
        )
        self.assertEqual(200, related_response.status_code)
        table_rows = related_response.get_data(as_text=True).partition("<tbody>")[2].partition("</tbody>")[0]
        self.assertIn("2026-0001", table_rows)
        self.assertNotIn("2026-0002", table_rows)
        self.assertNotIn("2026-0003", table_rows)

    def test_internal_annotation_can_be_revised_through_versioned_route(self):
        self.client.post(
            "/documents/business/invoices/invoice-a/annotations",
            data={"body": "Original"},
        )
        annotation = InvoiceRelationStore(self.root).annotations_for("invoice-a")[0]
        response = self.client.post(
            f"/documents/business/invoices/invoice-a/annotations/{annotation['annotation_id']}",
            data={"body": "Revised", "version": "1"},
        )
        self.assertEqual(302, response.status_code)
        updated = InvoiceRelationStore(self.root).annotations_for("invoice-a")[0]
        self.assertEqual("Revised", updated["body"])
        self.assertEqual(["Original", "Revised"], [item["body"] for item in updated["history"]])

    def test_relationship_mutation_requires_the_session_csrf_token(self):
        app.config["TEST_CSRF_PROTECTION"] = True
        token = "invoice-relationship-csrf-token-123456"
        with self.client.session_transaction() as session:
            session["_csrf_token"] = token
        url = "/documents/business/invoices/invoice-a/relationships"
        data = {"related_invoice_id": "invoice-b", "relationship_type": "follow_up"}

        rejected = self.client.post(url, data=data)
        self.assertEqual(403, rejected.status_code)
        self.assertEqual([], InvoiceRelationStore(self.root).relationships_for("invoice-a"))

        accepted = self.client.post(url, data={**data, "_csrf_token": token})
        self.assertEqual(302, accepted.status_code)
        self.assertEqual(1, len(InvoiceRelationStore(self.root).relationships_for("invoice-a")))


if __name__ == "__main__":
    unittest.main()
