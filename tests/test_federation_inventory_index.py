"""Shared inventory index scope: minimal fields, default deny, exact ISBN/GTIN.

Store-level tests (no login): the serving index exposes only canonical
identifiers plus a neutral display name. Prices, locations, notes,
images, document contents, and owner data must never appear.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.contextflow.inventory_match import normalize_gtin
from app.federation_inventory_index import (
    INVENTORY_INDEX_SCHEMA,
    build_inventory_rows,
    inventory_generation,
)
from app.object_store import ObjectStore


BOOK_ISBN = "9783161484100"
GTIN_ITEM = normalize_gtin("5901234123457")


class InventoryIndexScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = str(Path(self.temp.name) / "documents")
        self.store = ObjectStore(self.root)

    def add_item(self, **values) -> dict:
        base = {"name": "Beispiel", "type": "book"}
        base.update(values)
        return self.store.create(base, "anna")

    def test_schema_version_is_pinned(self) -> None:
        self.assertEqual("sofp-inventory-index/v1", INVENTORY_INDEX_SCHEMA)

    def test_only_active_isbn_gtin_items_are_shared(self) -> None:
        self.add_item(name="Buch", identifier=BOOK_ISBN)
        self.add_item(name="Werkzeug", identifier=GTIN_ITEM)
        rows = build_inventory_rows(self.root)

        self.assertEqual(2, len(rows))
        by_key = {row["identifier"]: row for row in rows}
        self.assertEqual(
            {"identifier_type", "identifier", "display_name"},
            set(rows[0].keys()))
        self.assertEqual("isbn", by_key[BOOK_ISBN]["identifier_type"])
        self.assertEqual("gtin", by_key[GTIN_ITEM]["identifier_type"])
        self.assertEqual("Buch", by_key[BOOK_ISBN]["display_name"])
        self.assertEqual("Werkzeug", by_key[GTIN_ITEM]["display_name"])

    def test_no_price_location_notes_images_or_owner_data(self) -> None:
        self.add_item(
            name="Teures Buch", identifier=BOOK_ISBN,
            description="Geheim", location="Tresor",
            notes=[{"note_id": "n1", "text": "privat",
                    "created_at": "", "created_by": "anna"}],
            fields={"price_source": "Amazon", "market_price": "9.99"},
            document_ids=["doc-1"],
        )
        rows = build_inventory_rows(self.root)

        self.assertEqual(1, len(rows))
        blob = " ".join(f"{key}={value}" for key, value in rows[0].items())
        for forbidden in ("9.99", "Amazon", "Tresor", "Geheim", "privat",
                          "doc-1", "anna", "location", "notes", "fields"):
            self.assertNotIn(forbidden, blob)

    def test_inactive_title_only_and_plain_barcode_items_are_excluded(self) -> None:
        self.add_item(name="Alt", identifier=BOOK_ISBN, status="retired")
        self.add_item(name="Ohne Kennung", identifier="")
        self.add_item(name="Kassenzettel", identifier="12345")
        rows = build_inventory_rows(self.root)

        self.assertEqual([], rows)

    def test_invalid_identifiers_never_match(self) -> None:
        self.add_item(name="Falsch", identifier="9783161484101")
        rows = build_inventory_rows(self.root)

        self.assertEqual([], rows)

    def test_generation_changes_with_rows(self) -> None:
        before = inventory_generation(build_inventory_rows(self.root))
        self.add_item(name="Buch", identifier=BOOK_ISBN)
        after = inventory_generation(build_inventory_rows(self.root))

        self.assertNotEqual(before, after)
        self.assertEqual(
            after, inventory_generation(build_inventory_rows(self.root)))


if __name__ == "__main__":
    unittest.main()
