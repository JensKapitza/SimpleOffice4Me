import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.gamification_store import GamificationStore


class GamificationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def store(self):
        return GamificationStore(self.root)

    def test_independent_votes_and_consensus(self):
        store = self.store()
        session = store.create_session("Familienrunde", "local", "owner", {"providers": ["images"]})
        item = store.add_item(session, "images", "image:123")
        proposal = store.propose(item, "tag", "Wald", "a")
        store.vote(proposal, "a", True)
        store.vote(proposal, "b", True)
        store.vote(proposal, "c", True)
        store.vote(proposal, "d", False)
        result = store.consensus(proposal)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["approvals"], 3)
        self.assertEqual(result["ratio"], 0.75)
        self.assertTrue(result["reached"])

    def test_second_vote_replaces_first_instead_of_counting_twice(self):
        store = self.store()
        session = store.create_session("Runde", "local", "owner", {})
        item = store.add_item(session, "contacts", "contact:1")
        proposal = store.propose(item, "city", "Koeln", "owner")
        store.vote(proposal, "person", False)
        store.vote(proposal, "person", True)
        result = store.consensus(proposal, min_votes=1)
        self.assertEqual(result, {"total": 1, "approvals": 1, "ratio": 1.0, "reached": True})

    def test_session_creation_is_audited(self):
        store = self.store()
        session = store.create_session("Runde", "organization", "admin", {})
        with sqlite3.connect(store.path) as db:
            row = db.execute("SELECT actor, action FROM game_audit WHERE session_id=?", (session,)).fetchone()
        self.assertEqual(row, ("admin", "session.created"))

    def test_challenge_is_bound_to_session_creator_and_becomes_proposal(self):
        store = self.store()
        session = store.create_session("Lokale Runde", "local", "owner", {})
        item = store.add_item(session, "contacts", "contact:1")
        challenge = store.add_challenge(item, "city", "text", "Welcher Ort?", {"display_name": "Test"})
        self.assertIsNone(store.get_challenge_for_actor(challenge, "stranger"))
        self.assertEqual(store.get_challenge_for_actor(challenge, "owner")["kind"], "city")
        proposal = store.answer_challenge(challenge, "owner", "Duisburg")
        self.assertTrue(proposal)
        self.assertIsNone(store.get_challenge_for_actor(challenge, "owner"))
        with sqlite3.connect(store.path) as db:
            row = db.execute(
                "SELECT p.field_name, p.value_json, c.status, c.answered_by "
                "FROM annotation_proposal p JOIN game_challenge c ON c.item_id=p.item_id "
                "WHERE p.id=? AND c.id=?",
                (proposal, challenge),
            ).fetchone()
        self.assertEqual(row, ("city", '"Duisburg"', "answered", "owner"))

    def test_challenge_cannot_be_answered_by_other_actor(self):
        store = self.store()
        session = store.create_session("Lokale Runde", "local", "owner", {})
        item = store.add_item(session, "documents", "document:1")
        challenge = store.add_challenge(item, "tags", "tags", "Tags?")
        with self.assertRaises(ValueError):
            store.answer_challenge(challenge, "stranger", "Vertrag")

    def test_unknown_consumes_challenge_without_proposal_and_is_audited(self):
        store = self.store()
        session = store.create_session("Lokale Runde", "local", "owner", {})
        item = store.add_item(session, "contacts", "contact:1")
        challenge = store.add_challenge(item, "phone", "text", "Telefon?")
        store.skip_challenge(challenge, "owner", disposition="unknown")
        self.assertIsNone(store.get_challenge_for_actor(challenge, "owner"))
        with sqlite3.connect(store.path) as db:
            status = db.execute("SELECT status, answered_by FROM game_challenge WHERE id=?", (challenge,)).fetchone()
            proposals = db.execute("SELECT COUNT(*) FROM annotation_proposal WHERE item_id=?", (item,)).fetchone()[0]
            audit = db.execute("SELECT action FROM game_audit WHERE session_id=? ORDER BY id DESC LIMIT 1", (session,)).fetchone()
        self.assertEqual(status, ("unknown", "owner"))
        self.assertEqual(proposals, 0)
        self.assertEqual(audit, ("challenge.unknown",))

    def test_unknown_cannot_be_submitted_by_other_actor(self):
        store = self.store()
        session = store.create_session("Lokale Runde", "local", "owner", {})
        item = store.add_item(session, "contacts", "contact:1")
        challenge = store.add_challenge(item, "email", "text", "E-Mail?")
        with self.assertRaises(ValueError):
            store.skip_challenge(challenge, "stranger", disposition="unknown")

    def test_same_proposal_is_idempotent(self):
        store = self.store()
        session = store.create_session("Lokale Runde", "local", "owner", {})
        item = store.add_item(session, "contacts", "contact:1")
        first = store.propose(item, "city", "Duisburg", "owner")
        second = store.propose(item, "city", "Duisburg", "owner")
        self.assertEqual(first, second)
        with sqlite3.connect(store.path) as db:
            count = db.execute("SELECT COUNT(*) FROM annotation_proposal WHERE item_id=?", (item,)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_proposal_review_is_bound_to_local_session_creator(self):
        store = self.store()
        session = store.create_session("Lokale Runde", "local", "owner", {})
        item = store.add_item(session, "contacts", "contact:1")
        proposal = store.propose(item, "city", "Duisburg", "owner")
        visible = store.get_proposal_for_actor(proposal, "owner")
        self.assertIsNotNone(visible)
        self.assertEqual(visible["provider"], "contacts")
        self.assertEqual(visible["object_ref"], "contact:1")
        self.assertEqual(visible["value"], "Duisburg")
        self.assertIsNone(store.get_proposal_for_actor(proposal, "stranger"))

    def test_non_local_proposal_is_not_available_to_local_review(self):
        store = self.store()
        session = store.create_session("Organisation", "organization", "owner", {})
        item = store.add_item(session, "contacts", "contact:1")
        proposal = store.propose(item, "city", "Duisburg", "owner")
        self.assertIsNone(store.get_proposal_for_actor(proposal, "owner"))


if __name__ == "__main__":
    unittest.main()
