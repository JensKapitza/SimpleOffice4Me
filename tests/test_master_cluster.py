import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask

from app import master_cluster


class MasterClusterReachabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()
        self.temp.cleanup()

    @staticmethod
    def _response(profile):
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps(profile).encode("utf-8")
        return response

    def test_master_address_identifies_this_node_by_public_fingerprint(self):
        with patch.multiple(master_cluster, LICENSE_MASTER_MODE=True, LICENSE_MASTER_URL="https://master.example"):
            with patch.object(master_cluster, "_open_profile", return_value=self._response({
                "master": {"is_master": True}, "fingerprint": "node-fingerprint",
            })) as open_profile:
                with patch.object(master_cluster.FederationIdentity, "public_identity", return_value={"fingerprint": "node-fingerprint"}):
                    with patch.object(master_cluster, "resolve_authority_id", return_value="node-fingerprint"):
                        result = master_cluster.inspect_master_address(self.root)
        self.assertEqual(result["role"], "master")
        self.assertEqual(result["reachability"], "reachable")
        self.assertEqual(result["observed_node_id"], "node-fingerprint")
        self.assertEqual(open_profile.call_args.args[0].full_url, "https://master.example/.well-known/simpleoffice-federation")

    def test_master_address_identifies_a_different_master_without_demoting_local_node(self):
        with patch.multiple(master_cluster, LICENSE_MASTER_MODE=True, LICENSE_MASTER_URL="https://master.example"):
            with patch.object(master_cluster, "_open_profile", return_value=self._response({
                "master": {"is_master": True}, "fingerprint": "other-fingerprint",
            })):
                with patch.object(master_cluster.FederationIdentity, "public_identity", return_value={"fingerprint": "local-fingerprint"}):
                    with patch.object(master_cluster, "resolve_authority_id", return_value="local-fingerprint"):
                        result = master_cluster.inspect_master_address(self.root)
        self.assertEqual(result["role"], "master")
        self.assertEqual(result["operating_state"], "active")
        self.assertEqual(result["observed_node_id"], "other-fingerprint")

    def test_unreachable_address_keeps_active_active_writable(self):
        with patch.multiple(master_cluster, LICENSE_MASTER_MODE=True, LICENSE_MASTER_URL="https://master.example"):
            with patch.object(master_cluster, "_open_profile", side_effect=OSError("offline")):
                with patch.object(master_cluster, "resolve_authority_id", side_effect=OSError("offline")):
                    result = master_cluster.inspect_master_address(self.root)
        self.assertEqual(result["role"], "master")
        self.assertEqual(result["operating_state"], "active_partitioned")
        self.assertEqual(result["reachability"], "unreachable")

    def test_invalid_non_https_master_address_is_rejected_without_request(self):
        with patch.multiple(master_cluster, LICENSE_MASTER_MODE=True, LICENSE_MASTER_URL="http://master.example"):
            with patch.object(master_cluster, "_open_profile") as open_profile:
                result = master_cluster.inspect_master_address(self.root)
        self.assertEqual(result["reachability"], "unreachable")
        open_profile.assert_not_called()

    def test_round_robin_peer_does_not_demote_active_active_node(self):
        self.assertEqual(master_cluster._operating_state("active-active", "local", "authority", True), "active")

    def test_active_active_remains_operational_during_network_partition(self):
        self.assertEqual(master_cluster._operating_state("active-active", "local", None, False), "active_partitioned")

    def test_backup_active_requires_authority_record_and_reachability(self):
        self.assertEqual(master_cluster._operating_state("backup-active", "local", "local", True), "active")
        self.assertEqual(master_cluster._operating_state("backup-active", "local", "peer", True), "standby")
        self.assertEqual(master_cluster._operating_state("backup-active", "local", "local", False), "standby_unreachable")

    def test_permanent_master_stays_active_without_dns(self):
        self.assertEqual(master_cluster._operating_state("permanent-master", "local", None, False), "active")

    def test_settings_reject_unknown_modes_and_invalid_txt_names(self):
        with self.assertRaises(ValueError):
            master_cluster.MasterClusterSettings.validate({"mode": "unknown", "txt_record_name": "_cluster.example"})
        with self.assertRaises(ValueError):
            master_cluster.MasterClusterSettings.validate({"mode": "active-active", "txt_record_name": "bad..name"})

    def test_txt_authority_record_requires_one_sha256_node_id(self):
        fingerprint = "a" * 64
        answer = MagicMock()
        answer.strings = [f"simpleoffice-master-id={fingerprint}".encode("ascii")]
        with patch.object(master_cluster.dns.resolver.Resolver, "resolve", return_value=[answer]):
            with patch.object(master_cluster.dns.resolver.Resolver, "configure"):
                self.assertEqual(master_cluster.resolve_authority_id("_simpleoffice-master.example"), fingerprint)

        answer.strings = [b"simpleoffice-master-id=invalid"]
        with patch.object(master_cluster.dns.resolver.Resolver, "resolve", return_value=[answer]):
            with patch.object(master_cluster.dns.resolver.Resolver, "configure"):
                with self.assertRaises(ValueError):
                    master_cluster.resolve_authority_id("_simpleoffice-master.example")


if __name__ == "__main__":
    unittest.main()
