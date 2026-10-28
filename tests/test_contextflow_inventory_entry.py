"""Bueroengel entry on /inventory plus origin back-link.

The entry is a plain GET link (no writes, no inventory data leaves the
page): visible only with the ContextFlow flag AND the documents feature,
carrying an allowlisted ``origin=inventory`` marker that the dashboard
answers with a fixed internal back link. Foreign origins are ignored.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flask import url_for

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class InventoryBueroengelEntryTest(unittest.TestCase):
    password = "contextflow-inventory-entry-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE",
                "DOCUMENT_ROOT",
                "TESTING",
                "TEST_CSRF_PROTECTION",
                "CONTEXTFLOW_ENABLED",
            )
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            CONTEXTFLOW_ENABLED=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin)"
                " VALUES (?,?,1)",
                ("owner", hash_password(self.password)),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin)"
                " VALUES (?,?,0)",
                ("berta", hash_password(self.password)),
            )
            berta_id = db.execute(
                "SELECT id FROM user WHERE username = 'berta'"
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO user_permission(user_id,feature,enabled,updated_at)"
                " VALUES (?,?,0,?)",
                (berta_id, "documents", "2026-10-28T00:00:00+00:00"),
            )
            db.commit()
        self.owner = self.login("owner")
        self.berta = self.login("berta")
        with app.test_request_context():
            self.inventory_url = url_for("inventory.index")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, username: str):
        client = app.test_client()
        client.post(
            "/auth/login",
            data={"username": username, "password": self.password},
        )
        return client

    def test_no_passive_cta_on_inventory(self) -> None:
        response = self.owner.get("/inventory")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"bueroengel-entry", response.data)
        self.assertNotIn("Büroengel öffnen".encode("utf-8"), response.data)

    def test_suggest_ui_visible_when_enabled(self) -> None:
        response = self.owner.get("/inventory")
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn(b'id="engel-suggest-box"', body)
        self.assertIn(b'id="analyze-photo"', body)
        self.assertIn(b"data-suggest-url=", body)

    def test_photo_actions_visible_when_enabled(self) -> None:
        response = self.owner.get("/inventory")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode("utf-8")
        self.assertIn('id="photo-actions"', html)
        self.assertIn('id="take-photo"', html)
        self.assertIn("Foto aufnehmen", html)
        self.assertIn('id="choose-photo"', html)
        self.assertIn("Foto auswählen", html)
        # Directly below the scanner controls, above the scan status.
        self.assertLess(
            html.index('id="start-barcode"'), html.index('id="photo-actions"')
        )
        self.assertLess(
            html.index('id="photo-actions"'), html.index('id="scan-status"')
        )
        # Plain buttons, never form submits.
        self.assertRegex(html, r'<button[^>]*type="button"[^>]*id="take-photo"')
        self.assertRegex(html, r'<button[^>]*type="button"[^>]*id="choose-photo"')

    def test_series_queue_visible_when_enabled(self) -> None:
        response = self.owner.get("/inventory")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode("utf-8")
        self.assertIn('id="photo-series"', html)
        self.assertIn('id="series-add"', html)
        self.assertIn("Fotos zur Serie hinzufügen", html)
        self.assertIn('id="series-capture"', html)
        self.assertIn("Serienfoto aufnehmen", html)
        self.assertIn('id="queue-list"', html)
        self.assertIn('id="queue-hint"', html)
        self.assertRegex(
            html,
            r'<input[^>]*id="photo-series-input"[^>]*'
            r'accept="image/jpeg,image/png,image/webp"[^>]*multiple',
        )
        # Below the single-photo actions, above the scan status.
        self.assertLess(
            html.index('id="photo-actions"'), html.index('id="photo-series"')
        )
        self.assertLess(
            html.index('id="photo-series"'), html.index('id="scan-status"')
        )
        # Plain buttons, never form submits.
        self.assertRegex(html, r'<button[^>]*type="button"[^>]*id="series-add"')
        self.assertRegex(html, r'<button[^>]*type="button"[^>]*id="series-capture"')

    def test_suggest_ui_omitted_when_flag_off(self) -> None:
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            response = self.owner.get("/inventory")
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"engel-suggest-box", response.data)
        self.assertNotIn(b"photo-actions", response.data)
        self.assertNotIn(b"take-photo", response.data)
        self.assertNotIn(b"choose-photo", response.data)
        self.assertNotIn(b"photo-series", response.data)
        self.assertNotIn(b"series-add", response.data)
        self.assertNotIn(b"series-capture", response.data)
        self.assertNotIn(b"queue-list", response.data)

    def test_inventory_denied_without_documents_feature(self) -> None:
        # Existing gate: auth.enforce_feature_permission maps the inventory
        # blueprint to the documents feature (FEATURE_BLUEPRINTS) and denies
        # with 403 before any markup renders. Route and rights untouched.
        response = self.berta.get("/inventory")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"engel-suggest-box", response.data)
        self.assertNotIn(b"photo-actions", response.data)
        self.assertNotIn(b"take-photo", response.data)
        self.assertNotIn(b"choose-photo", response.data)
        self.assertNotIn(b"photo-series", response.data)
        self.assertNotIn(b"series-add", response.data)
        self.assertNotIn(b"series-capture", response.data)
        self.assertNotIn(b"queue-list", response.data)

    def test_dashboard_back_link_with_inventory_origin(self) -> None:
        response = self.owner.get("/contextflow/", query_string={"origin": "inventory"})
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn("Zurück zum Inventar".encode("utf-8"), body)
        self.assertIn(f'href="{self.inventory_url}"'.encode(), body)

    def test_dashboard_ignores_foreign_origin(self) -> None:
        response = self.owner.get(
            "/contextflow/", query_string={"origin": "https://evil.example"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertNotIn("Zurück zum Inventar".encode("utf-8"), body)
        # No external URL is ever used as a target: the back-link href is a
        # fixed internal url_for (the hidden web-export form legitimately
        # echoes the current same-origin URL including its query string).
        self.assertNotIn(b'href="https://evil.example', body)
        self.assertNotIn(b"href='https://evil.example", body)


if __name__ == "__main__":
    unittest.main()
