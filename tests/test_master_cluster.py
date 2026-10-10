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
                "master": {"is_master": True}, "fingerprint": "a" * 64,
            })) as open_profile:
                with patch.object(master_cluster.FederationIdentity, "public_identity", return_value={"fingerprint": "a" * 64}):
                    with patch.object(master_cluster, "resolve_authority_id", return_value="a" * 64):
                        result = master_cluster.inspect_master_address(self.root)
        self.assertEqual(result["role"], "master")
        self.assertEqual(result["reachability"], "reachable")
        self.assertEqual(result["observed_node_id"], "a" * 64)
        self.assertEqual(open_profile.call_args.args[0].full_url, "https://master.example/.well-known/simpleoffice-federation")

    def test_master_address_identifies_a_different_master_without_demoting_local_node(self):
        with patch.multiple(master_cluster, LICENSE_MASTER_MODE=True, LICENSE_MASTER_URL="https://master.example"):
            with patch.object(master_cluster, "_open_profile", return_value=self._response({
                "master": {"is_master": True}, "fingerprint": "b" * 64,
            })):
                with patch.object(master_cluster.FederationIdentity, "public_identity", return_value={"fingerprint": "a" * 64}):
                    with patch.object(master_cluster, "resolve_authority_id", return_value="a" * 64):
                        result = master_cluster.inspect_master_address(self.root)
        self.assertEqual(result["role"], "master")
        self.assertEqual(result["operating_state"], "active")
        self.assertEqual(result["observed_node_id"], "b" * 64)

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

    def _dns_response(self, answers, status=0):
        response = self._response({"Status": status, "Answer": answers})
        return response

    def test_txt_authority_record_requires_one_sha256_node_id(self):
        name = "_simpleoffice-master.example"
        answer = {"name": name + ".", "type": 16, "data": '"simpleoffice-master-id=' + "a" * 64 + '"'}
        opener = MagicMock()
        opener.open.return_value = self._dns_response([answer])
        with patch.object(master_cluster.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(master_cluster.resolve_authority_id(name), "a" * 64)
        self.assertIn("cloudflare-dns.com", opener.open.call_args.args[0].full_url)
        opener.open.return_value = self._dns_response([{"name": name + ".", "type": 16, "data": '"invalid"'}])
        with patch.object(master_cluster.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                master_cluster.resolve_authority_id(name)

    def test_standard_txt_name_can_be_saved_and_loaded(self):
        settings = master_cluster.MasterClusterSettings(self.root)
        name = master_cluster.default_txt_record_name("https://simpleoffice4me.back2heaven.de")
        expected = {"mode": "backup-active", "txt_record_name": name}
        self.assertEqual(expected, settings.save(expected, "admin"))
        self.assertEqual(expected, settings.load())

    def test_corrupt_settings_never_fall_back_to_active_active(self):
        settings = master_cluster.MasterClusterSettings(self.root)
        settings.path.parent.mkdir(parents=True, exist_ok=True)
        for content in ('{invalid', '[]', '{"mode":"invalid"}'):
            with self.subTest(content=content):
                settings.path.write_text(content)
                with patch.object(master_cluster, "LICENSE_MASTER_MODE", True), patch.object(master_cluster, "_open_profile") as probe:
                    result = master_cluster.inspect_master_address(self.root)
                self.assertEqual("standby_invalid_configuration", result["operating_state"])
                probe.assert_not_called()

    def test_invalid_profile_fingerprint_is_not_reachable_master(self):
        with patch.multiple(master_cluster, LICENSE_MASTER_MODE=True, LICENSE_MASTER_URL="https://master.example"), patch.object(master_cluster, "resolve_authority_id", return_value="a" * 64), patch.object(master_cluster, "_open_profile", return_value=self._response({"master": {"is_master": True}, "fingerprint": "invalid"})):
            result = master_cluster.inspect_master_address(self.root)
        self.assertEqual("unreachable", result["reachability"])

    def test_conflicting_txt_authorities_are_rejected(self):
        name = "_simpleoffice-master.example"
        answers = [
            {"name": name + ".", "type": 16, "data": '"simpleoffice-master-id=' + value + '"'}
            for value in ("a" * 64, "b" * 64)
        ]
        opener = MagicMock()
        opener.open.return_value = self._dns_response(answers)
        with patch.object(master_cluster.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                master_cluster.resolve_authority_id(name)

    def test_dns_failure_and_malformed_data_fail_closed(self):
        name = "_simpleoffice-master.example"
        opener = MagicMock()
        with patch.object(master_cluster.urllib.request, "build_opener", return_value=opener):
            for payload in ({"Status": 2, "Answer": []}, {"Status": 0, "Answer": "bad"}):
                opener.open.return_value = self._response(payload)
                with self.assertRaises(ValueError):
                    master_cluster.resolve_authority_id(name)
            opener.open.side_effect = OSError("offline")
            with self.assertRaises(OSError):
                master_cluster.resolve_authority_id(name)

    def test_oversized_profile_is_rejected(self):
        response = self._response({})
        response.read.return_value = b"x" * (master_cluster.MAX_PROFILE_BYTES + 1)
        with patch.multiple(master_cluster, LICENSE_MASTER_MODE=True, LICENSE_MASTER_URL="https://master.example"), patch.object(master_cluster, "resolve_authority_id", return_value="a" * 64), patch.object(master_cluster, "_open_profile", return_value=response):
            result = master_cluster.inspect_master_address(self.root)
        self.assertEqual("unreachable", result["reachability"])


if __name__ == "__main__":
    unittest.main()
