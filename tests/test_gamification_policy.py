import unittest

from app.gamification_policy import GamePolicy, allowed_contact_fields, can_expose, is_hard_excluded


def policy(**kwargs):
    values = {"providers": frozenset({"images", "documents", "contacts"}), "preview_allowed": True}
    values.update(kwargs)
    return GamePolicy(**values)


class GamificationPolicyTests(unittest.TestCase):
    def test_normal_read_permission_is_always_required(self):
        self.assertFalse(can_expose(policy(), provider="images", actor="a", normal_read_allowed=False))

    def test_sensitive_classes_are_hard_excluded(self):
        for resource_class in ("invoice", "billing", "accounting", "payment", "crm-internal", "credential", "private note"):
            with self.subTest(resource_class=resource_class):
                self.assertTrue(is_hard_excluded(resource_class))
                self.assertFalse(can_expose(
                    policy(), provider="documents", actor="a",
                    normal_read_allowed=True, resource_class=resource_class,
                ))

    def test_federation_policy_cannot_bypass_peer_permission(self):
        p = policy(scope="federation")
        self.assertFalse(can_expose(
            p, provider="images", actor="friend", normal_read_allowed=True,
            federation_allowed=False,
        ))

    def test_organization_requires_membership_and_existing_read_access(self):
        p = policy(scope="organization", participants=frozenset({"employee"}))
        self.assertFalse(can_expose(p, provider="contacts", actor="employee", normal_read_allowed=True, organization_member=False))
        self.assertFalse(can_expose(p, provider="contacts", actor="employee", normal_read_allowed=False, organization_member=True))
        self.assertTrue(can_expose(p, provider="contacts", actor="employee", normal_read_allowed=True, organization_member=True))

    def test_contact_fields_are_allowlisted(self):
        p = policy(fields=frozenset({"phone", "street", "private_notes"}))
        self.assertEqual(allowed_contact_fields(p, ["phone", "street", "private_notes", "password"]), ("phone", "street"))
        self.assertFalse(can_expose(p, provider="contacts", actor="a", normal_read_allowed=True, field_name="private_notes"))

    def test_collection_and_participant_are_both_required_when_restricted(self):
        p = policy(collections=frozenset({"family"}), participants=frozenset({"jens"}))
        self.assertTrue(can_expose(p, provider="images", actor="jens", normal_read_allowed=True, collection="family"))
        self.assertFalse(can_expose(p, provider="images", actor="other", normal_read_allowed=True, collection="family"))
        self.assertFalse(can_expose(p, provider="images", actor="jens", normal_read_allowed=True, collection="work"))


if __name__ == "__main__":
    unittest.main()
