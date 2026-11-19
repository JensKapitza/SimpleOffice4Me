"""Bookmark mutations return to the filtered list that submitted them."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from werkzeug.security import generate_password_hash

from app import app
from app import db as database


class BookmarkFilterReturnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "PROPAGATE_EXCEPTIONS", "TEST_CSRF_PROTECTION")}
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "bookmark-filter.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True, PROPAGATE_EXCEPTIONS=False, TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("bookmark-filter-test", generate_password_hash("bookmark-filter-password")),
            )
            self.user_id = db.execute("SELECT id FROM user WHERE username=?", ("bookmark-filter-test",)).fetchone()["id"]
            db.commit()
        self.client = app.test_client()
        self.client.post("/auth/login", data={"username": "bookmark-filter-test", "password": "bookmark-filter-password"})

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def add_bookmark(self, title="Filter target", url="https://example.org/filter-target"):
        response = self.client.post("/bookmarks/add", data={"title": title, "url": url})
        self.assertEqual(302, response.status_code)
        with app.app_context():
            return database.get_db().execute(
                "SELECT id FROM browser_bookmark WHERE user_id=? AND url=?", (self.user_id, url),
            ).fetchone()["id"]

    def assert_returned_to_query(self, response, query):
        self.assertEqual(302, response.status_code)
        target = urlsplit(response.headers["Location"])
        self.assertEqual("/bookmarks/", target.path)
        self.assertEqual(query, parse_qs(target.query).get("q", [None])[0])

    def test_add_update_delete_and_pin_preserve_search_query(self):
        q = "deep research"
        added = self.client.post("/bookmarks/add", data={
            "title": "Added", "url": "https://example.org/added", "q": q,
        })
        self.assert_returned_to_query(added, q)
        bookmark_id = self.add_bookmark()

        updated = self.client.post(f"/bookmarks/{bookmark_id}/update", data={
            "title": "Updated", "url": "https://example.org/updated", "q": q,
        })
        self.assert_returned_to_query(updated, q)
        pinned = self.client.post(f"/bookmarks/{bookmark_id}/pin", data={"q": q})
        self.assert_returned_to_query(pinned, q)
        deleted = self.client.post(f"/bookmarks/{bookmark_id}/delete", data={"q": q})
        self.assert_returned_to_query(deleted, q)

    def test_editing_out_the_only_match_keeps_the_bookmark(self):
        bookmark_id = self.add_bookmark("Quarterly", "https://example.org/reports")
        response = self.client.post(f"/bookmarks/{bookmark_id}/update", data={
            "title": "Annual", "url": "https://example.org/reports", "q": "Quarterly",
        }, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Suchtreffer (0)", response.get_data(as_text=True))
        self.assertIn("Keine passenden Lesezeichen gefunden.", response.get_data(as_text=True))
        self.assertIn('value="Annual"', self.client.get("/bookmarks/").get_data(as_text=True))

    def test_validation_errors_preserve_search_query(self):
        q = "invalid url check"
        added = self.client.post("/bookmarks/add", data={
            "title": "Invalid", "url": "javascript:alert(1)", "q": q,
        })
        self.assert_returned_to_query(added, q)

        bookmark_id = self.add_bookmark()
        updated = self.client.post(f"/bookmarks/{bookmark_id}/update", data={
            "title": "Invalid update", "url": "javascript:alert(1)", "q": q,
        })
        self.assert_returned_to_query(updated, q)

    def test_missing_or_invalid_q_falls_back_to_unfiltered_internal_url(self):
        bookmark_id = self.add_bookmark()
        no_q = self.client.post(f"/bookmarks/{bookmark_id}/pin")
        self.assertEqual(302, no_q.status_code)
        self.assertEqual("/bookmarks/", urlsplit(no_q.headers["Location"]).path)
        self.assertEqual("", urlsplit(no_q.headers["Location"]).query)

        for bad_q in ("x" * 201, "ä" * 101, "bad\u0000query"):
            with self.subTest(q=repr(bad_q)):
                response = self.client.post(f"/bookmarks/{bookmark_id}/pin", data={"q": bad_q})
                self.assertEqual(302, response.status_code)
                target = urlsplit(response.headers["Location"])
                self.assertEqual("/bookmarks/", target.path)
                self.assertEqual("", target.query)

    def test_external_looking_q_is_encoded_as_data_and_old_clients_without_q_work(self):
        bookmark_id = self.add_bookmark()
        q = "https://attacker.example/path?next=/"
        response = self.client.post(f"/bookmarks/{bookmark_id}/pin", data={"q": q})
        self.assert_returned_to_query(response, q)
        self.assertEqual("", urlsplit(response.headers["Location"]).netloc)

        old_client_response = self.client.post("/bookmarks/add", data={
            "title": "Legacy", "url": "https://example.org/legacy",
        })
        self.assertEqual(302, old_client_response.status_code)
        self.assertEqual("/bookmarks/", urlsplit(old_client_response.headers["Location"]).path)
        self.assertEqual("", urlsplit(old_client_response.headers["Location"]).query)


if __name__ == "__main__":
    unittest.main()
