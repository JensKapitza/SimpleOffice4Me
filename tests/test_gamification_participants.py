import tempfile
import unittest
from pathlib import Path

from app.gamification_store import GamificationStore


class GamificationParticipantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = GamificationStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_federation_peer_gets_only_explicitly_shared_session(self):
        session = self.store.create_session("Familienrunde", "federation", "owner", {})
        item = self.store.add_item(session, "images", "document:one", "photo")
        challenge = self.store.add_challenge(item, "year", "year", "Welches Jahr?", {"preview": True})
        peer = "peer:family"
        self.assertIsNone(self.store.get_challenge_for_actor(challenge, peer))
        self.store.add_participant(session, peer, actor="owner")
        visible = self.store.get_challenge_for_actor(challenge, peer)
        self.assertIsNotNone(visible)
        self.assertEqual(visible["provider"], "images")
        proposal = self.store.answer_challenge(challenge, peer, "2020")
        self.assertIsNone(self.store.get_proposal_for_actor(proposal, peer))
        federation = self.store.get_proposal_for_actor(proposal, peer, scope="federation")
        self.assertIsNotNone(federation)
        self.assertEqual(federation["value"], "2020")

    def test_participant_cannot_add_itself_to_arbitrary_session(self):
        session = self.store.create_session("Familienrunde", "federation", "owner", {})
        with self.assertRaises(ValueError):
            self.store.add_participant(session, "peer:evil", actor="peer:evil")

    def test_review_only_participant_cannot_answer(self):
        session = self.store.create_session("Prüfrunde", "organization", "owner", {})
        item = self.store.add_item(session, "contacts", "contact:one", "contact")
        challenge = self.store.add_challenge(item, "city", "text", "Ort?")
        self.store.add_participant(session, "reviewer", role="review", actor="owner")
        self.assertIsNone(self.store.get_challenge_for_actor(challenge, "reviewer"))
        proposal = self.store.propose(item, "city", "Duisburg", "owner")
        self.assertIsNotNone(self.store.get_proposal_for_actor(proposal, "reviewer", scope="organization"))


if __name__ == "__main__":
    unittest.main()
