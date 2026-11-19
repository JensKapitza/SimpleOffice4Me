"""Narrow viewport regression for request-scoped document list pages."""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from app import app
from app import db as database
from app.document_store import DocumentStore
from app.password_security import hash_password


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright browser extra is required")
class DocumentListPaginationBrowserTests(unittest.TestCase):
    def test_mobile_index_and_inbox_hide_raw_totals_and_private_chat_documents(self):
        with tempfile.TemporaryDirectory() as temp:
            previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
            root = Path(temp) / "documents"
            app.config.update(
                TESTING=True,
                DATABASE=str(Path(temp) / "users.sqlite"),
                DOCUMENT_ROOT=str(root),
            )
            server = None
            thread = None
            try:
                with app.app_context():
                    database.ensure_auth_database()
                    db = database.get_db()
                    db.execute(
                        "INSERT INTO user(username,password,display_name,is_admin,"
                        "is_disabled,auth_version,created_at,updated_at) "
                        "VALUES (?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                        ("mobile-viewer", hash_password("mobile-viewer-test-password"), "Viewer"),
                    )
                    db.commit()

                store = DocumentStore(root)
                root.mkdir(parents=True, exist_ok=True)
                for number in range(120):
                    (root / f"visible-{number:03}.txt").write_text(f"visible record {number}", encoding="utf-8")
                store.scan()
                for number in (0, 1):
                    document = store.get_document(root / f"visible-{number:03}.txt")
                    store.update_metadata(
                        document["document_id"],
                        attributes={"chat_attachment": {
                            "visibility": "chat",
                            "allowed_local_users": ["another-user"],
                        }},
                        author="setup-admin",
                    )

                client = app.test_client()
                response = client.post(
                    "/auth/login",
                    data={"username": "mobile-viewer", "password": "mobile-viewer-test-password"},
                )
                self.assertIn(response.status_code, (200, 302))
                cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
                self.assertIsNotNone(cookie)

                server = make_server("127.0.0.1", 0, app, threaded=True)
                base_url = f"http://127.0.0.1:{server.server_port}"
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()

                from playwright.sync_api import sync_playwright

                with sync_playwright() as playwright:
                    chromium = shutil.which("chromium") or playwright.chromium.executable_path
                    if not Path(chromium).is_file():
                        self.skipTest("Chromium browser binary is not installed")
                    browser = playwright.chromium.launch(executable_path=chromium)
                    context = browser.new_context(viewport={"width": 375, "height": 812}, is_mobile=True)
                    context.add_cookies([{
                        "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                        "value": cookie.value,
                        "url": base_url,
                    }])
                    page = context.new_page()
                    for route in ("/documents/", "/documents/inbox"):
                        with self.subTest(route=route):
                            response = page.goto(base_url + route)
                            self.assertIsNotNone(response)
                            self.assertEqual(200, response.status)
                            header = page.locator(".card-header small").inner_text()
                            self.assertIn("1–100", header)
                            self.assertNotIn("120", header)
                            self.assertNotRegex(header, r"\b(?:of|von)\b")
                            next_link = page.locator("nav a[rel=next]")
                            self.assertTrue(next_link.is_visible())
                            self.assertNotIn("visible-000.txt", page.locator("main").inner_text())
                            self.assertNotIn("visible-001.txt", page.locator("main").inner_text())
                            self.assertLessEqual(
                                page.locator("body").evaluate("node => node.scrollWidth"),
                                page.evaluate("window.innerWidth") + 1,
                            )
                            next_link.click()
                            final_header = page.locator(".card-header small").inner_text()
                            self.assertIn("101–118", final_header)
                            self.assertIn("118", final_header)
                            self.assertNotIn("120", final_header)
                            self.assertFalse(page.locator("nav a[rel=next]").count())
                            self.assertNotIn("visible-000.txt", page.locator("main").inner_text())
                            self.assertNotIn("visible-001.txt", page.locator("main").inner_text())
                    page.goto(base_url + "/documents/search?q=visible")
                    found = []
                    for count in (25, 25, 25, 25, 18):
                        paths = page.locator("main .list-group-item strong").all_text_contents()
                        self.assertEqual(count, len(paths))
                        found.extend(paths)
                        more = page.locator("main nav a[rel=next]")
                        if count == 25:
                            self.assertIn("cursor=", more.get_attribute("href"))
                            more.click()
                        else:
                            self.assertEqual(0, more.count())
                    self.assertEqual({f"visible-{i:03}.txt" for i in range(2, 120)}, set(found))
                    context.close()
                    browser.close()
            finally:
                if server is not None:
                    server.shutdown()
                if thread is not None:
                    thread.join(timeout=5)
                app.config.update(previous)
