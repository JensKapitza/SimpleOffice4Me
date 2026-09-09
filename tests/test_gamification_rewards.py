import tempfile
import unittest
from pathlib import Path

from app.gamification_rewards import award_accepted_proposal, profile
from app.gamification_store import GamificationStore


class GamificationRewardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = GamificationStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def proposal(self, source="human"):
        session = self.store.create_session("Runde", "local", "owner", {})
        item = self.store.add_item(session, "documents", "document:1")
        return self.store.propose(item, "tags", "Wald", "player", source=source)

    def test_unaccepted_proposal_gets_no_xp(self):
        proposal = self.proposal()
        self.assertFalse(award_accepted_proposal(self.store, proposal))
        self.assertEqual(profile(self.store, "player")["xp"], 0)

    def test_accepted_human_proposal_gets_xp_once(self):
        proposal = self.proposal()
        self.store.accept(proposal, "owner")
        self.assertTrue(award_accepted_proposal(self.store, proposal))
        self.assertFalse(award_accepted_proposal(self.store, proposal))
        result = profile(self.store, "player")
        self.assertEqual(result["xp"], 10)
        self.assertEqual(result["accepted_fixes"], 1)
        self.assertIn("first_fix", {item["id"] for item in result["achievements"]})

    def test_ai_proposal_never_gets_reward(self):
        proposal = self.proposal(source="ai")
        self.store.accept(proposal, "owner")
        self.assertFalse(award_accepted_proposal(self.store, proposal))
        self.assertEqual(profile(self.store, "player")["xp"], 0)


if __name__ == "__main__":
    unittest.main()
