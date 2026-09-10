import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.gamification_engine import Candidate
from app.gamification_proposal_actions import (
    ProposalConflict,
    apply_contact_answer,
    apply_document_answer,
)
from app.gamification_providers import DocumentProvider


class GamificationProposalActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def document_candidate():
        return Candidate(
            provider="documents",
            object_ref="document:doc-1",
            data={"display_name": "notiz.txt"},
            normal_read_allowed=True,
            resource_class="released_file",
            collection="files",
        )

    def test_document_provider_offers_note_proposal(self):
        kinds = {
            challenge.kind
            for challenge in DocumentProvider().build_challenges(
                "document:doc-1", {"display_name": "notiz.txt"}
            )
        }
        self.assertIn("tags", kinds)
        self.assertIn("note", kinds)

    def test_tag_proposal_adds_without_replacing_existing_tags(self):
        proposal = {
            "provider": "documents",
            "object_ref": "document:doc-1",
            "field_name": "tags",
            "value": "Urlaub, familie, Urlaub",
            "accepted_by": None,
        }
        document = {"document_id": "doc-1", "tags": ["bestehend", "Familie"]}
        with patch(
            "app.gamification_proposal_actions.document_candidate_from_object_ref",
            return_value=self.document_candidate(),
        ), patch(
            "app.gamification_proposal_actions.DocumentStore.get_document",
            return_value=document,
        ), patch(
            "app.gamification_proposal_actions.DocumentStore.set_tags"
        ) as set_tags:
            message = apply_document_answer(self.root, "owner", proposal)
        set_tags.assert_called_once_with("doc-1", ["bestehend", "Familie", "Urlaub"], "owner")
        self.assertIn("1 Tag", message)

    def test_note_proposal_appends_document_note(self):
        proposal = {
            "provider": "documents",
            "object_ref": "document:doc-1",
            "field_name": "note",
            "value": "Gehört zur Reiseplanung 2026",
            "accepted_by": None,
        }
        with patch(
            "app.gamification_proposal_actions.document_candidate_from_object_ref",
            return_value=self.document_candidate(),
        ), patch(
            "app.gamification_proposal_actions.DocumentStore.add_note"
        ) as add_note:
            message = apply_document_answer(self.root, "owner", proposal)
        add_note.assert_called_once_with(
            "doc-1", "Gehört zur Reiseplanung 2026", "owner"
        )
        self.assertIn("Notiz", message)

    def test_contact_conflict_requires_explicit_replace(self):
        proposal = {
            "provider": "contacts",
            "object_ref": "contact:c-1",
            "field_name": "city",
            "value": "Duisburg",
            "accepted_by": None,
        }
        candidate = Candidate(
            provider="contacts",
            object_ref="contact:c-1",
            data={"display_name": "Test", "existing_fields": ("city",)},
            normal_read_allowed=True,
            resource_class="contact",
            collection="contacts",
        )
        contact = {"contact_id": "c-1", "fields": {"display_name": "Test", "city": "Essen"}}
        with patch(
            "app.gamification_proposal_actions.contact_candidates", return_value=[candidate]
        ), patch(
            "app.gamification_proposal_actions.ContactStore.get", return_value=contact
        ), patch(
            "app.gamification_proposal_actions.ContactStore.can_manage_contact", return_value=True
        ), patch(
            "app.gamification_proposal_actions.ContactStore.patch_fields"
        ) as patch_fields:
            with self.assertRaises(ProposalConflict):
                apply_contact_answer(self.root, "owner", proposal)
            patch_fields.assert_not_called()
            apply_contact_answer(self.root, "owner", proposal, replace_existing=True)
            patch_fields.assert_called_once_with("c-1", {"city": "Duisburg"}, "owner")


if __name__ == "__main__":
    unittest.main()
