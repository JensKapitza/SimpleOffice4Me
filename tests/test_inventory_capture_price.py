"""Capture batch adoption with marketplace price fields (issue #658 follow-up).

Store-level tests (no login): adopting a reviewed book group stores the
user-selected ``market_price``/``currency``/``price_source`` via the
existing inventory field allowlist. Listing URLs are never stored —
they stay review-only links in the batch review. A result without a
numeric price must not write ``price_source``; an invalid price fails
closed without creating an item.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app.capture_inbox_store import CaptureInboxStore
from tests.test_inventory_capture_batch import (
    BATCH_A,
    BATCH_B,
    PNG_1PX,
    PNG_RED_1PX,
    _photo_payload,
)


class BatchAdoptPriceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = str(Path(self.temp.name) / "documents")
        self.store = CaptureInboxStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def adopt(self, batch_id: str, fields: dict, token: str) -> dict:
        from app.inventory_capture import adopt_batch

        saved = app.config.get("DOCUMENT_ROOT")
        app.config["DOCUMENT_ROOT"] = self.root
        try:
            with app.test_request_context("/"):
                return adopt_batch(batch_id, "anna", self.root, fields, token)
        finally:
            app.config["DOCUMENT_ROOT"] = saved

    def create_analyzed(self, data: bytes, request_id: str,
                        batch_id: str) -> dict:
        entry, _ = self.store.create("anna", data, ".png", request_id,
                                     batch_id)
        self.store.save_analysis(
            entry["entry_id"], "anna", _photo_payload(entry["entry_id"]))
        return entry

    def test_adopt_without_price_fields_stays_unchanged(self) -> None:
        from app.object_store import ObjectStore

        self.create_analyzed(PNG_1PX, "c1" * 16, BATCH_A)
        result = self.adopt(
            BATCH_A,
            {"title": "Preisloses Buch", "item_type": "book",
             "description": "", "tags": ""},
            "d1" * 16)
        self.assertTrue(result["ok"])
        item = ObjectStore(self.root).object(result["object_id"])
        stored = dict(item.get("fields", {}))
        self.assertNotIn("market_price", stored)
        self.assertNotIn("price_source", stored)

    def test_adopt_stores_marketplace_price_and_source(self) -> None:
        from app.object_store import ObjectStore

        first = self.create_analyzed(PNG_1PX, "c2" * 16, BATCH_A)
        second = self.create_analyzed(PNG_RED_1PX, "c3" * 16, BATCH_A)
        self.assertNotEqual(first["entry_id"], second["entry_id"])
        result = self.adopt(
            BATCH_A,
            {"title": "Buch mit Preis", "item_type": "book",
             "description": "", "tags": "",
             "market_price": "12,50", "currency": "eur",
             "price_source": "Amazon",
             "provider": "temu",
             "marketplace_url": "https://www.amazon.de/dp/X",
             "url": "https://example.evil/x"},
            "d2" * 16)
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("saved"))
        item = ObjectStore(self.root).object(result["object_id"])
        stored = dict(item.get("fields", {}))
        self.assertEqual("12.50", stored.get("market_price"))
        self.assertEqual("EUR", stored.get("currency"))
        self.assertEqual("Amazon", stored.get("price_source"))
        self.assertNotIn("marketplace_url", stored)
        self.assertNotIn("url", stored)
        self.assertNotIn("provider", stored)
        self.assertNotIn("amazon.de", str(item))

    def test_adopt_drops_price_source_without_price(self) -> None:
        from app.object_store import ObjectStore

        self.create_analyzed(PNG_1PX, "c5" * 16, BATCH_B)
        result = self.adopt(
            BATCH_B,
            {"title": "Preisloser Treffer", "item_type": "book",
             "description": "", "tags": "",
             "market_price": "", "currency": "EUR",
             "price_source": "Amazon"},
            "d4" * 16)
        self.assertTrue(result["ok"])
        item = ObjectStore(self.root).object(result["object_id"])
        stored = dict(item.get("fields", {}))
        self.assertNotIn("market_price", stored)
        self.assertNotIn("price_source", stored)

    def test_adopt_rejects_negative_marketplace_price(self) -> None:
        from app.inventory_capture import adopt_batch

        self.create_analyzed(PNG_1PX, "c6" * 16, BATCH_B)
        saved = app.config.get("DOCUMENT_ROOT")
        app.config["DOCUMENT_ROOT"] = self.root
        try:
            with app.test_request_context("/"):
                with self.assertRaises(ValueError) as raised:
                    adopt_batch(
                        BATCH_B, "anna", self.root,
                        {"title": "Negativer Preis", "item_type": "book",
                         "market_price": "-12,50", "currency": "EUR",
                         "price_source": "eBay"},
                        "d5" * 16)
        finally:
            app.config["DOCUMENT_ROOT"] = saved
        self.assertIn("Preis", str(raised.exception))

    def test_adopt_rejects_invalid_marketplace_price(self) -> None:
        from app.inventory_capture import adopt_batch

        self.create_analyzed(PNG_1PX, "c4" * 16, BATCH_B)
        saved = app.config.get("DOCUMENT_ROOT")
        app.config["DOCUMENT_ROOT"] = self.root
        try:
            with app.test_request_context("/"):
                with self.assertRaises(ValueError) as raised:
                    adopt_batch(
                        BATCH_B, "anna", self.root,
                        {"title": "Falscher Preis", "item_type": "book",
                         "market_price": "kein-preis"},
                        "d3" * 16)
        finally:
            app.config["DOCUMENT_ROOT"] = saved
        self.assertIn("Preis", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
