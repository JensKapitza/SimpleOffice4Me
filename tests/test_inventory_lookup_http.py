"""Public HTTP contracts for ISBN metadata lookup."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database


class InventoryLookupHttpContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        keys = ("TESTING", "DATABASE", "DOCUMENT_ROOT", "TEST_CSRF_PROTECTION")
        saved = {key: app.config.get(key) for key in keys}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            DATABASE=str(root / "users.sqlite"),
            DOCUMENT_ROOT=str(root / "documents"),
            TEST_CSRF_PROTECTION=True,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('lookup-member','unused',0)")
            db.commit()
            user_id = db.execute(
                "SELECT id FROM user WHERE username='lookup-member'"
            ).fetchone()[0]
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def test_lookup_returns_provider_candidates_to_authenticated_client(self):
        """A valid ISBN lookup exposes both reviewable source candidates without caching them."""
        metadata = {
            "title": "Primary title",
            "metadata_source": "Open Library Search + Open Library",
            "metadata_candidates": [
                {"metadata_source": "Open Library Search", "title": "Primary title"},
                {"metadata_source": "Open Library", "title": "Alternate title"},
            ],
        }
        with patch("app.inventory.lookup_book_metadata", return_value=metadata):
            response = self.client.get("/inventory/lookup?isbn=9780131103627")

        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual("Primary title", response.json["title"])
        self.assertEqual(metadata["metadata_candidates"], response.json["metadata_candidates"])
        self.assertTrue(response.json["ok"])

    def test_lookup_rejects_invalid_isbn_before_provider_access(self):
        """An invalid checksum is a 400 API error and never starts a provider lookup."""
        with patch("app.inventory.lookup_book_metadata") as lookup:
            response = self.client.get("/inventory/lookup?isbn=9780131103628")

        self.assertEqual(400, response.status_code)
        self.assertEqual({"ok": False, "error": "Ungültige ISBN."}, response.json)
        lookup.assert_not_called()

    def test_lookup_requires_login(self):
        """The metadata endpoint remains unavailable to unauthenticated clients."""
        response = app.test_client().get("/inventory/lookup?isbn=9780131103627")

        self.assertIn(response.status_code, (302, 401))


if __name__ == "__main__":
    unittest.main()
