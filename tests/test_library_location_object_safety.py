from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.library.store import LibraryStore
from app.object_store import ObjectStore


class LibraryLocationObjectSafetyTests(unittest.TestCase):
    def test_sync_does_not_claim_unmanaged_shelf_with_same_identifier(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            objects = ObjectStore(root)
            unrelated = objects.create(
                {
                    "name": "Privates Regal",
                    "type": "shelf",
                    "status": "active",
                    "description": "Nicht von der Bibliothek verwaltet",
                    "identifier": "LIB-L0001",
                    "location": "",
                    "tags": "Privat",
                    "fields": {},
                },
                "tester",
            )

            library = LibraryStore(root)
            library.directory.mkdir(parents=True, exist_ok=True)
            library.state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "next_location": 2,
                        "locations": [
                            {
                                "location_id": "legacy-location",
                                "code": "LIB-L0001",
                                "name": "Bibliotheksregal",
                                "parent_id": "",
                                "created_at": "2026-01-01T00:00:00Z",
                                "created_by": "tester",
                            }
                        ],
                        "assignments": {},
                        "events": [],
                    }
                ),
                encoding="utf-8",
            )

            library.sync_location_objects("tester")
            location = library.location("legacy-location")
            managed = objects.object(location["object_id"])

        self.assertNotEqual(unrelated["object_id"], location["object_id"])
        self.assertEqual("legacy-location", managed["fields"]["library_location_id"])
        self.assertEqual({}, unrelated["fields"])


if __name__ == "__main__":
    unittest.main()
