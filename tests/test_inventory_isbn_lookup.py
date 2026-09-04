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
    def test_lookup_uses_current_openlibrary_search_when_google_has_no_result(self, http_json):
        http_json.side_effect = [
            {"totalItems": 0},
            {
                "docs": [{
                    "title": "The C Programming Language",
                    "isbn": ["9780131103627"],
                    "author_name": ["Brian W. Kernighan", "Dennis M. Ritchie"],
                    "publisher": ["Prentice Hall"],
                    "first_publish_year": 1988,
                }]
            },
        ]

        result = lookup_book_metadata("9780131103627")

        self.assertEqual("The C Programming Language", result["title"])
        self.assertIn("Open Library Search", result["metadata_source"])
        self.assertEqual(2, http_json.call_count)
        self.assertIn("/search.json?", http_json.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
