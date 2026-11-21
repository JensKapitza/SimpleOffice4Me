"""Compose validation for the isolated federation test network (issue #679).

Parses nothing external: the checks run on the committed Compose text so
they execute without Docker or network access.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "deploy/docker/compose.federation-test.yaml"
DOCKERFILE = REPO / "Dockerfile"
README = REPO / "deploy/docker/README.md"


class FederationTestnetComposeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = COMPOSE.read_text(encoding="utf-8")

    def test_exactly_three_isolated_services(self):
        services_block = self.compose.split("\nnetworks:", 1)[0].split("\nservices:", 1)[1]
        services = re.findall(r"^  ([a-z-]+):$", services_block, re.MULTILINE)
        self.assertEqual(sorted(services), ["peer-a", "peer-b", "relay"])

    def test_network_is_internal_without_published_ports_or_host_mode(self):
        self.assertIn("internal: true", self.compose)
        self.assertNotIn("network_mode:", self.compose)
        self.assertNotIn("ports:", self.compose)
        for service in ("peer-a", "peer-b", "relay"):
            self.assertNotIn(f"{service}-data:/var/lib/other", self.compose)

    def test_peers_have_separate_data_volumes_and_identities(self):
        for service, peer_id, base_url, local_token in (
            ("peer-a", "federation-test-peer-a", "http://peer-a:8080",
             "federation-test-only-peer-a-token"),
            ("peer-b", "federation-test-peer-b", "http://peer-b:8080",
             "federation-test-only-peer-b-token"),
            ("relay", "federation-test-relay", "http://relay:8080",
             "federation-test-only-global-token"),
        ):
            self.assertIn(f"{service}-data:/var/lib/simpleoffice4me", self.compose)
            self.assertIn(f"SIMPLEOFFICE_FEDERATION_PEER_ID: {peer_id}", self.compose)
            self.assertIn(f"SIMPLEOFFICE_FEDERATION_PUBLIC_URL: {base_url}", self.compose)
            self.assertIn(local_token, self.compose)

    def test_relay_tokens_are_distinct_per_peer(self):
        self.assertIn("SIMPLEOFFICE_FEDERATION_TEST_RELAY_TOKEN: relay-a", self.compose)
        self.assertIn("SIMPLEOFFICE_FEDERATION_TEST_RELAY_TOKEN: relay-b", self.compose)
        self.assertIn("SIMPLEOFFICE_FEDERATION_TEST_PEER_A_RELAY_TOKEN: relay-a", self.compose)
        self.assertIn("SIMPLEOFFICE_FEDERATION_TEST_PEER_B_RELAY_TOKEN: relay-b", self.compose)

    def test_test_only_values_are_pinned_without_secret_passthrough(self):
        self.assertIn("test-only", self.compose)
        self.assertNotIn("${SIMPLEOFFICE", self.compose)

    def test_image_healthcheck_exists_for_compose_wait(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("HEALTHCHECK", dockerfile)

    def test_operations_are_documented(self):
        readme = README.read_text(encoding="utf-8")
        for marker in (
            "compose.federation-test.yaml up",
            "federation_testnet_seed",
            "federation_testnet_smoke",
            "down --volumes",
        ):
            self.assertIn(marker, readme)


if __name__ == "__main__":
    unittest.main()
