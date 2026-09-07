import unittest

from app.printershare import _peer_retention_ceiling
from app.printershare_store import effective_retention


class PrinterSharePeerPolicyTest(unittest.TestCase):
    def test_print_enabled_without_storage_policy_defaults_to_no_store(self):
        peer = {"policy": {"printing": {"send": True}}}
        ceiling = _peer_retention_ceiling(peer)
        self.assertEqual(ceiling, "no_store")
        self.assertEqual(effective_retention("permanent", ceiling), "no_store")

    def test_admin_can_explicitly_allow_ttl_or_permanent_remote_storage(self):
        ttl_peer = {"policy": {"printing": {"send": True, "retention_ceiling": "ttl"}}}
        permanent_peer = {"policy": {"printing": {"send": True, "retention_ceiling": "permanent"}}}
        self.assertEqual(_peer_retention_ceiling(ttl_peer), "ttl")
        self.assertEqual(_peer_retention_ceiling(permanent_peer), "permanent")

    def test_invalid_peer_storage_policy_fails_safe(self):
        peer = {"policy": {"printing": {"send": True, "retention_ceiling": "forever"}}}
        self.assertEqual(_peer_retention_ceiling(peer), "no_store")


if __name__ == "__main__":
    unittest.main()
