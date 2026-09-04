import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, url_for

from app.inventory import (
    InventoryEnrichmentStore,
    _advance_due,
    _find_exact,
    _http_json,
    _image_extension,
    _inspection_rrule,
    _money,
    bp as inventory_blueprint,
    isbn_from_barcode,
    merge_book_metadata,
    normalize_isbn,
    parse_google_books,
    parse_openlibrary,
    record_inventory_task_completion,
)
from app.object_store import ObjectStore


class InventoryBookTests(unittest.TestCase):
    def test_valid_isbn13_is_preserved(self):
        self.assertEqual("9780131103627", normalize_isbn("978-0-13-110362-7"))

    def test_valid_isbn10_is_converted_to_isbn13(self):
        self.assertEqual("9780131103627", normalize_isbn("0-13-110362-8"))

    def test_invalid_isbn_checksum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Prüfziffer"):
            normalize_isbn("9780131103628")

    def test_non_book_ean13_is_not_accepted_as_isbn(self):
        with self.assertRaisesRegex(ValueError, "978 oder 979"):
            normalize_isbn("4006381333931")

    def test_book_ean_is_detected_as_isbn(self):
        self.assertEqual("9780131103627", isbn_from_barcode("9780131103627"))
        self.assertEqual("", isbn_from_barcode("4006381333931"))

    def test_google_books_parser_extracts_metadata_and_price(self):
        payload = {
            "items": [{
                "volumeInfo": {
                    "title": "Test Book",
                    "subtitle": "Second Edition",
                    "authors": ["A. Author", "B. Writer"],
                    "publisher": "Example Press",
                    "publishedDate": "2026-01-02",
                    "description": "Description",
                    "pageCount": 321,
                    "language": "de",
                    "categories": ["Technology"],
                    "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780131103627"}],
                },
                "saleInfo": {"retailPrice": {"amount": 19.95, "currencyCode": "EUR"}},
            }]
        }
        result = parse_google_books(payload, "9780131103627")
        self.assertEqual("Test Book", result["title"])
        self.assertEqual("A. Author; B. Writer", result["authors"])
        self.assertEqual("19.95", result["market_price"])
        self.assertEqual("EUR", result["currency"])
        self.assertEqual("Google Books", result["price_source"])

    def test_google_books_parser_skips_wrong_edition(self):
        payload = {
            "items": [
                {"volumeInfo": {"title": "Wrong Edition", "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9783161484100"}]}},
                {"volumeInfo": {"title": "Exact Edition", "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780131103627"}]}},
            ]
        }
        self.assertEqual("Exact Edition", parse_google_books(payload, "9780131103627")["title"])

    def test_google_books_exact_isbn_beats_identifierless_fallback(self):
        payload = {
            "items": [
                {"volumeInfo": {"title": "Unspecified Edition"}},
                {"volumeInfo": {"title": "Exact Edition", "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780131103627"}]}},
            ]
        }
        self.assertEqual("Exact Edition", parse_google_books(payload, "9780131103627")["title"])

    def test_google_books_identifierless_result_remains_fallback(self):
        payload = {"items": [{"volumeInfo": {"title": "Fallback Without ISBN"}}]}
        self.assertEqual("Fallback Without ISBN", parse_google_books(payload, "9780131103627")["title"])

    def test_google_books_zero_price_is_preserved(self):
        payload = {
            "items": [{
                "volumeInfo": {
                    "title": "Free Book",
                    "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780131103627"}],
                },
                "saleInfo": {"retailPrice": {"amount": 0, "currencyCode": "EUR"}},
            }]
        }
        result = parse_google_books(payload, "9780131103627")
        self.assertEqual("0", result["market_price"])
        self.assertEqual("Google Books", result["price_source"])

    def test_openlibrary_parser_fills_book_fields(self):
        payload = {
            "ISBN:9780131103627": {
                "title": "Fallback Book",
                "authors": [{"name": "A. Author"}],
                "publishers": [{"name": "Example Press"}],
                "publish_date": "2025",
                "number_of_pages": 200,
                "subjects": [{"name": "Computing"}],
            }
        }
        result = parse_openlibrary(payload, "9780131103627")
        self.assertEqual("Fallback Book", result["title"])
        self.assertEqual("A. Author", result["authors"])
        self.assertEqual("200", result["page_count"])
        self.assertEqual("Open Library", result["metadata_source"])

    def test_merge_keeps_primary_and_fills_gaps(self):
        result = merge_book_metadata(
            {"title": "Primary", "authors": "", "metadata_source": "Google Books"},
            {"title": "Fallback", "authors": "Writer", "publisher": "Press", "metadata_source": "Open Library"},
        )
        self.assertEqual("Primary", result["title"])
        self.assertEqual("Writer", result["authors"])
        self.assertEqual("Press", result["publisher"])
        self.assertEqual("Google Books + Open Library", result["metadata_source"])

    def test_rate_limit_rejects_second_immediate_action(self):
        with tempfile.TemporaryDirectory() as temp:
            store = InventoryEnrichmentStore(Path(temp))
            first, first_retry = store.consume_rate_limit("tester", "book-metadata", interval=5)
            second, second_retry = store.consume_rate_limit("tester", "book-metadata", interval=5)
            self.assertTrue(first)
            self.assertEqual(0, first_retry)
            self.assertFalse(second)
            self.assertGreaterEqual(second_retry, 1)
            self.assertLessEqual(second_retry, 5)

    def test_photo_type_is_checked_by_signature(self):
        self.assertEqual(".jpg", _image_extension(b"\xff\xd8\xffrest"))
        self.assertEqual(".png", _image_extension(b"\x89PNG\r\n\x1a\nrest"))
        self.assertEqual(".webp", _image_extension(b"RIFFxxxxWEBPrest"))
        with self.assertRaises(ValueError):
            _image_extension(b"not-an-image")

    def test_money_rejects_non_finite_values(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _money(value)

    def test_duplicate_lookup_checks_nfc_even_when_identifier_is_different(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ObjectStore(Path(temp))
            created = store.create(
                {"name": "Tagged book", "type": "book", "identifier": "9780131103627", "fields": "nfc_id=TAG-123"},
                "tester",
            )
            with patch("app.inventory._objects", return_value=store):
                found = _find_exact("9783161484100", "TAG-123")
            self.assertIsNotNone(found)
            self.assertEqual(created["object_id"], found["object_id"])

    def test_metadata_http_client_rejects_unknown_host_before_network_access(self):
        with self.assertRaisesRegex(ValueError, "Metadatenquelle"):
            _http_json("https://example.com/books")

    def test_inspection_rrules_cover_supported_intervals(self):
        self.assertEqual("FREQ=MONTHLY;INTERVAL=12", _inspection_rrule(12, "months"))
        self.assertEqual("FREQ=YEARLY;INTERVAL=1", _inspection_rrule(1, "years"))
        self.assertEqual("FREQ=WEEKLY;INTERVAL=2", _inspection_rrule(2, "weeks"))

    def test_inspection_due_handles_month_end_and_leap_year(self):
        self.assertEqual("2027-02-28", _advance_due("2027-01-31", 1, "months"))
        self.assertEqual("2029-02-28", _advance_due("2028-02-29", 1, "years"))

    def test_inspection_sidecar_records_history_and_next_due(self):
        with tempfile.TemporaryDirectory() as temp:
            store = InventoryEnrichmentStore(Path(temp))
            rule = store.add_inspection(
                "object-1",
                {"name": "Elektrische Prüfung", "next_due": "2026-10-01", "interval": 12, "unit": "months", "responsible": "tester", "note": "DGUV"},
                "tester",
                "task-1",
            )
            event = store.complete_inspection("object-1", rule["rule_id"], "tester", result="in Ordnung")
            self.assertEqual("2027-10-01", event["next_due"])
            meta = store.object_meta("object-1")
            self.assertEqual(1, len(meta["inspection_history"]))
            self.assertEqual("2027-10-01", meta["inspections"][0]["next_due"])
            self.assertEqual("in Ordnung", meta["inspections"][0]["last_result"])

    def test_one_time_inspection_cannot_be_completed_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            store = InventoryEnrichmentStore(Path(temp))
            rule = store.add_inspection(
                "object-once",
                {"name": "Einmal prüfen", "next_due": "2026-10-01", "interval": 0, "unit": "months", "responsible": "", "note": ""},
                "tester",
                "task-once",
            )
            store.complete_inspection("object-once", rule["rule_id"], "tester")
            with self.assertRaisesRegex(ValueError, "bereits abgeschlossen"):
                store.complete_inspection("object-once", rule["rule_id"], "tester")
            self.assertEqual(1, len(store.object_meta("object-once")["inspection_history"]))

    def test_task_completion_can_update_inventory_inspection_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = InventoryEnrichmentStore(Path(temp))
            store.add_inspection(
                "object-2",
                {"name": "Nachsehen", "next_due": "2026-11-01", "interval": 1, "unit": "years", "responsible": "", "note": ""},
                "tester",
                "task-2",
            )
            linked = record_inventory_task_completion(
                Path(temp),
                {"id": "task-2", "related_to": ["urn:simpleoffice:object:object-2"], "result": "erledigt"},
                "tester",
                "2027-11-01",
            )
            self.assertTrue(linked)
            meta = store.object_meta("object-2")
            self.assertEqual("2027-11-01", meta["inspections"][0]["next_due"])
            self.assertEqual("erledigt", meta["inspection_history"][0]["result"])

    def test_inventory_blueprint_preserves_legacy_create_book_endpoint(self):
        app = Flask("inventory-route-test")
        app.register_blueprint(inventory_blueprint)
        with app.test_request_context():
            self.assertEqual("/inventory/books", url_for("inventory.create_book"))
            self.assertEqual("/inventory/items", url_for("inventory.create_item"))

    def test_inventory_form_contains_universal_fields_and_production_csrf(self):
        template = (Path(__file__).parents[1] / "templates" / "inventory" / "index.html").read_text(encoding="utf-8")
        for fragment in ('name="_csrf_token"', 'name="item_type"', 'name="manufacturer"', 'name="serial_number"', 'name="inspection_due"', 'id="inventory-find"'):
            self.assertIn(fragment, template)
        self.assertIn("isbn.value=''", template)

    def test_inventory_detail_contains_inspection_history(self):
        template = (Path(__file__).parents[1] / "templates" / "inventory" / "detail.html").read_text(encoding="utf-8")
        self.assertIn("Prüfhistorie", template)
        self.assertIn("inventory.complete_inspection", template)
        self.assertIn('name="_csrf_token"', template)
        self.assertIn('loading="lazy"', template)
        self.assertIn('rel="noopener noreferrer"', template)
        self.assertIn('scope="col"', template)

    def test_task_board_uses_security_middleware_csrf_field(self):
        template = (Path(__file__).parents[1] / "templates" / "tasks" / "board.html").read_text(encoding="utf-8")
        self.assertNotIn('name="csrf_token"', template)
        self.assertIn('name="_csrf_token"', template)


if __name__ == "__main__":
    unittest.main()
