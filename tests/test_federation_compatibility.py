import unittest

from app.federation_compatibility import compatibility, requirements


class FederationCompatibilityTest(unittest.TestCase):
    def test_matching_protocol_and_features_are_compatible(self):
        result = compatibility({
            "base_url": "https://peer.example",
            "federation": {
                "name": "simpleoffice-federation",
                "min_version": 1,
                "max_version": 1,
            },
            "features": {
                "chat": 1,
                "documents": 1,
                "contacts": 1,
                "calendar": 1,
                "tasks": 1,
            },
        })
        self.assertEqual(result["protocol"], "compatible")
        self.assertEqual(result["protocol_version"], "1")
        self.assertEqual(result["transport"], "HTTPS")
        self.assertTrue(all(value is True for value in result["features"].values()))

    def test_non_overlapping_protocol_is_incompatible(self):
        result = compatibility({
            "base_url": "http://peer.example",
            "federation": {
                "name": "simpleoffice-federation",
                "min_version": 2,
                "max_version": 2,
            },
            "features": {"chat": 1},
        })
        self.assertEqual(result["protocol"], "incompatible")
        self.assertTrue(all(value is False for value in result["features"].values()))

    def test_legacy_profile_is_reported_unknown_not_compatible(self):
        result = compatibility({"base_url": "http://peer.example"})
        self.assertEqual(result["protocol"], "unknown")
        self.assertTrue(all(value is None for value in result["features"].values()))

    def test_requirements_match_current_federation_v1_contract(self):
        value = requirements()
        self.assertEqual(value["protocol_name"], "simpleoffice-federation")
        self.assertEqual(value["protocol_min"], 1)
        self.assertEqual(value["protocol_max"], 1)
        self.assertEqual(value["features"]["chat"], 1)
        self.assertEqual(value["features"]["documents"], 1)


if __name__ == "__main__":
    unittest.main()
