from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from app import app
from app.business_document_generation import _invoice_store_path
from app.contact_store import ContactStore
from app.db import ensure_auth_database, get_db
from app.document_store import atomic_json_write
from app.password_security import hash_password
from app.settings_store import SettingsStore
from app.v3_finance import FinanceLifecycleStore


class FinanceInvoicePickerRouteTests(unittest.TestCase):
    password = "test-only-invoice-picker-password"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?,1,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin"),
            )
            db.commit()
        self.root = Path(app.config["DOCUMENT_ROOT"])
        SettingsStore(self.root).save({"v3": {"v3.finance": True}}, "test")
        self.client = app.test_client()
        login_page = self.client.get("/auth/login")
        token = re.search(rb'name="_csrf_token" value="([^"]+)"', login_page.data).group(1).decode()
        self.client.post("/auth/login", data={"username": "admin", "password": self.password, "_csrf_token": token})

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _csrf(self):
        with self.client.session_transaction() as session:
            return session.get("_csrf_token", "")

    def _order(self):
        contact = ContactStore(self.root).upsert({"display_name": "Picker Contact"}, "admin")
        store = FinanceLifecycleStore(self.root)
        order = store.create("order", "admin", contact_id=contact["contact_id"], title="Confirmed order")
        order = store.transition(order.lifecycle_id, "confirmed", "admin")
        return store, order, contact["contact_id"]

    def _invoice(self, invoice_id, contact_id):
        row = {
            "invoice_id": invoice_id,
            "invoice_number": "DRAFT-2026-0042",
            "contact_id": contact_id,
            "status": "draft",
            "issue_date": "2026-11-18",
            "currency": "EUR",
            "totals": {"gross": "42.00"},
        }
        atomic_json_write(_invoice_store_path(self.root, invoice_id), row)
        return row

    def test_picker_lists_same_contact_registered_draft_and_links_existing_record(self):
        store, order, contact_id = self._order()
        draft = self._invoice("picker-draft", contact_id)
        registered = store.attach_invoice(draft, "admin")
        url = f"/documents/business/lifecycle-v3/{order.lifecycle_id}"
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        self.assertIn(b"DRAFT-2026-0042", response.data)

        response = self.client.post(
            url + "/convert",
            data={"_csrf_token": self._csrf(), "target_kind": "invoice", "invoice_id": draft["invoice_id"]},
        )
        self.assertEqual(302, response.status_code)
        linked = store.by_external("invoice", draft["invoice_id"])
        self.assertEqual(registered.lifecycle_id, linked.lifecycle_id)
        self.assertEqual(order.lifecycle_id, linked.predecessor_id)

    def test_forged_other_contact_invoice_is_rejected(self):
        store, order, contact_id = self._order()
        other = ContactStore(self.root).upsert({"display_name": "Other Contact"}, "admin")
        draft = self._invoice("forged-draft", other["contact_id"])
        store.attach_invoice(draft, "admin")
        response = self.client.post(
            f"/documents/business/lifecycle-v3/{order.lifecycle_id}/convert",
            data={"_csrf_token": self._csrf(), "target_kind": "invoice", "invoice_id": draft["invoice_id"]},
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual("", store.by_external("invoice", draft["invoice_id"]).predecessor_id)
        self.assertEqual([], store.successors(order.lifecycle_id))

    def test_picker_empty_state_links_to_existing_invoice_editor(self):
        store, order, contact_id = self._order()
        response = self.client.get(f"/documents/business/lifecycle-v3/{order.lifecycle_id}")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"No available invoice draft", response.data)
        self.assertIn(f"/documents/business/contacts/{contact_id}/invoice".encode(), response.data)

    def test_picker_is_hidden_until_source_is_ready(self):
        contact = ContactStore(self.root).upsert({"display_name": "Unready Contact"}, "admin")
        order = FinanceLifecycleStore(self.root).create(
            "order", "admin", contact_id=contact["contact_id"], title="Draft order"
        )
        response = self.client.get(f"/documents/business/lifecycle-v3/{order.lifecycle_id}")
        self.assertEqual(200, response.status_code)
        self.assertNotIn(b"Existing invoice draft", response.data)
        self.assertNotIn(b"Create an invoice draft", response.data)

    def test_picker_hides_draft_with_conflicting_project(self):
        contact = ContactStore(self.root).upsert({"display_name": "Project Contact"}, "admin")
        store = FinanceLifecycleStore(self.root)
        order = store.create(
            "order", "admin", contact_id=contact["contact_id"],
            project_id="project-source", title="Project order",
        )
        order = store.transition(order.lifecycle_id, "confirmed", "admin")
        draft = self._invoice("project-conflict-draft", contact["contact_id"])
        store.create(
            "invoice", "admin", contact_id=contact["contact_id"],
            project_id="project-other", external_id=draft["invoice_id"],
        )
        response = self.client.get(f"/documents/business/lifecycle-v3/{order.lifecycle_id}")
        self.assertEqual(200, response.status_code)
        self.assertNotIn(b"DRAFT-2026-0042", response.data)
        self.assertIn(b"No available invoice draft", response.data)


if __name__ == "__main__":
    unittest.main()
