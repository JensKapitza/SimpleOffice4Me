import tempfile
import unittest
from pathlib import Path

from app.inventory import (
    InventoryEnrichmentStore,
    _image_extension,
    isbn_from_barcode,
    merge_book_metadata,
    normalize_isbn,
    parse_google_books,
    parse_openlibrary,
)


class InventoryBookTests(unittest.TestCase):
    def test_valid_isbn13_is_preserved(self):
        self.assertEqual("9780131103627", normalize_isbn("978-0-13-110362-7"))

    def test_valid_isbn10_is_converted_to_isbn13(self):
        self.assertEqual("9780131103627", normalize_isbn("0-13-110362-8"))

    def test_invalid_isbn_checksum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Prüfziffer"):
            normalize_isbn("9780131103628")

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


if __name__ == "__main__":
    unittest.main()
