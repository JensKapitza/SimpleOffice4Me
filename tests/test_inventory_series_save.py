"""Series-save contracts: finish a photo series item-by-item via JSON.

The same inventory form posts to the existing domain route with
``response_format=json`` so the page and the remaining queue survive.
Each candidate carries a stable ``capture_request_id``: a retry after a
lost response returns the receipt instead of a duplicate.
"""
from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from app import app
from app import db as database
from app.inventory import InventoryEnrichmentStore
from app.library.store import LibraryStore
from app.object_store import ObjectStore
from tests.test_contextflow_inventory_match import PNG_1PX

CSRF_TOKEN = "series-save-csrf-token-long-enough"
ISBN = "9780131103627"


class InventorySeriesSaveContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.document_root = root / "documents"
        keys = ("TESTING", "DATABASE", "DOCUMENT_ROOT", "TEST_CSRF_PROTECTION", "SECRET_KEY")
        saved = {key: app.config.get(key) for key in keys}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            DATABASE=str(root / "users.sqlite"),
            DOCUMENT_ROOT=str(self.document_root),
            TEST_CSRF_PROTECTION=True,
            SECRET_KEY="inventory-series-save-test-secret",
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('series-member','unused',0)")
            db.commit()
            self.user_id = db.execute("SELECT id FROM user WHERE username='series-member'").fetchone()[0]
        self.client = app.test_client()
        with self.client.session_transaction() as state:
            state["user_id"] = self.user_id
            state["_csrf_token"] = CSRF_TOKEN
        self.location = LibraryStore(self.document_root).create_location("Regal Serie", "series-member")

    def series_form(self, token="b" * 32, **overrides):
        values = {
            "_csrf_token": CSRF_TOKEN,
            "response_format": "json",
            "library_return": "1",
            "capture_request_id": token,
            "library_location_id": self.location["location_id"],
            "item_type": "book",
            "title": "Serienbuch",
            "barcode": ISBN,
            "isbn": ISBN,
            "market_price": "12.50",
            "currency": "EUR",
            "price_source": "eBay",
        }
        values.update(overrides)
        return values

    def series_object_form(self, token="b" * 32, **overrides):
        values = {
            "_csrf_token": CSRF_TOKEN,
            "response_format": "json",
            "capture_request_id": token,
            "item_type": "tool",
            "title": "Hammer",
            "barcode": "HAMMER-01",
            "manufacturer": "Werkstatt",
        }
        values.update(overrides)
        return values

    def tool_items(self):
        return [item for item in ObjectStore(self.document_root).objects() if item.get("type") == "tool"]

    def book_items(self):
        return [item for item in ObjectStore(self.document_root).objects() if item.get("type") == "book"]

    def test_series_create_returns_json_links_and_assigns_shelf_photo_and_price(self):
        data = self.series_form()
        data["photo"] = (BytesIO(PNG_1PX), "serie.png")

        response = self.client.post("/inventory/items", data=data)

        self.assertEqual(200, response.status_code)
        self.assertIn("application/json", response.content_type)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["saved"])
        self.assertEqual("Serienbuch", payload["name"])
        self.assertTrue(payload["display_id"])
        self.assertTrue(payload["detail_url"].endswith(payload["object_id"]))
        self.assertIn(self.location["location_id"], payload["library_url"])
        item = self.book_items()[0]
        self.assertEqual(payload["object_id"], item["object_id"])
        self.assertEqual(ISBN, item["identifier"])
        self.assertEqual("12.50", item["fields"].get("market_price"))
        self.assertEqual("EUR", item["fields"].get("currency"))
        self.assertEqual("eBay", item["fields"].get("price_source"))
        assignment = LibraryStore(self.document_root).assignment(item["object_id"])
        self.assertEqual(self.location["location_id"], assignment["location_id"])
        meta = InventoryEnrichmentStore(self.document_root).object_meta(item["object_id"])
        self.assertEqual(1, len(meta.get("photos", [])))
        self.assertEqual(ISBN, meta.get("fields_snapshot", {}).get("isbn"))

    def test_series_retry_with_same_token_returns_receipt_without_duplicate(self):
        form = self.series_form(token="c" * 32)
        first = self.client.post("/inventory/items", data=form)
        retry = self.client.post("/inventory/items", data=form)

        self.assertEqual(200, first.status_code)
        self.assertEqual(200, retry.status_code)
        self.assertTrue(first.get_json()["saved"])
        payload = retry.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["saved"])
        self.assertTrue(payload["retried"])
        self.assertEqual(first.get_json()["object_id"], payload["object_id"])
        self.assertEqual(1, len(self.book_items()))
        self.assertEqual(1, len(LibraryStore(self.document_root).contents(self.location["location_id"])))

    def test_series_missing_title_is_json_field_error_and_creates_nothing(self):
        response = self.client.post("/inventory/items", data=self.series_form(title=""))

        self.assertEqual(400, response.status_code)
        self.assertIn("application/json", response.content_type)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("Buchtitel", payload["error"])
        self.assertEqual({"title": "missing"}, payload["fields"])
        self.assertEqual([], self.book_items())

    def test_series_invalid_isbn_is_json_field_error(self):
        response = self.client.post("/inventory/items", data=self.series_form(isbn="9780131103628"))

        self.assertEqual(400, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual({"isbn": "invalid"}, payload["fields"])
        self.assertEqual([], self.book_items())

    def test_series_unknown_shelf_is_json_field_error(self):
        response = self.client.post(
            "/inventory/items",
            data=self.series_form(library_location_id="missing-location"),
        )

        self.assertEqual(400, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual({"library_location_id": "invalid"}, payload["fields"])
        self.assertEqual([], self.book_items())

    def test_series_invalid_price_is_json_field_error(self):
        response = self.client.post("/inventory/items", data=self.series_form(market_price="zwölf"))

        self.assertEqual(400, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual({"market_price": "invalid"}, payload["fields"])
        self.assertEqual([], self.book_items())

    def test_series_duplicate_reports_existing_item_without_false_saved(self):
        existing = ObjectStore(self.document_root).create(
            {
                "name": "Vorhandenes Buch",
                "type": "book",
                "status": "active",
                "description": "",
                "identifier": ISBN,
                "location": "",
                "expires_at": "",
                "tags": "Inventar",
                "fields": "",
            },
            "series-member",
        )

        response = self.client.post("/inventory/items", data=self.series_form(token="d" * 32))

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["saved"])
        self.assertTrue(payload["duplicate"])
        self.assertEqual(existing["object_id"], payload["object_id"])
        self.assertTrue(payload["detail_url"].endswith(existing["object_id"]))
        self.assertEqual(1, len(self.book_items()))
        assignment = LibraryStore(self.document_root).assignment(existing["object_id"])
        self.assertEqual(self.location["location_id"], assignment["location_id"])

    def test_series_copy_needs_fresh_token_and_allow_duplicate(self):
        ObjectStore(self.document_root).create(
            {
                "name": "Vorhandenes Buch",
                "type": "book",
                "status": "active",
                "description": "",
                "identifier": ISBN,
                "location": "",
                "expires_at": "",
                "tags": "Inventar",
                "fields": "",
            },
            "series-member",
        )

        response = self.client.post(
            "/inventory/items",
            data=self.series_form(token="e" * 32, allow_duplicate="1"),
        )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["saved"])
        self.assertEqual(2, len(self.book_items()))

    def test_series_token_of_other_actor_is_rejected(self):
        with app.app_context():
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('series-guest','unused',0)")
            db.commit()
            guest_id = db.execute("SELECT id FROM user WHERE username='series-guest'").fetchone()[0]
        form = self.series_form(token="f" * 32)
        created = self.client.post("/inventory/items", data=form)
        self.assertTrue(created.get_json()["saved"])
        with self.client.session_transaction() as state:
            state["user_id"] = guest_id
            state["_csrf_token"] = CSRF_TOKEN

        response = self.client.post("/inventory/items", data=form)

        self.assertEqual(403, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(1, len(self.book_items()))

    def test_series_object_saves_without_shelf_and_keeps_type(self):
        response = self.client.post("/inventory/items", data=self.series_object_form())

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["saved"])
        self.assertEqual("Hammer", payload["name"])
        self.assertNotIn("library_url", payload)
        tools = self.tool_items()
        self.assertEqual(1, len(tools))
        tool = tools[0]
        self.assertEqual("tool", tool["type"])
        self.assertEqual("HAMMER-01", tool["identifier"])
        self.assertEqual(payload["object_id"], tool["object_id"])

    def test_series_object_retry_with_same_token_returns_receipt(self):
        form = self.series_object_form(token="c" * 32)
        first = self.client.post("/inventory/items", data=form)
        retry = self.client.post("/inventory/items", data=form)

        self.assertTrue(first.get_json()["saved"])
        payload = retry.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["saved"])
        self.assertTrue(payload["retried"])
        self.assertEqual(first.get_json()["object_id"], payload["object_id"])
        self.assertEqual(1, len(self.tool_items()))

    def test_series_object_token_of_other_actor_is_rejected(self):
        with app.app_context():
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('series-guest','unused',0)")
            db.commit()
            guest_id = db.execute("SELECT id FROM user WHERE username='series-guest'").fetchone()[0]
        form = self.series_object_form(token="f" * 32)
        created = self.client.post("/inventory/items", data=form)
        self.assertTrue(created.get_json()["saved"])
        with self.client.session_transaction() as state:
            state["user_id"] = guest_id
            state["_csrf_token"] = CSRF_TOKEN

        response = self.client.post("/inventory/items", data=form)

        self.assertEqual(403, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(1, len(self.tool_items()))

    def test_series_plain_malformed_token_is_rejected(self):
        before = {item["object_id"] for item in ObjectStore(self.document_root).objects()}
        response = self.client.post("/inventory/items", data=self.series_object_form(token="kurz"))

        self.assertEqual(400, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual({"capture_request_id": "invalid"}, payload["fields"])
        after = {item["object_id"] for item in ObjectStore(self.document_root).objects()}
        self.assertEqual(before, after)
        self.assertEqual([], self.tool_items())

    def test_series_object_without_any_code_saves_with_type_kept(self):
        response = self.client.post("/inventory/items", data=self.series_object_form(token="0" * 32, barcode=""))

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["saved"])
        tools = self.tool_items()
        self.assertEqual(1, len(tools))
        self.assertEqual("tool", tools[0]["type"])
        self.assertEqual("", tools[0]["identifier"])
        self.assertEqual(payload["object_id"], tools[0]["object_id"])

    def test_series_book_title_only_saves_assigns_photo_and_retry_is_idempotent(self):
        form = self.series_form(token="1" * 32, title="Nur Titel", barcode="", isbn="")
        form["photo"] = (BytesIO(PNG_1PX), "titel.png")
        first = self.client.post("/inventory/items", data=form)

        self.assertEqual(200, first.status_code)
        payload = first.get_json()
        self.assertTrue(payload["saved"])
        books = self.book_items()
        self.assertEqual(1, len(books))
        item = books[0]
        self.assertEqual("book", item["type"])
        self.assertEqual("Nur Titel", item["name"])
        self.assertEqual(payload["object_id"], item["object_id"])
        assignment = LibraryStore(self.document_root).assignment(item["object_id"])
        self.assertEqual(self.location["location_id"], assignment["location_id"])
        meta = InventoryEnrichmentStore(self.document_root).object_meta(item["object_id"])
        self.assertEqual(1, len(meta.get("photos", [])))

        retry = self.client.post(
            "/inventory/items",
            data=self.series_form(token="1" * 32, title="Nur Titel", barcode="", isbn=""),
        )
        retried = retry.get_json()
        self.assertEqual(200, retry.status_code)
        self.assertTrue(retried["ok"])
        self.assertFalse(retried["saved"])
        self.assertTrue(retried["retried"])
        self.assertEqual(1, len(self.book_items()))

    def test_plain_submit_without_json_flag_still_redirects(self):
        form = self.series_form()
        del form["response_format"]

        response = self.client.post("/inventory/items", data=form)

        self.assertEqual(302, response.status_code)
        self.assertIn("/library?", response.location)
        self.assertEqual(1, len(self.book_items()))


class InventorySeriesShelfSelectorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        keys = ("TESTING", "DATABASE", "DOCUMENT_ROOT", "TEST_CSRF_PROTECTION", "SECRET_KEY", "CONTEXTFLOW_ENABLED")
        saved = {key: app.config.get(key) for key in keys}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            DATABASE=str(root / "users.sqlite"),
            DOCUMENT_ROOT=str(root / "documents"),
            TEST_CSRF_PROTECTION=True,
            SECRET_KEY="inventory-series-shelf-test-secret",
            CONTEXTFLOW_ENABLED=True,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('series-owner','unused',1)")
            db.commit()
            owner_id = db.execute("SELECT id FROM user WHERE username='series-owner'").fetchone()[0]
        self.client = app.test_client()
        with self.client.session_transaction() as state:
            state["user_id"] = owner_id
            state["_csrf_token"] = CSRF_TOKEN
        self.location = LibraryStore(Path(self.temp.name) / "documents").create_location(
            "Regal Serie", "series-owner"
        )

    def test_series_block_exposes_retained_shelf_and_queue(self):
        response = self.client.get("/inventory")

        self.assertEqual(200, response.status_code)
        self.assertIn(b'id="series-shelf"', response.data)
        self.assertIn(b'id="queue-list"', response.data)
        self.assertIn("Regal Serie".encode("utf-8"), response.data)
        self.assertIn(self.location["location_id"].encode(), response.data)

    def test_series_block_hidden_without_contextflow_flag(self):
        self.addCleanup(app.config.update, {"CONTEXTFLOW_ENABLED": True})
        app.config.update(CONTEXTFLOW_ENABLED=False)

        response = self.client.get("/inventory")

        self.assertEqual(200, response.status_code)
        self.assertNotIn(b'id="series-shelf"', response.data)


if __name__ == "__main__":
    unittest.main()
