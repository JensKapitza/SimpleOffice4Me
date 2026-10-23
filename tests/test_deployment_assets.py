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

    def test_scanner_build_context_includes_required_workflows_but_excludes_local_data(self):
        dockerignore = self.text(".oss-scanner/Dockerfile.dockerignore")
        for item in (
            "!.github/workflows/android-apk-build.yml",
            "!.github/workflows/desktop-build.yml",
            "!.github/workflows/server-deb-build.yml",
            "!.github/workflows/project-release.yml",
            "!.github/workflows/ubuntu-26-compat.yml",
            ".git",
            ".venv",
            "instance",
            "database/documents",
            "database/files",
            "packaging/private",
            "packaging/*.secret.sh",
        ):
            with self.subTest(item=item):
                self.assertIn(item, dockerignore)

    def test_federation_compose_restart_smoke_is_wired_to_docker_ci(self):
        compose = self.text("deploy/docker/compose.federation-test.yaml")
        workflow = self.text(".github/workflows/ubuntu-26-compat.yml")
        self.assertIn("internal: true", compose)
        self.assertIn("peer-a-data:/var/lib/simpleoffice4me", compose)
        self.assertIn("peer-b-data:/var/lib/simpleoffice4me", compose)
        self.assertIn("relay-data:/var/lib/simpleoffice4me", compose)
        self.assertIn("SIMPLEOFFICE_FEDERATION_PEER_ID: federation-test-relay", compose)
        self.assertIn("load: true", workflow)
        self.assertIn("Run isolated federation restart smoke", workflow)
        self.assertIn('restart peer-b', workflow)
        for result in ("direct_trust_required", "pending", "duplicate"):
            self.assertIn(f"--expect-receive {result}", workflow)
        for action in ("denied", "send", "receive"):
            self.assertIn(f"--relay-action {action}", workflow)
        self.assertIn("--configure-relay-node", workflow)
        self.assertIn("--enable-test-relay", workflow)
        self.assertIn('restart peer-b relay', workflow)
        self.assertIn("down --volumes --remove-orphans", workflow)
        smoke = self.text("tools/federation_testnet_smoke.py")
        logging_config = 'os.environ.setdefault("SIMPLEOFFICE_LOG_STDERR_ONLY", "1")'
        self.assertLess(
            smoke.index(logging_config),
            smoke.index("from app.federation_peer_auth import headers as peer_headers"),
        )
        self.assertIn(
            '"direct_trust_required": (409, "rejected", "direct_trust_required")',
            smoke,
        )
        self.assertIn("relay recovery probe was not delivered exactly once", smoke)
        self.assertIn("rendezvous relay is not explicitly enabled", smoke)

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
