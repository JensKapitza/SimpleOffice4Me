"""Regression checks for quick-search result actions and escaping."""
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app import app
from app.db import ensure_auth_database
from app.object_store import ObjectStore


class QuickSearchUiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "quickuser", "password": "a-long-passphrase"})
        self.client.post("/auth/login", data={"username": "quickuser", "password": "a-long-passphrase"})

    def tearDown(self):
        app.config.update(self.old)
        self.temp.cleanup()

    def test_ambiguous_results_have_direct_open_copy_and_filters(self):
        store = ObjectStore(self.root)
        store.create({"name": "Quick Test Alpha", "type": "Gerät"}, "quickuser")
        store.create({"name": "Quick Test Beta", "type": "Gerät"}, "quickuser")

        response = self.client.get("/documents/quick-search?q=Quick%20Test")
        self.assertEqual(200, response.status_code)
        soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        rows = soup.select("[data-quick-match='object']")
        self.assertEqual(2, len(rows))
        self.assertIsNotNone(soup.select_one("[data-quick-filter='all']"))
        self.assertIsNotNone(soup.select_one("[data-quick-filter='object']"))
        self.assertIsNotNone(soup.select_one("script[src$='quick_search_results.js']"))
        for row in rows:
            self.assertIsNotNone(row.select_one("a.btn[href]"))
            self.assertIsNotNone(row.select_one("button[data-copy-label]"))
        self.assertIsNotNone(soup.select_one("a[href*='/documents/search?']"))

    def test_search_labels_remain_escaped_and_copy_data_is_text(self):
        store = ObjectStore(self.root)
        store.create({"name": 'Payload <script>alert("x")</script> One', "type": "Gerät"}, "quickuser")
        store.create({"name": 'Payload <script>alert("x")</script> Two', "type": "Gerät"}, "quickuser")
        response = self.client.get("/documents/quick-search?q=Payload")
        self.assertEqual(200, response.status_code)
        soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        self.assertEqual([], soup.select("article[data-quick-match] script"))
        names = [button.get("data-copy-label", "") for button in soup.select("button[data-copy-label]")]
        self.assertTrue(any('<script>' in name for name in names))

    def test_empty_search_has_explicit_feedback(self):
        response = self.client.get("/documents/quick-search?q=no-results-unique-98765")
        self.assertEqual(200, response.status_code)
        soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        self.assertEqual([], soup.select("[data-quick-match]"))
        self.assertIn("No matches", soup.get_text(" "))


if __name__ == "__main__":
    unittest.main()
