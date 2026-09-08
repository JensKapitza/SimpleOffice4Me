import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentAssetsTest(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_standard_compose_is_not_privileged_and_keeps_web_local_by_default(self):
        compose = self.text("deploy/docker/compose.yaml")
        self.assertNotIn("privileged: true", compose)
        self.assertIn("127.0.0.1", compose)
        self.assertIn("cap_drop:\n      - ALL", compose)
        self.assertIn("SIMPLEOFFICE_HOST: 0.0.0.0", compose)
        self.assertIn("simpleoffice-data:/var/lib/simpleoffice4me", compose)

    def test_lan_worker_uses_host_network_and_scoped_capabilities(self):
        compose = self.text("deploy/docker/compose.lan.yaml")
        self.assertIn("network_mode: host", compose)
        self.assertNotIn("privileged: true", compose)
        for capability in ("DAC_OVERRIDE", "NET_BIND_SERVICE", "NET_RAW", "NET_ADMIN"):
            self.assertIn(f"- {capability}", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn("no-new-privileges:true", compose)

    def test_container_does_not_bake_runtime_secrets(self):
        dockerfile = self.text("Dockerfile")
        self.assertNotIn("SIMPLEOFFICE_FEDERATION_TOKEN=", dockerfile)
        self.assertNotIn("SIMPLEOFFICE_FEDERATION_PEER_ID=", dockerfile)
        self.assertIn("gosu", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)

    def test_deployment_documentation_covers_all_supported_modes(self):
        docs = self.text("docs/DEPLOYMENT.md")
        for marker in (
            "Docker Standard",
            "Docker LAN",
            "Linux-VM",
            "Native Debian/Ubuntu-Installation",
            "SIMPLEOFFICE_TRUSTED_PROXY_HOPS",
            "net.ipv4.ip_forward=1",
            "Bridge/External Network",
        ):
            self.assertIn(marker, docs)
        self.assertIn("nicht pauschal mit `--privileged`", docs)


if __name__ == "__main__":
    unittest.main()
