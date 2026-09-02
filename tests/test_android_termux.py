import unittest
from pathlib import Path

from tools.launcher import osm_index_enabled


ROOT = Path(__file__).resolve().parents[1]


class AndroidTermuxTestCase(unittest.TestCase):
    def test_android_scripts_are_syntax_valid(self):
        import subprocess
        for path in (ROOT / "android" / "setup-termux.sh", ROOT / "android" / "simpleoffice-termux.sh"):
            subprocess.run(["bash", "-n", str(path)], check=True)

    def test_phone_server_is_local_and_disables_heavy_workers(self):
        script = (ROOT / "android" / "simpleoffice-termux.sh").read_text(encoding="utf-8")
        self.assertIn("SIMPLEOFFICE_HOST=127.0.0.1", script)
        self.assertIn("SIMPLEOFFICE_BACKGROUND_INDEX=0", script)
        self.assertIn("SIMPLEOFFICE_OSM_INDEX=0", script)
        self.assertIn("SIMPLEOFFICE_DATALOGGER=0", script)

    def test_osm_worker_can_be_disabled(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"SIMPLEOFFICE_OSM_INDEX": "0"}):
            self.assertFalse(osm_index_enabled())
