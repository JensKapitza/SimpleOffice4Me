from __future__ import annotations

import io
import os
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from app import inventory_catalog as catalog
from app import inventory_catalog_http as transport

ISBN = "9780140328721"
ISBN10 = "0140328726"
OTHER_ISBN = "9780261103573"
EAN = "4006381333931"
OTHER_EAN = "4006381333948"


def open_record(isbn=ISBN, **fields):
    row = {
        "title": "A Book: Ä Edition", "authors": [{"name": "P. Author"}],
        "publishers": [{"name": "Field Press"}], "publish_date": "November 1988",
        "identifiers": {"isbn_13": [isbn]},
    }
    row.update(fields)
    return {f"ISBN:{ISBN}": row}


def google_volume(isbn=ISBN, **fields):
    row = {
        "title": "A Book: A\u0308 Edition", "authors": ["P. Author"],
        "publisher": "field press", "publishedDate": "1988-11-01",
        "industryIdentifiers": [{"type": "ISBN_13", "identifier": isbn}],
        "description": "Untrusted text ignored",
    }
    row.update(fields)
    return {"volumeInfo": row, "saleInfo": {"retailPrice": {"amount": 12.0}}}


def books(open_data=None, *google_items, total=None):
    if open_data is None:
        open_data = open_record()
    if not google_items:
        google_items = (google_volume(),)
    seen = []

    def fetch(provider, code):
        seen.append((provider, code))
        value = open_data if provider == "openlibrary" else {
            "items": list(google_items), "totalItems": len(google_items) if total is None else total,
        }
        if isinstance(value, Exception):
            raise value
        return value

    return fetch, seen


class CatalogEvidenceTests(unittest.TestCase):
    def setUp(self):
        catalog._cache.clear()
        self.addCleanup(catalog._cache.clear)

    def test_invalid_or_local_data_never_reaches_provider(self):
        sentinel = lambda *args: self.fail(f"bad data left app: {args!r}")
        for value in (ISBN + "6", "ISBN:" + ISBN, "Private Names " + ISBN, "9780140328720",
                      "4006381333939", "0000000000000", "１２３４５６７８", EAN + "x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                catalog.lookup_catalog(value, book_fetch=sentinel, product_fetch=sentinel)
        self.assertEqual(catalog.normalize_catalog_code("0-140-32872-6").value, ISBN)
        self.assertEqual(catalog.normalize_catalog_code("96385074").kind, "product")
        self.assertEqual(catalog.normalize_catalog_code("036000291452").kind, "product")
        self.assertEqual(catalog.normalize_catalog_code("04006381333931").kind, "product")

    def test_exact_title_and_edition_two_independent_providers_and_distinct_per_field_ids(self):
        fetch, seen = books()
        result = catalog.lookup_catalog(ISBN10, observed_title="  A BOOK: Ä   EDITION ", book_fetch=fetch)
        self.assertEqual(seen, [("openlibrary", ISBN), ("google_books", ISBN)])
        self.assertEqual((result["status"], result["reason"]), ("confirmed", "independent_edition_match"))
        recommendation = result["recommended"]
        self.assertEqual(set(recommendation["corroborated_by"]), set(catalog.BOOK_PROVIDERS))
        self.assertIn(recommendation["candidate_id"], [x["candidate_id"] for x in result["candidates"]])
        first, second = result["candidates"]
        self.assertNotEqual(first["field_evidence"]["isbn"]["source_id"], second["field_evidence"]["isbn"]["source_id"])
        self.assertEqual(first["field_evidence"]["title"]["field"], "title")
        self.assertEqual(first["field_evidence"]["title"]["kind"], "external_catalog")
        self.assertEqual(first["field_evidence"]["title"]["provider"], first["provider"])
        for row in result["candidates"]:
            self.assertNotIn("saleInfo", row)
            self.assertNotIn("description", row)
            self.assertNotIn("retail_price", row)
        self.assertEqual(result, catalog.lookup_catalog(ISBN, observed_title="A Book: Ä Edition", book_fetch=fetch))

    def test_no_echo_no_weak_google_suggestion_or_wrong_edition_borrowing(self):
        fetch, _ = books(open_record(identifiers={}), google_volume(OTHER_ISBN),
                         google_volume(industryIdentifiers=[]))
        result = catalog.lookup_catalog(ISBN, book_fetch=fetch)
        self.assertEqual(result["providers"], {"openlibrary": "no_match", "google_books": "no_match"})
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["recommended"])
        fetch, _ = books(open_record(), google_volume(industryIdentifiers=[
            {"type": "ISBN_13", "identifier": ISBN}, {"type": "ISBN_13", "identifier": OTHER_ISBN},
        ]))
        result = catalog.lookup_catalog(ISBN, book_fetch=fetch)
        self.assertEqual(result["reason"], "single_source")
        self.assertEqual(len(result["candidates"]), 1)

    def test_disagreements_do_not_merge_and_observed_title_remains_local(self):
        fixture = google_volume(publishedDate="1995", title="Another exact ISBN title")
        fetch, seen = books(open_record(), google_volume(), fixture)
        result = catalog.lookup_catalog(ISBN, book_fetch=fetch)
        self.assertEqual((result["status"], result["reason"]), ("review", "conflicting_edition"))
        self.assertEqual(len(result["candidates"]), 3)
        self.assertIsNone(result["recommended"])
        result = catalog.lookup_catalog(ISBN, observed_title="Private OCR title", book_fetch=fetch)
        self.assertEqual(result["reason"], "observed_title_differs")
        self.assertTrue(all(code == ISBN for _, code in seen))
        self.assertNotIn("Private OCR title", str(result))

    def test_no_confirm_when_fields_or_bounded_result_page_are_incomplete(self):
        fixtures = [
            (google_volume(publisher=None), 1, "edition_unconfirmed"),
            (google_volume(), 11, "provider_results_truncated"),
            (google_volume(publishedDate="1999"), 1, "conflicting_edition"),
        ]
        for row, total, expected in fixtures:
            with self.subTest(expected=expected):
                fetch, _ = books(open_record(), row, total=total)
                result = catalog.lookup_catalog(ISBN, book_fetch=fetch)
                self.assertEqual(result["reason"], expected)
                self.assertEqual(result["status"], "review")
                self.assertIsNone(result["recommended"])

    def test_provider_failures_do_not_leak_errors_or_block_manual_entry(self):
        fetch, _ = books(TimeoutError("secret"), google_volume())
        result = catalog.lookup_catalog(ISBN, book_fetch=fetch)
        self.assertEqual(result["providers"]["openlibrary"], "unavailable")
        self.assertEqual(result["status"], "review")
        self.assertNotIn("secret", str(result))
        with patch.dict(os.environ, {"SIMPLEOFFICE_GOOGLE_BOOKS_API_KEY": ""}), patch.object(
            transport, "get_json", side_effect=TimeoutError("secret")
        ) as request:
            result = catalog.lookup_catalog(ISBN)
        self.assertEqual(result["providers"], {"openlibrary": "unavailable", "google_books": "not_configured"})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(request.call_count, 1)

    def test_missing_answer_from_an_enabled_provider_is_not_a_definite_not_found(self):
        def provider(name, code):
            if name == "google_books":
                raise TimeoutError("network down")
            return {}
        result = catalog.lookup_catalog(ISBN, book_fetch=provider)
        self.assertEqual(result["providers"], {"openlibrary": "no_match", "google_books": "unavailable"})
        self.assertEqual((result["status"], result["reason"]), ("unavailable", "provider_unavailable"))
        self.assertIsNone(result["recommended"])
        result = catalog.lookup_catalog(ISBN, book_fetch=lambda name, code: {f"ISBN:{code}": "bad"}
                                        if name == "openlibrary" else {"totalItems": 0})
        self.assertEqual(result["providers"]["openlibrary"], "unavailable")
        self.assertEqual(result["status"], "unavailable")

    def test_only_normalized_code_on_wire_key_is_separate_and_success_cache_is_bounded(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_GOOGLE_BOOKS_API_KEY": "fixture-key"}), patch.object(
            transport, "get_json", side_effect=[open_record(), {"items": [google_volume()], "totalItems": 1}]
        ) as request:
            result = catalog.lookup_catalog(ISBN, observed_title="A Book: Ä Edition")
            result["candidates"][0]["title"] = "Caller mutated their copy"
            again = catalog.lookup_catalog(ISBN, observed_title="A Book: Ä Edition")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(again["status"], "confirmed")
        self.assertNotIn("Caller mutated", str(again))
        first, second = request.call_args_list
        self.assertEqual(first.args[0], "openlibrary.org")
        self.assertEqual(parse_qs(urlsplit(first.args[1]).query)["bibkeys"], [f"ISBN:{ISBN}"])
        self.assertEqual(second.args[0], "www.googleapis.com")
        self.assertEqual(parse_qs(urlsplit(second.args[1]).query)["q"], [f"isbn:{ISBN}"])
        self.assertEqual(second.kwargs["headers"], {"X-Goog-Api-Key": "fixture-key"})
        self.assertNotIn("fixture-key", second.args[1])
        self.assertNotIn("fixture-key", str(again))
        self.assertNotIn("Ä Edition", str(request.call_args_list))
        with patch.object(catalog, "CACHE_LIMIT", 1), patch.object(
            transport, "get_json", return_value={f"ISBN:{OTHER_ISBN}": {
                "title": "Another Book", "identifiers": {"isbn_13": [OTHER_ISBN]},
            }}
        ), patch.dict(os.environ, {"SIMPLEOFFICE_GOOGLE_BOOKS_API_KEY": ""}):
            catalog.lookup_catalog(OTHER_ISBN)
        self.assertEqual(len(catalog._cache), 1)
        self.assertEqual(next(iter(catalog._cache))[1], OTHER_ISBN)

    def test_gtin_requires_exact_code_and_never_uses_book_or_unapproved_marketplace(self):
        def forbidden(*_):
            self.fail("no book provider or marketplace request for default product lookup")

        with patch.object(transport, "get_json", side_effect=forbidden):
            result = catalog.lookup_catalog(EAN, book_fetch=forbidden)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["providers"], {"ebay": "not_configured"})

        def provider(name, code):
            self.assertEqual((name, code), ("ebay", EAN))
            return {"items": [
                {"gtin": OTHER_EAN, "title": "Different item", "variant": "wrong"},
                {"gtin": "04006381333931", "title": "Pencil", "variant": "red", "size": "M", "unit": "1 piece",
                 "retail_price": "wrong"},
                {"gtin": EAN, "title": "Pencil multipack", "variant": "red", "size": "M", "unit": "6 pieces"},
                {"title": "Missing barcode", "variant": "red", "size": "M", "unit": "1 piece"},
            ]}
        result = catalog.lookup_catalog(EAN, observed_variant="Red", observed_size="m",
                                        observed_unit="1 piece", product_fetch=provider)
        self.assertEqual(result["reason"], "conflicting_variant")
        self.assertEqual([row["variant_match"] for row in result["candidates"]], ["matches_observed", "differs"])
        self.assertEqual(result["candidates"][0]["field_evidence"]["unit"]["provider"], "ebay")
        self.assertNotIn("retail_price", result["candidates"][0])
        self.assertIsNone(result["recommended"])

    def test_product_exact_code_but_missing_variant_stays_manual(self):
        result = catalog.lookup_catalog(EAN, product_fetch=lambda *_: {"items": [{"gtin": EAN, "title": "Pencil"}]})
        self.assertEqual(result["reason"], "missing_variant_details")
        self.assertEqual(result["status"], "review")
        self.assertIsNone(result["recommended"])


class CatalogTransportTests(unittest.TestCase):
    def test_fixed_origin_and_endpoint_only(self):
        for host, path in [("private.test", "/api/books"), ("openlibrary.org", "//other.test/api/books"),
                           ("openlibrary.org", "/api/books/../secrets"), ("openlibrary.org", "/api/books#fragment")]:
            with self.subTest(host=host, path=path), patch.object(transport.http.client, "HTTPSConnection") as conn:
                with self.assertRaises(ValueError):
                    transport.get_json(host, path)
                conn.assert_not_called()

    def test_redirect_http_failure_or_size_cap_are_not_followed(self):
        with patch.object(transport.http.client, "HTTPSConnection") as cls:
            response = cls.return_value.getresponse.return_value
            response.status = 302
            with self.assertRaises(ValueError):
                transport.get_json("openlibrary.org", "/api/books")
            response.read1.assert_not_called()
            cls.return_value.close.assert_called_once()
        with patch.object(transport.http.client, "HTTPSConnection") as cls, patch.object(transport, "MAX_BYTES", 5):
            response = cls.return_value.getresponse.return_value
            response.status = 200
            response.getheader.return_value = None
            response.read1.side_effect = io.BytesIO(b'{"over":"limit"}').read
            with self.assertRaises(ValueError):
                transport.get_json("openlibrary.org", "/api/books")

    def test_allowed_fixed_endpoint_and_real_json_decode(self):
        with patch.object(transport.http.client, "HTTPSConnection") as cls:
            response = cls.return_value.getresponse.return_value
            response.status = 200
            response.getheader.return_value = None
            response.read1.side_effect = io.BytesIO(b'{"totalItems":0}').read
            self.assertEqual(transport.get_json("www.googleapis.com", "/books/v1/volumes?q=isbn%3A123"),
                             {"totalItems": 0})
            self.assertEqual(cls.call_args.kwargs["timeout"], transport.TIMEOUT_SECONDS)
            self.assertEqual(cls.return_value.request.call_args.args, ("GET", "/books/v1/volumes?q=isbn%3A123"))
