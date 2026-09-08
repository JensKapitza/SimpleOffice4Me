from app.gamification_engine import Candidate, eligible_challenges, roulette
from app.gamification_policy import GamePolicy
from app.gamification_providers import get_provider


def test_roulette_never_exposes_invoice():
    policy = GamePolicy(providers=frozenset({"documents"}), participants=frozenset({"jens"}))
    candidates = [Candidate("documents", "doc:1", {"display_name": "invoice.pdf"}, True, resource_class="invoice")]
    assert roulette(policy, "jens", candidates, seed=1) is None


def test_roulette_requires_normal_read_access():
    policy = GamePolicy(providers=frozenset({"images"}))
    candidates = [Candidate("images", "img:1", {"preview_url": "/preview/1"}, False)]
    assert eligible_challenges(policy, "jens", candidates) == []


def test_contact_provider_only_builds_policy_fields():
    policy = GamePolicy(providers=frozenset({"contacts"}), fields=frozenset({"street", "phone", "private_note"}))
    candidate = Candidate("contacts", "contact:1", {"display_name": "Max"}, True)
    kinds = {challenge.kind for challenge in eligible_challenges(policy, "jens", [candidate])}
    assert kinds == {"street", "phone"}


def test_organization_roulette_needs_membership():
    policy = GamePolicy(scope="organization", providers=frozenset({"contacts"}), fields=frozenset({"email"}))
    candidate = Candidate("contacts", "contact:1", {"display_name": "Max"}, True, organization_member=False)
    assert roulette(policy, "employee", [candidate]) is None


def test_federation_roulette_needs_peer_permission():
    policy = GamePolicy(scope="federation", providers=frozenset({"images"}), preview_allowed=True)
    candidate = Candidate("images", "img:1", {"preview_url": "/preview/1"}, True, federation_allowed=False)
    assert roulette(policy, "friend", [candidate]) is None


def test_answer_validation_accepts_unknown_as_ui_skip_not_answer():
    provider = get_provider("images")
    challenge = provider.build_challenges("img:1", {})[0]
    assert provider.validate_answer(challenge, "2020") is True
    assert provider.validate_answer(challenge, "unknown") is False
