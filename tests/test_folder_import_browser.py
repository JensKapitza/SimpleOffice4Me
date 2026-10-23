"""Browser behavior for folder picker fallback and bounded path manifests."""
from __future__ import annotations

import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from app import app
from app.db import ensure_auth_database


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the web-export extra")
class FolderImportBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            ensure_auth_database()
        client = app.test_client()
        credentials = {"username": "folder-import-browser", "password": "browser-test-password"}
        client.post("/auth/register", data=credentials)
        client.post("/auth/login", data=credentials)
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)
        app.config.update(self.saved)
        self.temp.cleanup()

    def _select_folder_files(self, page, paths):
        page.locator("#document-folder").evaluate(
            """(input, paths) => {
              const transfer = new DataTransfer();
              paths.forEach((path, index) => {
                const name = path.split('/').pop();
                const file = new File(['payload-' + index], name, {type: 'text/plain'});
                Object.defineProperty(file, 'webkitRelativePath', {configurable: true, value: path});
                transfer.items.add(file);
              });
              input.files = transfer.files;
              input.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            paths,
        )

    def test_manifest_and_upload_controls_switch_with_selected_source(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 360, "height": 800})
            context.add_cookies([{
                "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                "value": self.cookie,
                "url": self.base_url,
            }])
            try:
                page = context.new_page()
                page.goto(f"{self.base_url}/documents/")
                self.assertIsNone(page.locator("#document-folder").get_attribute("webkitdirectory"))
                self.assertTrue(page.locator("#document-upload-form button[type=submit]").is_disabled())
                supported = page.locator("#document-folder").evaluate("input => 'webkitdirectory' in input")
                if not supported:
                    self.skipTest("Chromium on this host does not expose webkitdirectory")
                self._select_folder_files(page, ["root/a.txt", "root/nested/b.txt"])
                upload_state = page.locator("#document-upload-form").evaluate(
                    """form => ({
                      submitDisabled: form.querySelector('button[type=submit]').disabled,
                      submitAriaDisabled: form.querySelector('button[type=submit]').getAttribute('aria-disabled'),
                      submitDisabledPseudo: form.querySelector('button[type=submit]').matches(':disabled'),
                      selectedFiles: document.getElementById('document-folder').files.length,
                      folderImport: document.getElementById('document-folder-import').value,
                      manifest: document.getElementById('document-folder-path-manifest').value,
                      status: document.getElementById('document-selection').textContent
                    })"""
                )
                self.assertTrue(
                    page.locator("#document-upload-form button[type=submit]").is_enabled(),
                    f"folder selection did not enable upload: {upload_state}",
                )
                self.assertIsNone(upload_state["submitAriaDisabled"])
                self.assertEqual("1", page.locator("#document-folder-import").input_value())
                self.assertEqual(
                    ["root/a.txt", "root/nested/b.txt"],
                    page.locator("#document-folder-path-manifest").evaluate("field => JSON.parse(field.value)"),
                )
                self.assertEqual(1, page.locator('input[name="relative_paths"]').count())
                self.assertEqual("files", page.locator("#document-folder").get_attribute("name"))
                self.assertTrue(page.locator("#document-files").get_attribute("name") is None)
                self.assertTrue(page.locator("#archive").is_disabled())
                self.assertIn("2 Datei(en)", page.locator("#document-selection").inner_text())

                page.locator("#document-files").evaluate(
                    """input => {
                      const transfer = new DataTransfer();
                      transfer.items.add(new File(['plain'], 'plain.txt', {type: 'text/plain'}));
                      input.files = transfer.files;
                      input.dispatchEvent(new Event('change', {bubbles: true}));
                    }"""
                )
                self.assertEqual("0", page.locator("#document-folder-import").input_value())
                self.assertTrue(page.locator("#document-folder-path-manifest").is_disabled())
                self.assertTrue(page.locator("#archive").is_enabled())
                self.assertEqual("files", page.locator("#document-files").get_attribute("name"))
            finally:
                context.close()
                browser.close()

    def test_webkitdirectory_attribute_exists_only_during_picker_activation(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_cookies([{
                "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                "value": self.cookie,
                "url": self.base_url,
            }])
            try:
                page = context.new_page()
                page.goto(f"{self.base_url}/documents/")
                folder = page.locator("#document-folder")
                if not folder.evaluate("input => 'webkitdirectory' in input"):
                    self.skipTest("Chromium on this host does not expose webkitdirectory")
                self.assertIsNone(folder.get_attribute("webkitdirectory"))
                page.evaluate("""() => {
                  window.folderModeAtClick = false;
                  const nativeClick = HTMLInputElement.prototype.click;
                  HTMLInputElement.prototype.click = function() {
                    if (this.id === 'document-folder') {
                      window.folderModeAtClick = this.hasAttribute('webkitdirectory');
                    }
                    return nativeClick.call(this);
                  };
                }""")
                with page.expect_file_chooser() as chooser:
                    page.locator("#pick-document-folder").click()
                chooser.value.set_files([])
                self.assertTrue(page.evaluate("window.folderModeAtClick"))
                self.assertIsNone(folder.get_attribute("webkitdirectory"))
            finally:
                context.close()
                browser.close()

    def test_browser_disables_batches_above_file_and_manifest_limits(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_cookies([{
                "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                "value": self.cookie,
                "url": self.base_url,
            }])
            try:
                page = context.new_page()
                page.goto(f"{self.base_url}/documents/")
                supported = page.locator("#document-folder").evaluate("input => 'webkitdirectory' in input")
                if not supported:
                    self.skipTest("Chromium on this host does not expose webkitdirectory")
                self._select_folder_files(page, [f"root/file-{index}.txt" for index in range(1001)])
                self.assertTrue(page.locator("#document-upload-form button[type=submit]").is_disabled())
                self.assertIn("Maximal 1000", page.locator("#document-selection").inner_text())

                deep = "root/" + "/".join(["d"] * 1000) + "/"
                self._select_folder_files(page, [deep + f"file-{index}.txt" for index in range(210)])
                self.assertTrue(page.locator("#document-upload-form button[type=submit]").is_disabled())
                self.assertIn("Pfadliste ist zu groß", page.locator("#document-selection").inner_text())
            finally:
                context.close()
                browser.close()

    def test_mobile_webkit_picker_has_an_explicit_unsupported_state(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.webkit.launch()
            context = browser.new_context(**playwright.devices["iPhone 14"])
            context.add_cookies([{
                "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                "value": self.cookie,
                "url": self.base_url,
            }])
            try:
                page = context.new_page()
                page.goto(f"{self.base_url}/documents/")
                supported = page.locator("#document-folder").evaluate("input => 'webkitdirectory' in input")
                self.assertEqual(not supported, page.locator("#pick-document-folder").is_disabled())
                if not supported:
                    self.assertIn("nicht unterstützt", page.locator("#pick-document-folder").get_attribute("title"))
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
