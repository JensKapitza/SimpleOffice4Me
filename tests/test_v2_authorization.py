import tempfile
import time
import unittest
from pathlib import Path

from app.v2.authorization import AuthorizationStore, GrantRight


class V2AuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AuthorizationStore(Path(self.temp.name))
        self.expires = int(time.time()) + 3600

    def tearDown(self):
        self.temp.cleanup()

    def test_relay_right_does_not_imply_read_right(self):
        grant = self.store.issue_root(
            issuer="controller-a",
            subject="relay-b",
            rights={GrantRight.RELAY, GrantRight.STORE},
            object_refs={"object-1"},
            expires_at=self.expires,
        )
        self.assertTrue(
            self.store.allows(
                grant.grant_id,
                subject="relay-b",
                right=GrantRight.RELAY,
                object_ref="object-1",
            )
        )
        self.assertFalse(
            self.store.allows(
                grant.grant_id,
                subject="relay-b",
                right=GrantRight.READ,
                object_ref="object-1",
            )
        )

    def test_delegation_cannot_expand_rights_scope_or_expiry(self):
        parent = self.store.issue_root(
            issuer="controller-a",
            subject="peer-b",
            rights={GrantRight.RELAY, GrantRight.STORE, GrantRight.DELEGATE},
            object_refs={"object-1", "object-2"},
            expires_at=self.expires,
        )
        child = self.store.delegate(
            parent.grant_id,
            issuer="peer-b",
            subject="peer-c",
            rights={GrantRight.STORE},
            object_refs={"object-1"},
            expires_at=self.expires - 60,
        )
        self.assertTrue(self.store.is_effective(child.grant_id))

        with self.assertRaises(ValueError):
            self.store.delegate(
                parent.grant_id,
                issuer="peer-b",
                subject="peer-c",
                rights={GrantRight.READ},
                object_refs={"object-1"},
                expires_at=self.expires - 60,
            )
        with self.assertRaises(ValueError):
            self.store.delegate(
                parent.grant_id,
                issuer="peer-b",
                subject="peer-c",
                rights={GrantRight.STORE},
                object_refs={"object-outside-scope"},
                expires_at=self.expires - 60,
            )

    def test_redelegation_is_not_automatic(self):
        parent = self.store.issue_root(
            issuer="controller-a",
            subject="peer-b",
            rights={GrantRight.STORE, GrantRight.DELEGATE},
            object_refs={"object-1"},
            expires_at=self.expires,
        )
        with self.assertRaises(ValueError):
            self.store.delegate(
                parent.grant_id,
                issuer="peer-b",
                subject="peer-c",
                rights={GrantRight.STORE, GrantRight.DELEGATE},
                object_refs={"object-1"},
                expires_at=self.expires - 60,
            )

    def test_revoking_parent_invalidates_descendants(self):
        parent = self.store.issue_root(
            issuer="controller-a",
            subject="peer-b",
            rights={GrantRight.STORE, GrantRight.DELEGATE},
            object_refs={"object-1"},
            expires_at=self.expires,
        )
        child = self.store.delegate(
            parent.grant_id,
            issuer="peer-b",
            subject="peer-c",
            rights={GrantRight.STORE},
            object_refs={"object-1"},
            expires_at=self.expires - 60,
        )
        self.store.revoke(parent.grant_id)
        self.assertFalse(self.store.is_effective(child.grant_id))


if __name__ == "__main__":
    unittest.main()
