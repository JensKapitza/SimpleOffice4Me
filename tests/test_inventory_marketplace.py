import os
import unittest
from pathlib import Path
from unittest import mock

from app.inventory_marketplace import (
    parse_amazon_results,
    parse_ebay_results,
    provider_status,
    search_marketplace,
)


class InventoryMarketplaceTests(unittest.TestCase):
    def test_unconfigured_providers_return_safe_search_fallback(self):
        cleared = {
            "SIMPLEOFFICE_AMAZON_ACCESS_KEY": "",
            "SIMPLEOFFICE_AMAZON_SECRET_KEY": "",
            "SIMPLEOFFICE_AMAZON_PARTNER_TAG": "",
            "SIMPLEOFFICE_EBAY_CLIENT_ID": "",
            "SIMPLEOFFICE_EBAY_CLIENT_SECRET": "",
        }
        with mock.patch.dict(os.environ, cleared, clear=False):
            self.assertEqual({"amazon": False, "ebay": False}, provider_status())
            amazon = search_marketplace("amazon", "9783423283302")
            ebay = search_marketplace("ebay", "Compact Disc")
        self.assertFalse(amazon["configured"])
        self.assertEqual([], amazon["results"])
        self.assertTrue(amazon["fallback_url"].startswith("https://www.amazon.de/"))
        self.assertFalse(ebay["configured"])
        self.assertTrue(ebay["fallback_url"].startswith("https://www.ebay.de/"))

    def test_amazon_parser_limits_and_maps_three_results(self):
        items = []
        for index in range(5):
            items.append(
                {
                    "ASIN": f"ASIN{index}",
                    "DetailPageURL": f"https://www.amazon.de/dp/ASIN{index}",
                    "ItemInfo": {
                        "Title": {"DisplayValue": f"Titel {index}"},
                        "ByLineInfo": {
                            "Manufacturer": {"DisplayValue": "Hersteller"},
                            "Contributors": [{"Name": "Autor", "Role": "Author"}],
                        },
                        "Classifications": {"Binding": {"DisplayValue": "Taschenbuch"}},
                        "ExternalIds": {
                            "ISBNs": {"DisplayValues": ["9783423283302"]},
                            "EANs": {"DisplayValues": ["9783423283302"]},
                        },
                        "Features": {"DisplayValues": ["Merkmal eins", "Merkmal zwei"]},
                    },
                    "Offers": {"Listings": [{"Price": {"Amount": 12.5, "Currency": "EUR"}}]},
                }
            )
        results = parse_amazon_results({"SearchResult": {"Items": items}})
        self.assertEqual(3, len(results))
        self.assertEqual("Titel 0", results[0]["title"])
        self.assertEqual("Autor", results[0]["authors"])
        self.assertEqual("9783423283302", results[0]["isbn"])
        self.assertEqual("12.5", results[0]["market_price"])
        self.assertEqual("EUR", results[0]["currency"])
        self.assertEqual("Amazon Product Advertising API", results[0]["metadata_source"])

    def test_ebay_parser_limits_and_maps_three_results(self):
        items = [
            {
                "itemId": f"item-{index}",
                "title": f"CD {index}",
                "itemWebUrl": f"https://www.ebay.de/itm/{index}",
                "price": {"value": "4.99", "currency": "EUR"},
                "categories": [{"categoryName": "Musik"}],
                "shortDescription": "gebraucht",
            }
            for index in range(5)
        ]
        results = parse_ebay_results({"itemSummaries": items})
        self.assertEqual(3, len(results))
        self.assertEqual("CD 0", results[0]["title"])
        self.assertEqual("4.99", results[0]["market_price"])
        self.assertEqual("Musik", results[0]["categories"])
        self.assertEqual("eBay Browse API", results[0]["metadata_source"])

    def test_frontend_keeps_marketplace_actions_above_android_navigation(self):
        script = (Path(__file__).resolve().parents[1] / "static" / "js" / "inventory.js").read_text(encoding="utf-8")
        self.assertIn("visualViewport", script)
        self.assertIn("safe-area-inset-bottom", script)
        self.assertIn("eBay Daten suchen", script)
        self.assertIn("Bester Treffer", script)
        self.assertIn("/inventory/marketplace/search", script)


if __name__ == "__main__":
    unittest.main()
