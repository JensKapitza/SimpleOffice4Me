"""Public HTTP contracts for ISBN metadata lookup."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.book_metadata_cache import BOOK_METADATA_CACHE_TTL_SECONDS, BookMetadataCache


class InventoryLookupHttpContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.document_root = root / "documents"
        keys = ("TESTING", "DATABASE", "DOCUMENT_ROOT", "TEST_CSRF_PROTECTION")
        saved = {key: app.config.get(key) for key in keys}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            DATABASE=str(root / "users.sqlite"),
            DOCUMENT_ROOT=str(self.document_root),
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
        """A valid ISBN lookup exposes review candidates and caches only normalized fields."""
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

    def test_fresh_cache_avoids_a_second_provider_request_and_rate_limit(self):
        metadata = {
            "title": "Cached title",
            "description": "Cached description",
            "metadata_source": "Open Library Search",
            "metadata_candidates": [{"metadata_source": "Open Library Search", "title": "Cached title"}],
            "lookup_errors": ["must-not-be-persisted"],
            "provider_raw": "must-not-be-persisted",
        }
        with patch("app.inventory.lookup_book_metadata", return_value=metadata) as lookup:
            first = self.client.get("/inventory/lookup?isbn=9780131103627")
            second = self.client.get("/inventory/lookup?isbn=9780131103627")

        self.assertEqual(200, first.status_code)
        self.assertEqual("miss", first.json["metadata_cache"])
        self.assertEqual(200, second.status_code)
        self.assertEqual("fresh", second.json["metadata_cache"])
        self.assertEqual("Cached title", second.json["title"])
        self.assertNotIn("lookup_errors", second.json)
        self.assertNotIn("provider_raw", second.json)
        self.assertEqual(1, lookup.call_count)

    def test_stale_cache_is_used_only_for_a_complete_provider_outage(self):
        metadata = {
            "title": "Offline title",
            "metadata_source": "Open Library Search",
            "metadata_candidates": [{"metadata_source": "Open Library Search", "title": "Offline title"}],
        }
        with patch("app.inventory.lookup_book_metadata", return_value=metadata):
            first = self.client.get("/inventory/lookup?isbn=9780131103627")
        self.assertEqual(200, first.status_code)

        cache_path = BookMetadataCache(self.document_root).path
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        cache["entries"]["9780131103627"]["cached_at_epoch"] = (
            time.time() - BOOK_METADATA_CACHE_TTL_SECONDS - 1
        )
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
        outage = {
            "lookup_errors": ["openlibrary-search:TimeoutError"],
            "lookup_reachable": False,
            "metadata_candidates": [],
        }
        with (
            patch("app.inventory.InventoryEnrichmentStore.consume_rate_limit", return_value=(True, 0)),
            patch("app.inventory.lookup_book_metadata", return_value=outage) as lookup,
        ):
            response = self.client.get("/inventory/lookup?isbn=9780131103627")

        self.assertEqual(200, response.status_code)
        self.assertEqual("stale", response.json["metadata_cache"])
        self.assertEqual("Offline title", response.json["title"])
        self.assertIn("ältere lokal gespeicherte Metadaten", response.json["lookup_warning"])
        self.assertEqual(1, lookup.call_count)

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
