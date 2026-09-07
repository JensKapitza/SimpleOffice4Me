import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LicenseBuildTests(unittest.TestCase):
    def test_source_build_defaults_to_no_external_master(self):
        source = (ROOT / "app" / "build_master.py").read_text(encoding="utf-8")
        self.assertIn('LICENSE_MASTER_URL = ""', source)
        self.assertIn('LICENSE_MASTER_PEER_ID = "license-master"', source)
        self.assertIn("LICENSE_MASTER_MODE = False", source)

    def test_fpm_build_freezes_master_into_staging_tree(self):
        script = (ROOT / "packaging" / "build-fpm.sh").read_text(encoding="utf-8")
        self.assertIn("SIMPLEOFFICE_BUILD_LICENSE_MASTER_URL", script)
        self.assertIn("SIMPLEOFFICE_BUILD_LICENSE_MASTER_PEER_ID", script)
        self.assertIn("SIMPLEOFFICE_BUILD_LICENSE_MASTER_MODE", script)
        self.assertIn('$APP_DIR/app/build_master.py', script)
        self.assertIn("Generated at package build time", script)
        self.assertIn("Changing it requires a new build", script)

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
