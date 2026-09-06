import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FpmPackagingTests(unittest.TestCase):
    def test_builder_uses_fpm_and_offline_python_wheels(self):
        script = (ROOT / "packaging" / "build-fpm.sh").read_text(encoding="utf-8")
        self.assertIn("python3 -m pip wheel", script)
        self.assertIn("fpm \\", script)
        self.assertIn("--depends \"python3 (>= 3.10)\"", script)
        self.assertIn("--depends \"python3-venv\"", script)
        self.assertIn("--depends \"git\"", script)
        self.assertIn("--after-install", script)
        self.assertIn("--config-files \"/etc/simpleoffice4me/simpleoffice.env\"", script)
        self.assertIn("--exclude='./instance'", script)
        self.assertIn("--exclude='./database/*.sqlite'", script)

    def test_build_dep_installs_complete_debian_build_environment(self):
        script = (ROOT / "build-dep.sh").read_text(encoding="utf-8")
        self.assertIn("apt-get update", script)
        self.assertIn("apt-get install", script)
        for package in (
            "python3-dev",
            "python3-pip",
            "python3-venv",
            "ruby-dev",
            "build-essential",
            "pkg-config",
            "libffi-dev",
            "libssl-dev",
            "libjpeg-dev",
            "zlib1g-dev",
            "rustc",
            "cargo",
            "dpkg-dev",
        ):
            self.assertIn(package, script)
        self.assertIn("gem install --no-document fpm", script)
        self.assertIn("python3 -c", script)
        self.assertIn("Python >= 3.10", script)

    def test_postinst_installs_without_pypi_and_preserves_state(self):
        postinst = (ROOT / "packaging" / "postinst.sh").read_text(encoding="utf-8")
        self.assertIn("--no-index", postinst)
        self.assertIn("--find-links", postinst)
        self.assertIn("/var/lib/simpleoffice4me", postinst)
        self.assertIn("ln -s \"$INSTANCE_DIR\" \"$APP_DIR/instance\"", postinst)
        postrm = (ROOT / "packaging" / "postrm.sh").read_text(encoding="utf-8")
        self.assertNotIn("rm -rf /var/lib/simpleoffice4me", postrm)

    def test_systemd_service_is_non_root_and_hardened(self):
        service = (ROOT / "packaging" / "simpleoffice4me.service").read_text(encoding="utf-8")
        self.assertIn("User=simpleoffice", service)
        self.assertIn("Group=simpleoffice", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("EnvironmentFile=-/etc/simpleoffice4me/simpleoffice.env", service)

    def test_wrapper_defaults_to_start_without_losing_arguments(self):
        wrapper = (ROOT / "packaging" / "simpleoffice4me-wrapper.sh").read_text(encoding="utf-8")
        self.assertIn('[ "$#" -gt 0 ] || set -- start', wrapper)
        self.assertIn('tools.launcher "$@"', wrapper)


if __name__ == "__main__":
    unittest.main()
