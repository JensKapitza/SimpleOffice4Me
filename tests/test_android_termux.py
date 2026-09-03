import unittest
from pathlib import Path

from tools.launcher import osm_index_enabled


ROOT = Path(__file__).resolve().parents[1]


class AndroidTermuxTestCase(unittest.TestCase):
    def test_android_scripts_are_syntax_valid(self):
        import subprocess
        for path in (
            ROOT / "start.sh",
            ROOT / "start-sftp.sh",
            ROOT / "android" / "setup-termux.sh",
            ROOT / "android" / "simpleoffice-termux.sh",
        ):
            subprocess.run(["bash", "-n", str(path)], check=True)

    def test_phone_server_is_local_and_disables_heavy_workers(self):
        script = (ROOT / "android" / "simpleoffice-termux.sh").read_text(encoding="utf-8")
        self.assertIn("SIMPLEOFFICE_HOST=127.0.0.1", script)
        self.assertIn("SIMPLEOFFICE_BACKGROUND_INDEX=0", script)
        self.assertIn("SIMPLEOFFICE_OSM_INDEX=0", script)
        self.assertIn("SIMPLEOFFICE_DATALOGGER=0", script)

    def test_start_script_reuses_termux_native_web_python_packages(self):
        script = (ROOT / "start.sh").read_text(encoding="utf-8")
        self.assertIn("python-cryptography", script)
        self.assertIn("python-pillow", script)
        self.assertNotIn("python-bcrypt", script)
        self.assertNotIn("python-pynacl", script)
        self.assertIn("--system-site-packages", script)
        self.assertIn("--no-deps --editable", script)
        self.assertIn("venv_uses_system_site_packages", script)

    def test_sftp_script_owns_termux_native_sftp_dependencies(self):
        script = (ROOT / "start-sftp.sh").read_text(encoding="utf-8")
        self.assertIn("python-bcrypt", script)
        self.assertIn("python-pynacl", script)
        self.assertIn("python-cryptography", script)
        self.assertIn("python-pillow", script)
        self.assertIn("--system-site-packages", script)
        self.assertIn("--only-binary=:all: --no-deps 'paramiko>=3.5,<6'", script)
        self.assertIn("pip check", script)

    def test_osm_worker_can_be_disabled(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"SIMPLEOFFICE_OSM_INDEX": "0"}):
            self.assertFalse(osm_index_enabled())
