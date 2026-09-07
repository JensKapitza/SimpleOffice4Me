import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LicenseBuildTests(unittest.TestCase):
    def test_source_build_defaults_to_no_external_master(self):
        source = (ROOT / "app" / "build_master.py").read_text(encoding="utf-8")
        self.assertIn('LICENSE_MASTER_URL = ""', source)
        self.assertIn('LICENSE_MASTER_PEER_ID = "license-master"', source)
        self.assertIn("LICENSE_MASTER_MODE = False", source)

    def test_client_and_server_builds_are_explicitly_separated(self):
        client = (ROOT / "packaging" / "build-client.sh").read_text(encoding="utf-8")
        server = (ROOT / "packaging" / "build-server.sh").read_text(encoding="utf-8")
        backend = (ROOT / "packaging" / "build-fpm.sh").read_text(encoding="utf-8")
        self.assertIn("SIMPLEOFFICE_BUILD_ROLE=client", client)
        self.assertIn("SIMPLEOFFICE_BUILD_LICENSE_MASTER_MODE=0", client)
        self.assertIn("build-client.local.sh", client)
        self.assertIn("SIMPLEOFFICE_BUILD_ROLE=server", server)
        self.assertIn("SIMPLEOFFICE_BUILD_LICENSE_MASTER_MODE=1", server)
        self.assertIn("build-server.local.sh", server)
        self.assertIn('case "$BUILD_ROLE" in', backend)
        self.assertIn("build-client.sh", backend)
        self.assertIn("build-server.sh", backend)

    def test_fpm_build_freezes_only_non_secret_master_identity(self):
        script = (ROOT / "packaging" / "build-fpm.sh").read_text(encoding="utf-8")
        self.assertIn("SIMPLEOFFICE_BUILD_LICENSE_MASTER_URL", script)
        self.assertIn("SIMPLEOFFICE_BUILD_LICENSE_MASTER_PEER_ID", script)
        self.assertIn('$APP_DIR/app/build_master.py', script)
        self.assertIn("Generated at package build time", script)
        self.assertNotIn("SIMPLEOFFICE_LICENSE_MASTER_TOKEN=", script)
        self.assertIn("Tokens/Passwoerter wurden nicht in das Paket eingebettet", script)

    def test_local_build_configuration_is_ignored_and_excluded_from_packages(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        backend = (ROOT / "packaging" / "build-fpm.sh").read_text(encoding="utf-8")
        for name in ("build-client.local.sh", "build-server.local.sh"):
            self.assertIn(f"packaging/{name}", ignored)
            self.assertIn(f"--exclude='./packaging/{name}'", backend)
        self.assertIn("packaging/*.secret.sh", ignored)
        self.assertIn("packaging/private/", ignored)

    def test_example_build_configs_contain_no_secret_fields(self):
        for path in (
            ROOT / "packaging" / "build-client.local.sh.example",
            ROOT / "packaging" / "build-server.local.sh.example",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("TOKEN=", source)
            self.assertNotIn("PASSWORD=", source)
            self.assertIn("KEINE Tokens", source)

    def test_runtime_secret_helper_prompts_without_command_line_token(self):
        source = (ROOT / "packaging" / "configure-license-secret.sh").read_text(encoding="utf-8")
        self.assertIn("read -r -s TOKEN", source)
        self.assertIn("SIMPLEOFFICE_LICENSE_MASTER_TOKEN", source)
        self.assertNotIn("$1", source)
        self.assertIn("chmod 0640", source)

    def test_runtime_master_url_is_not_read_from_environment(self):
        source = (ROOT / "app" / "build_master.py").read_text(encoding="utf-8")
        routes = (ROOT / "app" / "license_routes.py").read_text(encoding="utf-8")
        self.assertNotIn("os.environ", source)
        self.assertNotIn("SIMPLEOFFICE_LICENSE_MASTER_URL", routes)
        self.assertIn("SIMPLEOFFICE_LICENSE_MASTER_TOKEN", routes)

    def test_blocked_client_is_visible_and_federation_can_refuse_it(self):
        layout = (ROOT / "templates" / "layout.html").read_text(encoding="utf-8")
        routes = (ROOT / "app" / "license_routes.py").read_text(encoding="utf-8")
        self.assertIn("license-blocked-banner", layout)
        self.assertIn("licenseBlockedModal", layout)
        self.assertIn("SIMPLEOFFICE_FEDERATION_REFUSE_BAD_CLIENT", routes)
        self.assertIn("X-SimpleOffice-Client-State", routes)
        self.assertIn("bad_client_refused", routes)


if __name__ == "__main__":
    unittest.main()
