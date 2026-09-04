import unittest
from unittest.mock import patch

from app.inventory import lookup_book_metadata, parse_openlibrary_search


class InventoryIsbnLookupRegressionTests(unittest.TestCase):
    def test_openlibrary_search_requires_exact_isbn(self):
        payload = {
            "docs": [
                {
                    "title": "Wrong edition",
                    "isbn": ["9783161484100"],
                    "author_name": ["Wrong Author"],
                },
                {
                    "title": "Exact edition",
                    "subtitle": "Matched by ISBN",
                    "isbn": ["0-13-110362-8", "9780131103627"],
                    "author_name": ["A. Author"],
                    "publisher": ["Example Press"],
                    "first_publish_year": 1988,
                    "number_of_pages_median": 274,
                    "language": ["eng"],
                    "subject": ["Programming"],
                },
            ]
        }

        result = parse_openlibrary_search(payload, "9780131103627")

        self.assertEqual("Exact edition", result["title"])
        self.assertEqual("A. Author", result["authors"])
        self.assertEqual("Example Press", result["publisher"])
        self.assertEqual("Open Library Search", result["metadata_source"])

    def test_openlibrary_search_rejects_only_wrong_editions(self):
        payload = {
            "docs": [
                {"title": "Wrong edition", "isbn": ["9783161484100"]},
                {"title": "No ISBN"},
            ]
        }

        self.assertEqual({}, parse_openlibrary_search(payload, "9780131103627"))

    @patch("app.inventory._http_json")
    def test_lookup_prefers_openlibrary_and_does_not_call_google_for_valid_title(self, http_json):
        http_json.return_value = {
            "docs": [{
                "title": "The C Programming Language",
                "isbn": ["9780131103627"],
                "author_name": ["Brian W. Kernighan", "Dennis M. Ritchie"],
                "publisher": ["Prentice Hall"],
                "first_publish_year": 1988,
            }]
        }

        result = lookup_book_metadata("9780131103627")

        self.assertEqual("The C Programming Language", result["title"])
        self.assertIn("Open Library Search", result["metadata_source"])
        self.assertEqual(1, http_json.call_count)
        self.assertIn("openlibrary.org/search.json", http_json.call_args.args[0])

    @patch("app.inventory._http_json")
    def test_google_is_only_last_fallback_when_openlibrary_has_no_title(self, http_json):
        http_json.side_effect = [
            {"docs": []},
            {},
            {
                "items": [{
                    "volumeInfo": {
                        "title": "Fallback title",
                        "industryIdentifiers": [{"identifier": "9780131103627"}],
                    }
                }]
            },
        ]

        result = lookup_book_metadata("9780131103627")

        self.assertEqual("Fallback title", result["title"])
        self.assertEqual(3, http_json.call_count)
        self.assertIn("openlibrary.org/search.json", http_json.call_args_list[0].args[0])
        self.assertIn("openlibrary.org/api/books", http_json.call_args_list[1].args[0])
        self.assertIn("www.googleapis.com/books", http_json.call_args_list[2].args[0])

    @patch("app.inventory._http_json")
    def test_reachable_openlibrary_without_match_is_not_reported_as_total_outage(self, http_json):
        http_json.side_effect = [
            {"docs": []},
            {},
            OSError("google blocked"),
        ]

        result = lookup_book_metadata("9780131103627")

        self.assertFalse(result.get("title"))
        self.assertTrue(result["lookup_reachable"])
        self.assertIn("google:OSError", result["lookup_errors"])


if __name__ == "__main__":
    unittest.main()
