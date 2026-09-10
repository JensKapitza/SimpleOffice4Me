import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.gamification_document_selection import (
    document_candidate,
    query_document_candidates,
    set_document_game_release,
)


def _document(document_id="doc-1", *, tags=None, name="urlaub.txt", state="active"):
    return {
        "document_id": document_id,
        "last_path": f"archive/{name}",
        "tags": list(tags or []),
        "attributes": {},
        "state": state,
    }


class GamificationDocumentSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_persistent_candidate_still_requires_release_tag(self):
        store = MagicMock()
        store.get_document.return_value = _document(tags=["urlaub"])
        with patch("app.gamification_document_selection.DocumentStore", return_value=store):
            self.assertIsNone(document_candidate(self.root, "alice", "doc-1"))

    def test_session_candidate_does_not_require_persistent_release(self):
        store = MagicMock()
        store.get_document.return_value = _document(tags=["urlaub"], name="reiseplan.txt")
        with patch("app.gamification_document_selection.DocumentStore", return_value=store):
            candidate = document_candidate(self.root, "alice", "doc-1", require_release=False)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.object_ref, "document:doc-1")
        self.assertEqual(candidate.data, {"display_name": "reiseplan.txt"})

    def test_session_selection_keeps_hard_exclusions(self):
        store = MagicMock()
        store.get_document.return_value = _document(tags=["privat"], name="secret.txt")
        with patch("app.gamification_document_selection.DocumentStore", return_value=store):
            self.assertIsNone(document_candidate(self.root, "alice", "doc-1", require_release=False))

    def test_search_query_is_only_resolved_to_candidates(self):
        documents = {
            "one": _document("one", tags=["urlaub"], name="eins.txt"),
            "two": _document("two", tags=["projekt"], name="zwei.txt"),
        }
        store = MagicMock()
        store.search.return_value = [
            {"document_id": "one", "path": "archive/eins.txt", "state": "active"},
            {"document_id": "two", "path": "archive/zwei.txt", "state": "active"},
        ]
        store.get_document.side_effect = lambda value: documents[value]
        with patch("app.gamification_document_selection.DocumentStore", return_value=store):
            candidates = query_document_candidates(self.root, "alice", "tag:urlaub ODER tag:projekt")
        self.assertEqual([item.object_ref for item in candidates], ["document:one", "document:two"])
        store.set_tags.assert_not_called()

    def test_persistent_release_preserves_unrelated_tags(self):
        store = MagicMock()
        store.get_document.return_value = _document(tags=["urlaub", "familie"])
        store.set_tags.return_value = _document(tags=["urlaub", "familie", "daten-roulette"])
        with patch("app.gamification_document_selection.DocumentStore", return_value=store):
            set_document_game_release(self.root, "alice", "doc-1", True)
        written = store.set_tags.call_args.args[1]
        self.assertEqual(written, ["urlaub", "familie", "daten-roulette"])

    def test_persistent_unrelease_removes_all_roulette_aliases(self):
        store = MagicMock()
        store.get_document.return_value = _document(
            tags=["urlaub", "gamification", "daten-roulette", "spiel-freigabe"]
        )
        store.set_tags.return_value = _document(tags=["urlaub"])
        with patch("app.gamification_document_selection.DocumentStore", return_value=store):
            set_document_game_release(self.root, "alice", "doc-1", False)
        self.assertEqual(store.set_tags.call_args.args[1], ["urlaub"])


if __name__ == "__main__":
    unittest.main()
