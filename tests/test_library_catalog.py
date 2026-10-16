"""Black-box contracts for work, edition and physical-copy identities."""
from __future__ import annotations

import copy
import unittest
import uuid

from app.library.catalog import create_physical_copy


class MemoryObjects:
    def __init__(self, item):
        self.items = {item["object_id"]: copy.deepcopy(item)}

    def object(self, object_id):
        if object_id not in self.items:
            raise ValueError("unknown object")
        return copy.deepcopy(self.items[object_id])

    def update(self, object_id, values, actor):
        item = self.object(object_id)
        item.update(copy.deepcopy(values))
        item["updated_by"] = actor
        self.items[object_id] = item
        return self.object(object_id)

    def create(self, values, actor):
        object_id = str(uuid.uuid4())
        item = {
            "object_id": object_id,
            "display_id": str(len(self.items) + 1),
            "created_by": actor,
            **copy.deepcopy(values),
        }
        self.items[object_id] = item
        return self.object(object_id)


def source_book(**changes):
    item = {
        "object_id": "8466806c-f4b1-48d2-8363-32f9166f83c1",
        "display_id": "1",
        "name": "Das Beispielbuch",
        "type": "book",
        "status": "active",
        "description": "Bibliografische Beschreibung",
        "identifier": "9780306406157",
        "location": "Wohnzimmer / Regal 2",
        "tags": ["buch", "isbn"],
        "fields": {
            "isbn": "9780306406157",
            "authors": "Ada Autor",
            "barcode": "COPY-ONE",
            "nfc_id": "NFC-ONE",
            "purchase_date": "2026-10-01",
            "library_location_id": "location-id",
            "library_location_code": "LIB-L0001",
        },
    }
    item.update(changes)
    return item


class LibraryCatalogContractTest(unittest.TestCase):
    def test_multiple_copies_share_work_and_edition_not_copy_identity(self):
        objects = MemoryObjects(source_book())

        source, first = create_physical_copy(objects, source_book()["object_id"], "reader")
        _, second = create_physical_copy(objects, first["object_id"], "reader")

        for created in (first, second):
            self.assertEqual(source["fields"]["library_work_id"], created["fields"]["library_work_id"])
            self.assertEqual(source["fields"]["library_edition_id"], created["fields"]["library_edition_id"])
            self.assertEqual("9780306406157", created["fields"]["isbn"])
        copy_ids = {
            source["fields"]["library_copy_id"],
            first["fields"]["library_copy_id"],
            second["fields"]["library_copy_id"],
        }
        self.assertEqual(3, len(copy_ids))
        self.assertEqual(3, len(objects.items))

    def test_physical_state_is_not_copied(self):
        objects = MemoryObjects(source_book())

        _, created = create_physical_copy(objects, source_book()["object_id"], "reader")

        self.assertEqual("", created["location"])
        self.assertEqual("Ada Autor", created["fields"]["authors"])
        for key in ("barcode", "nfc_id", "purchase_date", "library_location_id", "library_location_code"):
            self.assertNotIn(key, created["fields"])

    def test_existing_valid_identities_are_preserved(self):
        fields = dict(source_book()["fields"])
        identities = {
            "library_work_id": str(uuid.uuid4()),
            "library_edition_id": str(uuid.uuid4()),
            "library_copy_id": str(uuid.uuid4()),
        }
        fields.update(identities)
        objects = MemoryObjects(source_book(fields=fields))

        source, created = create_physical_copy(objects, source_book()["object_id"], "reader")

        for key, value in identities.items():
            self.assertEqual(value, source["fields"][key])
        self.assertEqual(identities["library_work_id"], created["fields"]["library_work_id"])
        self.assertEqual(identities["library_edition_id"], created["fields"]["library_edition_id"])

    def test_malformed_identity_fails_before_creating_copy(self):
        fields = dict(source_book()["fields"])
        fields["library_work_id"] = "not-a-uuid"
        objects = MemoryObjects(source_book(fields=fields))

        with self.assertRaisesRegex(ValueError, "library_work_id"):
            create_physical_copy(objects, source_book()["object_id"], "reader")

        self.assertEqual(1, len(objects.items))

    def test_non_book_is_rejected(self):
        objects = MemoryObjects(source_book(type="equipment", tags=[]))

        with self.assertRaisesRegex(ValueError, "Nur Bücher"):
            create_physical_copy(objects, source_book()["object_id"], "reader")

        self.assertEqual(1, len(objects.items))


if __name__ == "__main__":
    unittest.main()
