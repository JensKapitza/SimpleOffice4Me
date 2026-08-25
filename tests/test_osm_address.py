import unittest

from app.osm_address import _address_from_result, unique_candidate


class OsmAddressTests(unittest.TestCase):
    def test_normalizes_nominatim_address(self):
        result = _address_from_result({
            "display_name": "Musterstraße 12, 12345 Musterstadt, Deutschland",
            "lat": "51.0", "lon": "6.0", "osm_type": "way", "osm_id": 123,
            "address": {
                "road": "Musterstraße", "house_number": "12", "postcode": "12345",
                "city": "Musterstadt", "country": "Deutschland", "country_code": "de",
            },
        })
        self.assertEqual("Musterstraße 12", result["street"])
        self.assertEqual("12345", result["postal"])
        self.assertEqual("Musterstadt", result["city"])
        self.assertEqual("DE", result["country"])

    def test_unique_requires_one_complete_candidate(self):
        candidate = {"street": "A 1", "postal": "12345", "city": "Ort", "country": "DE"}
        self.assertEqual(candidate, unique_candidate([candidate]))
        self.assertIsNone(unique_candidate([candidate, dict(candidate)]))
        self.assertIsNone(unique_candidate([{"street": "A 1", "city": ""}]))


if __name__ == "__main__":
    unittest.main()
