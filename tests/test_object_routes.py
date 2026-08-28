import json
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database


class ObjectRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "browser-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"})

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def test_sparse_historical_object_renders_in_list_and_detail(self):
        object_id = "10000000-0000-0000-0000-000000000001"
        directory = self.root / ".simpleoffice-meta" / "objects"
        directory.mkdir(parents=True)
        (directory / f"{object_id}.json").write_text(
            json.dumps({
                "object_id": object_id,
                "sequence_id": 1,
                "name": "Altbestand",
                "type": "Gerät",
                "document_ids": ["nicht-mehr-vorhanden"],
            }),
            encoding="utf-8",
        )

        listing = self.client.get("/documents/objects")
        detail = self.client.get(f"/documents/objects/{object_id}")

        self.assertEqual(200, listing.status_code)
        self.assertEqual(200, detail.status_code)
        self.assertIn("Altbestand", listing.get_data(as_text=True))
        self.assertIn("[Dokument fehlt]", detail.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
