import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.osm_download_worker import run_osm_download


class OsmDownloadWorkerTest(unittest.TestCase):
    def test_download_is_followed_by_forced_resumable_index(self):
        with tempfile.TemporaryDirectory() as root, \
             patch("tools.osm_download_worker.lower_process_priority"), \
             patch("tools.osm_download_worker.LocalAddressIndex.download_region", return_value=Path(root) / "germany-latest.osm.pbf") as download, \
             patch("tools.osm_download_worker.run_osm_index", return_value=0) as index:
            self.assertEqual(0, run_osm_download(root, "germany"))
        download.assert_called_once_with("germany")
        index.assert_called_once_with(root, force=True)

    def test_unknown_region_is_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as root, \
             patch("tools.osm_download_worker.LocalAddressIndex.download_region") as download:
            self.assertEqual(2, run_osm_download(root, "unknown"))
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
