import unittest

from app.gamification_engine import Candidate, eligible_challenges, roulette
from app.gamification_policy import GamePolicy
from app.gamification_providers import get_provider


class GamificationProviderTests(unittest.TestCase):
    def test_roulette_never_exposes_invoice(self):
        policy = GamePolicy(providers=frozenset({"documents"}), participants=frozenset({"jens"}))
        candidates = [Candidate("documents", "doc:1", {"display_name": "invoice.pdf"}, True, resource_class="invoice")]
        self.assertIsNone(roulette(policy, "jens", candidates, seed=1))

    def test_roulette_requires_normal_read_access(self):
        policy = GamePolicy(providers=frozenset({"images"}), collections=frozenset({"images"}), preview_allowed=True)
        candidates = [Candidate("images", "img:1", {"preview": True}, False, resource_class="photo", collection="images")]
        self.assertEqual(eligible_challenges(policy, "jens", candidates), [])

    def test_image_provider_requires_server_verified_preview(self):
        provider = get_provider("images")
        self.assertEqual(provider.build_challenges("img:1", {}), [])
        challenges = provider.build_challenges("img:1", {"preview": True})
        self.assertEqual({item.kind for item in challenges}, {"year", "tags", "place"})
        self.assertTrue(all(item.payload == {"preview": True} for item in challenges))
        self.assertNotIn("img:1", str([item.payload for item in challenges]))

    def test_contact_provider_only_builds_policy_fields(self):
        policy = GamePolicy(providers=frozenset({"contacts"}), fields=frozenset({"street", "phone", "private_note"}))
        candidate = Candidate("contacts", "contact:1", {"display_name": "Max"}, True)
        kinds = {challenge.kind for challenge in eligible_challenges(policy, "jens", [candidate])}
        self.assertEqual(kinds, {"street", "phone"})

    def test_organization_roulette_needs_membership(self):
        policy = GamePolicy(scope="organization", providers=frozenset({"contacts"}), fields=frozenset({"email"}))
        candidate = Candidate("contacts", "contact:1", {"display_name": "Max"}, True, organization_member=False)
        self.assertIsNone(roulette(policy, "employee", [candidate]))

    def test_federation_roulette_needs_peer_permission(self):
        policy = GamePolicy(scope="federation", providers=frozenset({"images"}), preview_allowed=True)
        candidate = Candidate("images", "img:1", {"preview": True}, True, federation_allowed=False)
        self.assertIsNone(roulette(policy, "friend", [candidate]))

    def test_answer_validation_accepts_unknown_as_ui_skip_not_answer(self):
        provider = get_provider("images")
        challenge = provider.build_challenges("img:1", {"preview": True})[0]
        self.assertTrue(provider.validate_answer(challenge, "2020"))
        self.assertFalse(provider.validate_answer(challenge, "unknown"))


if __name__ == "__main__":
    unittest.main()
