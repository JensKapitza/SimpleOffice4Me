import json
import tempfile
import unittest
from pathlib import Path

from app.contact_store import ContactStore
from app.gamification_adapters import contact_candidates


def _write_contacts(root, contacts):
    store = ContactStore(root)
    store.initialize()
    store.contacts_path.write_text(json.dumps({"contacts": contacts}), encoding="utf-8")


def _contact(contact_id, name, owner="owner", *, fields=None, readers=None, tags=None):
    return {
        "contact_id": contact_id,
        "fields": {"display_name": name, **(fields or {})},
        "addresses": [],
        "owner": owner,
        "managers": [],
        "readers": readers or [],
        "tags": tags or [],
        "groups": [],
        "changes": [],
        "created_by": owner,
    }


class GamificationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_contact_adapter_only_returns_actor_visible_contacts(self):
        _write_contacts(self.root, [
            _contact("owned", "Owned"),
            _contact("shared", "Shared", owner="other", readers=["owner"]),
            _contact("hidden", "Hidden", owner="other"),
        ])
        refs = {candidate.object_ref for candidate in contact_candidates(self.root, "owner")}
        self.assertEqual(refs, {"contact:owned", "contact:shared"})

    def test_contact_adapter_excludes_crm_and_financial_contacts(self):
        _write_contacts(self.root, [
            _contact("normal", "Normal", fields={"phone": "123"}),
            _contact("customer", "Customer", fields={"customer_number": "K-1"}),
            _contact("bank", "Bank", fields={"bank_iban": "DE00"}),
            _contact("tagged", "Tagged", tags=["CRM"]),
        ])
        candidates = contact_candidates(self.root, "owner")
        self.assertEqual([candidate.object_ref for candidate in candidates], ["contact:normal"])
        self.assertEqual(candidates[0].data["display_name"], "Normal")
        self.assertIn("phone", candidates[0].data["existing_fields"])
        self.assertNotIn("123", str(candidates[0].data))

    def test_contact_adapter_never_copies_notes_or_sharing_metadata(self):
        _write_contacts(self.root, [
            _contact("one", "One", fields={"note": "private text", "email": "a@example.invalid"}),
        ])
        candidate = contact_candidates(self.root, "owner")[0]
        self.assertEqual(set(candidate.data), {"display_name", "existing_fields"})
        self.assertNotIn("private text", str(candidate.data))
        self.assertNotIn("a@example.invalid", str(candidate.data))


if __name__ == "__main__":
    unittest.main()
