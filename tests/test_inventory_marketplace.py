import os
import unittest
from pathlib import Path
from unittest import mock

from app.inventory_marketplace import (
    parse_amazon_html,
    parse_amazon_results,
    parse_ebay_html,
    parse_ebay_results,
    provider_status,
    search_marketplace,
)


AMAZON_HTML = """
<html><body>
  <div data-component-type="s-search-result" data-asin="3499249168">
    <h2><a href="/dp/3499249168"><span>Flammenbrut: Thriller</span></a></h2>
    <span class="a-price"><span class="a-offscreen">13,00 €</span></span>
  </div>
  <div data-component-type="s-search-result" data-asin="B0002">
    <h2><a href="/dp/B0002"><span>Flammenbrut gebraucht</span></a></h2>
  </div>
</body></html>
"""

EBAY_HTML = """
<html><body><ul>
  <li class="s-item">
    <a class="s-item__link" href="https://www.ebay.de/itm/123456789"><span class="s-item__title">Neues Angebot Flammenbrut Simon Beckett</span></a>
    <span class="s-item__price">4,99 EUR</span>
  </li>
</ul></body></html>
"""


class InventoryMarketplaceTests(unittest.TestCase):
    def cleared_marketplace_env(self):
        return {
            "SIMPLEOFFICE_AMAZON_ACCESS_KEY": "",
            "SIMPLEOFFICE_AMAZON_SECRET_KEY": "",
            "SIMPLEOFFICE_AMAZON_PARTNER_TAG": "",
            "SIMPLEOFFICE_EBAY_CLIENT_ID": "",
            "SIMPLEOFFICE_EBAY_CLIENT_SECRET": "",
        }

    def test_unconfigured_amazon_uses_public_page_after_user_search(self):
        with mock.patch.dict(os.environ, self.cleared_marketplace_env(), clear=False), mock.patch(
            "app.inventory_marketplace._html_request", return_value=AMAZON_HTML
        ):
            self.assertEqual({"amazon": False, "ebay": False}, provider_status())
            result = search_marketplace("amazon", "9783499249167")
        self.assertFalse(result["configured"])
        self.assertTrue(result["public_lookup"])
        self.assertEqual("public_page", result["source"])
        self.assertEqual("Flammenbrut: Thriller", result["results"][0]["title"])
        self.assertEqual("13.00", result["results"][0]["market_price"])
        self.assertEqual("EUR", result["results"][0]["currency"])
        self.assertEqual("Amazon öffentliche Suche", result["results"][0]["metadata_source"])

    def test_unconfigured_ebay_uses_public_page_after_user_search(self):
        with mock.patch.dict(os.environ, self.cleared_marketplace_env(), clear=False), mock.patch(
            "app.inventory_marketplace._html_request", return_value=EBAY_HTML
        ):
            result = search_marketplace("ebay", "9783499249167")
        self.assertFalse(result["configured"])
        self.assertEqual("Flammenbrut Simon Beckett", result["results"][0]["title"])
        self.assertEqual("4.99", result["results"][0]["market_price"])
        self.assertEqual("123456789", result["results"][0]["external_id"])

    def test_blocked_public_page_returns_safe_normal_search_without_bypass(self):
        with mock.patch.dict(os.environ, self.cleared_marketplace_env(), clear=False), mock.patch(
            "app.inventory_marketplace._html_request",
            side_effect=ValueError("Marketplace zeigt eine Schutzseite"),
        ):
            result = search_marketplace("amazon", "9783499249167")
        self.assertEqual([], result["results"])
        self.assertIn("Schutzseite", result["warning"])
        self.assertTrue(result["fallback_url"].startswith("https://www.amazon.de/"))

    def test_amazon_html_parser_keeps_only_supported_https_results(self):
        results = parse_amazon_html(AMAZON_HTML)
        self.assertEqual(2, len(results))
        self.assertEqual("3499249168", results[0]["external_id"])
        self.assertTrue(results[0]["url"].startswith("https://www.amazon.de/"))

    def test_ebay_html_parser_maps_title_price_and_item_id(self):
        results = parse_ebay_html(EBAY_HTML)
        self.assertEqual(1, len(results))
        self.assertEqual("Flammenbrut Simon Beckett", results[0]["title"])
        self.assertEqual("4.99", results[0]["market_price"])
        self.assertEqual("EUR", results[0]["currency"])

    def test_amazon_api_parser_limits_and_maps_three_results(self):
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

    def test_ebay_api_parser_limits_and_maps_three_results(self):
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

    def test_frontend_keeps_marketplace_actions_above_android_navigation(self):
        script = (Path(__file__).resolve().parents[1] / "static" / "js" / "inventory.js").read_text(encoding="utf-8")
        self.assertIn("visualViewport", script)
        self.assertIn("safe-area-inset-bottom", script)
        self.assertIn("eBay Daten suchen", script)
        self.assertIn("Bester Treffer", script)
        self.assertIn("/inventory/marketplace/search", script)


if __name__ == "__main__":
    unittest.main()
