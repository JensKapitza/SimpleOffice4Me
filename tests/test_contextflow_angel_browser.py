"""Browser regression for the inventory angel dialog inside its save form."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from app import app
from app import db as database
from app.password_security import hash_password


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional web-export extra")
class ContextFlowAngelBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
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
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(base / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            CONTEXTFLOW_ENABLED=True,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,is_disabled,"
                "auth_version,created_at,updated_at) VALUES(?,?,?,1,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("angel-browser-admin", hash_password("Angel-Browser-Passphrase-42"), "Admin"),
            )
            db.commit()
        client = app.test_client()
        response = client.post(
            "/auth/login",
            data={"username": "angel-browser-admin", "password": "Angel-Browser-Passphrase-42"},
        )
        self.assertIn(response.status_code, {200, 302})
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server_thread.join(timeout=5)
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_dialog_close_and_partial_adoption_leave_inventory_form_intact(self) -> None:
        from playwright.sync_api import expect, sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_cookies([
                {
                    "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                    "value": self.cookie,
                    "url": self.base_url,
                }
            ])
            try:
                page = context.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.route(
                    "**/contextflow/inventory-suggest",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "provider_label": "Lokaler Test",
                                "suggestion": {
                                    "object_name": "Vorgeschlagener Scanner",
                                    "item_type": "book",
                                    "note": "Neue Beschreibung",
                                    "tags": ["scan", "inventar"],
                                },
                            }
                        ),
                    ),
                )
                page.goto(self.base_url + "/inventory")
                title = page.locator("#title")
                item_type = page.locator("#item-type")
                tags = page.locator("#tags")
                description = page.locator("#description")
                item_type.fill("object")
                tags.fill("vorhanden")
                description.fill("Bestehende Beschreibung")

                with page.expect_response(
                    lambda response: response.url.endswith("/contextflow/inventory-suggest")
                ):
                    page.locator("#photo").set_input_files(
                        {"name": "synthetic.jpg", "mimeType": "image/jpeg", "buffer": b"\xff\xd8\xfftest"}
                    )
                expect(page.locator("#object-suggestion")).to_be_visible()
                page.locator("#engel-details").click()
                dialog = page.locator("#engel-suggest-dialog")
                self.assertTrue(dialog.evaluate("element => element.open"))
                self.assertEqual(
                    "inventory-form",
                    page.locator("#inventory-save").evaluate("element => element.form?.id"),
                )

                page.locator("[data-engel-close]").click()
                self.assertFalse(dialog.evaluate("element => element.open"))
                page.locator("#engel-details").click()
                page.locator("#engel-part-type").uncheck()
                page.locator("#engel-part-tags").uncheck()
                page.locator("#engel-adopt-parts").click()

                self.assertEqual("Vorgeschlagener Scanner", title.input_value())
                self.assertEqual("object", item_type.input_value())
                self.assertEqual("vorhanden", tags.input_value())
                self.assertEqual("Bestehende Beschreibung", description.input_value())
                self.assertEqual("inventory-form", page.locator("#inventory-save").evaluate("element => element.form?.id"))
                self.assertEqual([], page_errors)
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
