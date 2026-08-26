import tempfile
import unittest
from pathlib import Path

from app.osm_address import LocalAddressIndex, unique_candidate


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


if __name__ == "__main__":
    unittest.main()
