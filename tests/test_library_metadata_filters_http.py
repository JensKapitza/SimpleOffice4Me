"""Public library page contracts for metadata and location filters."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database

from app.library.store import LibraryStore
from app.object_store import ObjectStore


class LibraryMetadataFiltersHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        saved = {key: app.config.get(key) for key in ("TESTING", "DATABASE", "DOCUMENT_ROOT")}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            DATABASE=str(self.root / "users.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin) VALUES('filter-reader','unused',1)"
            )
            db.commit()
            self.user_id = db.execute(
                "SELECT id FROM user WHERE username='filter-reader'"
            ).fetchone()["id"]
        self.client = app.test_client()
        with self.client.session_transaction() as state:
            state["user_id"] = self.user_id
            state["_csrf_token"] = "library-filter-csrf-token"

        self.objects = ObjectStore(self.root)
        self.library = LibraryStore(self.root)
        self.history = self.library.create_location("History", "reader")
        self.fiction = self.library.create_location("Fiction", "reader")
        self.target = self.objects.create(
            {
                "name": "Harbor Atlas",
                "type": "book",
                "identifier": "9780000000001",
                "fields": {"authors": "Maya Rivers", "genre": "History"},
            },
            "reader",
        )
        self.same_location_other_genre = self.objects.create(
            {
                "name": "Night Letters",
                "type": "book",
                "fields": {"authors": "Maya Rivers", "genre": "Fiction"},
            },
            "reader",
        )
        self.other_location = self.objects.create(
            {
                "name": "Old Maps",
                "type": "book",
                "fields": {"authors": "Ari Stone", "genre": "History"},
            },
            "reader",
        )
        self.library.assign(self.target["object_id"], self.history["location_id"], "reader")
        self.library.assign(self.same_location_other_genre["object_id"], self.history["location_id"], "reader")
        self.library.assign(self.other_location["object_id"], self.fiction["location_id"], "reader")

    def test_combined_author_genre_and_location_filters_limit_visible_and_selectable_books(self):
        response = self.client.get(
            "/library",
            query_string={
                "author": "rivers",
                "genre": "history",
                "location": self.history["location_id"],
            },
        )

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Harbor Atlas", body)
        self.assertNotIn("Night Letters", body)
        self.assertNotIn("Old Maps", body)
        self.assertIn(f'value="{self.target["object_id"]}"', body)
        self.assertIn('name="author" value="rivers"', body)
        self.assertIn('name="genre" value="history"', body)

    def test_free_text_search_combines_with_author_filter(self):
        response = self.client.get(
            "/library",
            query_string={"q": "9780000000001", "author": "Rivers"},
        )

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Harbor Atlas", body)
        self.assertNotIn("Night Letters", body)
        self.assertNotIn("Old Maps", body)

    def test_filter_controls_submit_to_the_library_page_not_an_export_endpoint(self):
        response = self.client.get("/library")

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('<form method="get" action="/library">', body)
        self.assertIn('id="apply-library-filters" type="submit">Filtern</button>', body)
        self.assertIn('href="/library">Filter zurücksetzen</a>', body)
        self.assertIn('formaction="/library/export/books.csv">CSV</button>', body)

    def test_csv_export_uses_active_filters_when_nothing_is_selected(self):
        response = self.client.get(
            "/library/export/books.csv",
            query_string={
                "author": "rivers",
                "genre": "history",
                "location": self.history["location_id"],
            },
        )

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Harbor Atlas", body)
        self.assertNotIn("Night Letters", body)
        self.assertNotIn("Old Maps", body)

    def test_csv_export_with_filters_that_match_no_books_does_not_export_the_entire_library(self):
        response = self.client.get(
            "/library/export/books.csv",
            query_string={"author": "no such author"},
        )

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("ID,Titel,ISBN", body)
        self.assertNotIn("Harbor Atlas", body)
        self.assertNotIn("Night Letters", body)
        self.assertNotIn("Old Maps", body)

    def test_csv_export_explicit_selection_takes_precedence_over_active_filters(self):
        response = self.client.get(
            "/library/export/books.csv",
            query_string={
                "object_id": self.target["object_id"],
                "author": "no such author",
            },
        )

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Harbor Atlas", body)
        self.assertNotIn("Night Letters", body)
        self.assertNotIn("Old Maps", body)


if __name__ == "__main__":
    unittest.main()
