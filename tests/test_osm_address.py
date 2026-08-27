import json
import tempfile
import unittest
from pathlib import Path

from app.osm_address import LocalAddressIndex, human_bytes, unique_candidate


class OsmAddressTests(unittest.TestCase):
    def test_local_sqlite_index_finds_normalized_address(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            with index._db() as db:
                db.execute(
                    "INSERT INTO address(street,house_number,postal,city,country,state,lat,lon,osm_type,osm_id,normalized) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("Musterstraße", "12", "12345", "Musterstadt", "DE", "NRW", "51.0", "6.0", "node", "123", "musterstrasse 12 12345 musterstadt de"),
                )
            result = index.search("Musterstr. 12 12345", country_code="de")
            self.assertEqual(1, len(result))
            self.assertEqual("Musterstraße 12", result[0]["street"])
            self.assertEqual("12345", result[0]["postal"])
            self.assertEqual("Musterstadt", result[0]["city"])
            self.assertEqual("DE", result[0]["country"])

    def test_search_does_not_use_network_without_local_index(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual([], LocalAddressIndex(Path(root)).search("Musterstraße 12"))

    def test_unique_requires_one_complete_candidate(self):
        candidate = {"street": "A 1", "postal": "12345", "city": "Ort", "country": "DE"}
        self.assertEqual(candidate, unique_candidate([candidate]))
        self.assertIsNone(unique_candidate([candidate, dict(candidate)]))
        self.assertIsNone(unique_candidate([{"street": "A 1", "city": ""}]))

    def test_human_bytes_formats_download_sizes(self):
        self.assertEqual("1.0 MiB", human_bytes(1024 * 1024))
        self.assertEqual("1.5 GiB", human_bytes(int(1.5 * 1024**3)))
        self.assertEqual("unbekannt", human_bytes(0))

    def test_status_calculates_download_progress(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            index.data_dir.mkdir(parents=True, exist_ok=True)
            index.status_path.write_text(json.dumps({"state": "downloading", "downloaded_bytes": 25, "expected_bytes": 100}), encoding="utf-8")
            status = index.status()
            self.assertEqual(25.0, status["progress_percent"])
            self.assertEqual(25, status["downloaded_bytes"])
            self.assertEqual(100, status["expected_bytes"])


if __name__ == "__main__":
    unittest.main()
