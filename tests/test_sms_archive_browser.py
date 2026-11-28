"""Exercise repeated downloads on the same real archive page in Chromium."""
import importlib.util
import json
import os
from pathlib import Path
from threading import Thread
import unittest

from werkzeug.serving import make_server

from app import app
from app.sms_archive_store import SmsArchive
from tests.http_api_fixture import PublicHttpTestCase


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional web-export extra")
class SmsArchiveBrowserTests(PublicHttpTestCase):
    def test_attachment_does_not_leave_export_controls_locked(self):
        from playwright.sync_api import Error, sync_playwright

        root = Path(app.config["DOCUMENT_ROOT"])
        first = {"source": "a" * 32, "provider_id": 1, "date_ms": 1750000001000,
                 "direction": "inbox", "address": "Synthetic sender", "body": "Only this message"}
        SmsArchive(root, "owner").append([first, {**first, "provider_id": 2, "body": "Other message", "direction": "sent"}])
        cookie_name = app.config["SESSION_COOKIE_NAME"]
        cookie = self.client.get_cookie(cookie_name)
        self.assertIsNotNone(cookie)
        with make_server("127.0.0.1", 0, app, threaded=True) as server, sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(executable_path=os.environ.get("SIMPLEOFFICE_SMS_CHROMIUM") or None)
            except Error as exc:
                if "Executable doesn't exist" in str(exc):
                    self.skipTest("install Playwright Chromium")
                raise
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                context = browser.new_context(service_workers="block", accept_downloads=True)
                context.add_cookies([{"name": cookie_name, "value": cookie.value, "url": origin}])
                page = context.new_page()
                page.set_default_timeout(12000)
                page.goto(origin + "/communication/sms", wait_until="load")
                page.locator("#sms-direction").select_option("inbox")
                page.locator("#sms-date-from").fill("2025-06-15")
                page.locator("#sms-date-to").fill("2025-06-15")
                page.locator('button[name="page"][value="0"]').click()
                page.wait_for_load_state("load")
                self.assertEqual(origin + "/communication/sms", page.url)
                self.assertEqual("inbox", page.locator("#sms-direction").input_value())
                self.assertEqual(1, page.locator('input[name="selected"]').count())
                self.assertNotIn("Other message", page.locator("main").inner_text())
                page.evaluate("window.archivePageDidNotNavigate = true")
                form = page.locator('form[action="/communication/sms/export"]')
                form.locator("li").filter(has_text=first["body"]).locator('input[name="selected"]').check()
                backup = form.locator('button[type="submit"]:not([formaction])')
                draft = form.locator('button[formaction="/communication/sms/mail-draft"]')
                for _ in range(2):
                    with page.expect_download() as saved:
                        backup.click(no_wait_after=True)
                    content = json.loads(saved.value.path().read_bytes())
                    self.assertEqual([first], content["payload"]["records"])
                    page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    self.assertTrue(page.evaluate("window.archivePageDidNotNavigate"))
                    self.assertTrue(backup.is_enabled(), "download leaves the same page; it must not lock the form")
                    self.assertTrue(draft.is_enabled(), "the download must not block access to the draft")
                restore = page.locator('form[action="/communication/sms/restore"]')
                self.assertIsNone(restore.get_attribute("data-allow-multiple-submit"))
            finally:
                browser.close()
                server.shutdown()
                thread.join(timeout=5)
