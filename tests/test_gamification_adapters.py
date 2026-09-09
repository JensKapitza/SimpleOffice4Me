import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.contact_store import ContactStore
from app.gamification_adapters import (
    apply_contact_proposal,
    contact_candidates,
    contact_proposal_can_apply,
    document_candidates,
    image_candidates,
    image_preview_path,
)


def _write_contacts(root, contacts):
    store = ContactStore(root)
    store.initialize()
    store.contacts_path.write_text(json.dumps({"contacts": contacts}), encoding="utf-8")


def _contact(contact_id, name, owner="owner", *, fields=None, readers=None, managers=None, tags=None):
    return {
        "contact_id": contact_id,
        "fields": {"display_name": name, **(fields or {})},
        "addresses": [],
        "owner": owner,
        "managers": managers or [],
        "readers": readers or [],
        "tags": tags or [],
        "groups": [],
        "changes": [],
        "created_by": owner,
    }


def _proposal(contact_id="one", field="city", value="Duisburg", **extra):
    return {
        "provider": "contacts",
        "object_ref": f"contact:{contact_id}",
        "field_name": field,
        "value": value,
        "accepted_by": None,
        **extra,
    }


def _photo(document_id="photo-1", *, tags=None, source="mobile-web-bulk"):
    return {
        "document_id": document_id,
        "last_path": f"photos/{document_id}.jpg",
        "sha256": "a" * 64,
        "tags": tags or ["bild", "foto-upload"],
        "attributes": {"photo_upload": {"source": source}},
        "preview": {
            "status": "ready",
            "source_sha256": "a" * 64,
            "thumbnail": f".webcache/{document_id}/{'a' * 64}/thumbnail.webp",
        },
    }


def _file(document_id="file-1", *, tags=None, name="notes.txt"):
    return {
        "document_id": document_id,
        "last_path": f"archive/folder/{name}",
        "tags": tags or [],
        "attributes": {},
        "state": "active",
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

    def test_owner_can_apply_missing_contact_field(self):
        _write_contacts(self.root, [_contact("one", "One")])
        proposal = _proposal()
        self.assertTrue(contact_proposal_can_apply(self.root, "owner", proposal))
        updated = apply_contact_proposal(self.root, "owner", proposal)
        self.assertEqual(updated["fields"]["city"], "Duisburg")

    def test_manager_can_apply_missing_contact_field(self):
        _write_contacts(self.root, [_contact("one", "One", owner="other", managers=["manager"])])
        proposal = _proposal()
        self.assertTrue(contact_proposal_can_apply(self.root, "manager", proposal))
        updated = apply_contact_proposal(self.root, "manager", proposal)
        self.assertEqual(updated["fields"]["city"], "Duisburg")

    def test_reader_cannot_apply_contact_proposal(self):
        _write_contacts(self.root, [_contact("one", "One", owner="other", readers=["reader"])])
        proposal = _proposal()
        self.assertFalse(contact_proposal_can_apply(self.root, "reader", proposal))
        with self.assertRaises(ValueError):
            apply_contact_proposal(self.root, "reader", proposal)
        self.assertNotIn("city", ContactStore(self.root).get("one", "reader")["fields"])

    def test_populated_field_makes_proposal_stale(self):
        _write_contacts(self.root, [_contact("one", "One", fields={"city": "Essen"})])
        proposal = _proposal(value="Duisburg")
        self.assertFalse(contact_proposal_can_apply(self.root, "owner", proposal))
        with self.assertRaises(ValueError):
            apply_contact_proposal(self.root, "owner", proposal)
        self.assertEqual(ContactStore(self.root).get("one", "owner")["fields"]["city"], "Essen")

    def test_contact_that_becomes_crm_is_refused_at_apply_time(self):
        _write_contacts(self.root, [_contact("one", "One", fields={"customer_number": "K-1"})])
        proposal = _proposal()
        self.assertFalse(contact_proposal_can_apply(self.root, "owner", proposal))
        with self.assertRaises(ValueError):
            apply_contact_proposal(self.root, "owner", proposal)

    def test_arbitrary_contact_field_cannot_be_applied(self):
        _write_contacts(self.root, [_contact("one", "One")])
        proposal = _proposal(field="note", value="secret")
        self.assertFalse(contact_proposal_can_apply(self.root, "owner", proposal))
        with self.assertRaises(ValueError):
            apply_contact_proposal(self.root, "owner", proposal)

    def test_photo_adapter_requires_explicit_upload_and_cached_thumbnail(self):
        safe = _photo("safe")
        not_upload = _photo("legacy", source="scanner")
        cached = self.root / ".webcache" / "safe" / ("a" * 64) / "thumbnail.webp"
        with patch("app.gamification_adapters.DocumentStore.list_documents", return_value=[safe, not_upload]), \
             patch("app.gamification_adapters.PreviewService.cached_path", side_effect=lambda item, variant="thumbnail": cached if item["document_id"] == "safe" else None):
            candidates = image_candidates(self.root, "owner")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].object_ref, "document:safe")
        self.assertEqual(candidates[0].data, {"preview": True})
        self.assertEqual(candidates[0].resource_class, "photo")
        self.assertNotIn("photos/", str(candidates[0].data))
        self.assertNotIn("safe", str(candidates[0].data))

    def test_photo_adapter_excludes_sensitive_markers(self):
        documents = [
            _photo("private", tags=["foto-upload", "privat"]),
            _photo("invoice", tags=["foto-upload", "rechnung"]),
        ]
        cached = self.root / ".webcache" / "x" / "thumbnail.webp"
        with patch("app.gamification_adapters.DocumentStore.list_documents", return_value=documents), \
             patch("app.gamification_adapters.PreviewService.cached_path", return_value=cached):
            self.assertEqual(image_candidates(self.root, "owner"), [])

    def test_photo_adapter_refuses_missing_cached_thumbnail(self):
        with patch("app.gamification_adapters.DocumentStore.list_documents", return_value=[_photo()]), \
             patch("app.gamification_adapters.PreviewService.cached_path", return_value=None):
            self.assertEqual(image_candidates(self.root, "owner"), [])

    def test_photo_preview_resolver_never_falls_back_to_original(self):
        document = _photo("one")
        cached = self.root / ".webcache" / "one" / "thumbnail.webp"
        with patch("app.gamification_adapters.DocumentStore.get_document", return_value=document), \
             patch("app.gamification_adapters.PreviewService.cached_path", return_value=cached):
            self.assertEqual(image_preview_path(self.root, "owner", "document:one"), cached)
        with patch("app.gamification_adapters.DocumentStore.get_document", return_value=document), \
             patch("app.gamification_adapters.PreviewService.cached_path", return_value=None):
            self.assertIsNone(image_preview_path(self.root, "owner", "document:one"))

    def test_photo_preview_resolver_rejects_client_style_arbitrary_reference(self):
        self.assertIsNone(image_preview_path(self.root, "owner", "../../etc/passwd"))
        self.assertIsNone(image_preview_path(self.root, "", "document:one"))

    def test_file_adapter_requires_explicit_release_tag(self):
        released = _file("released", tags=["gamification-freigegeben"], name="urlaub-plan.txt")
        hidden = _file("hidden", tags=["normal"], name="hidden.txt")
        with patch("app.gamification_adapters.DocumentStore.list_documents", return_value=[released, hidden]):
            candidates = document_candidates(self.root, "owner")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].object_ref, "document:released")
        self.assertEqual(candidates[0].data, {"display_name": "urlaub-plan.txt"})
        self.assertNotIn("archive/folder", str(candidates[0].data))

    def test_file_adapter_blocks_sensitive_even_when_released(self):
        documents = [
            _file("invoice", tags=["gamification-freigegeben", "rechnung"], name="x.pdf"),
            _file("private", tags=["daten-roulette", "privat"], name="y.txt"),
            _file("crm", tags=["spiel-freigabe", "crm"], name="z.docx"),
        ]
        with patch("app.gamification_adapters.DocumentStore.list_documents", return_value=documents):
            self.assertEqual(document_candidates(self.root, "owner"), [])

    def test_file_adapter_does_not_duplicate_images(self):
        document = _file("image", tags=["gamification"], name="picture.jpg")
        with patch("app.gamification_adapters.DocumentStore.list_documents", return_value=[document]):
            self.assertEqual(document_candidates(self.root, "owner"), [])


if __name__ == "__main__":
    unittest.main()
