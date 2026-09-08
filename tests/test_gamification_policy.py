import pytest

from app.gamification_policy import GamePolicy, allowed_contact_fields, can_expose, is_hard_excluded


def policy(**kwargs):
    values = {"providers": frozenset({"images", "documents", "contacts"}), "preview_allowed": True}
    values.update(kwargs)
    return GamePolicy(**values)


def test_normal_read_permission_is_always_required():
    assert not can_expose(policy(), provider="images", actor="a", normal_read_allowed=False)


@pytest.mark.parametrize("resource_class", ["invoice", "billing", "accounting", "payment", "crm-internal", "credential", "private note"])
def test_sensitive_classes_are_hard_excluded(resource_class):
    assert is_hard_excluded(resource_class)
    assert not can_expose(policy(), provider="documents", actor="a", normal_read_allowed=True, resource_class=resource_class)


def test_federation_policy_cannot_bypass_peer_permission():
    p = policy(scope="federation")
    assert not can_expose(p, provider="images", actor="friend", normal_read_allowed=True, federation_allowed=False)


def test_organization_requires_membership_and_existing_read_access():
    p = policy(scope="organization", participants=frozenset({"employee"}))
    assert not can_expose(p, provider="contacts", actor="employee", normal_read_allowed=True, organization_member=False)
    assert not can_expose(p, provider="contacts", actor="employee", normal_read_allowed=False, organization_member=True)
    assert can_expose(p, provider="contacts", actor="employee", normal_read_allowed=True, organization_member=True)


def test_contact_fields_are_allowlisted():
    p = policy(fields=frozenset({"phone", "street", "private_notes"}))
    assert allowed_contact_fields(p, ["phone", "street", "private_notes", "password"]) == ("phone", "street")
    assert not can_expose(p, provider="contacts", actor="a", normal_read_allowed=True, field_name="private_notes")


def test_collection_and_participant_are_both_required_when_restricted():
    p = policy(collections=frozenset({"family"}), participants=frozenset({"jens"}))
    assert can_expose(p, provider="images", actor="jens", normal_read_allowed=True, collection="family")
    assert not can_expose(p, provider="images", actor="other", normal_read_allowed=True, collection="family")
    assert not can_expose(p, provider="images", actor="jens", normal_read_allowed=True, collection="work")
