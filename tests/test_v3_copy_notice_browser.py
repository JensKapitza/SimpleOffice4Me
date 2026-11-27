"""Explicit opt-in and one real Web Audio node on the authenticated copy feed."""
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from werkzeug.serving import make_server

from app import app
from app.db import ensure_auth_database
from app.settings_store import SettingsStore
from app.v3_activity import ActivityStore
from app.workflow_peer_notices import PEER_NOTICE


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional web-export extra")
class CopyNoticeSoundBrowserTest(unittest.TestCase):
    def test_new_paired_notice_sounds_after_a_real_click(self):
        from playwright.sync_api import Error, sync_playwright

        with sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(
                    executable_path=os.environ.get("SIMPLEOFFICE_COPY_NOTICE_CHROMIUM") or None,
                    ignore_default_args=["--mute-audio"],
                )
            except Error as exc:
                if "Executable doesn't exist" in str(exc):
                    self.skipTest("install Playwright Chromium")
                raise
            try:
                self._exercise(browser)
            finally:
                browser.close()

    def _exercise(self, browser):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "docs"
            config = {"DOCUMENT_ROOT": str(root), "DATABASE": str(Path(directory) / "users.sqlite"), "TESTING": True}
            saved = {key: app.config[key] for key in config}
            app.config.update(config)
            server = None
            try:
                with app.app_context():
                    ensure_auth_database()
                SettingsStore(root).save({"v3": {"v3.activity": True, "v3.automation": True}}, "owner")
                client = app.test_client()
                credentials = {"username": "owner", "password": "synthetic-tone-only"}
                client.post("/auth/register", data=credentials)
                client.post("/auth/login", data=credentials)
                cookie_name = app.config["SESSION_COOKIE_NAME"]
                cookie = client.get_cookie(cookie_name)
                self.assertIsNotNone(cookie)
                server = make_server("127.0.0.1", 0, app, threaded=True)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                origin = f"http://127.0.0.1:{server.server_port}"
                context = browser.new_context(service_workers="block")
                try:
                    context.add_cookies([{"name": cookie_name, "value": cookie.value, "url": origin}])
                    self._verify(context.new_page(), origin, root)
                finally:
                    context.close()
            finally:
                if server is not None:
                    server.shutdown()
                    thread.join(5)
                app.config.update(saved)

    def _verify(self, page, origin, root):
        page.add_init_script("""
            window.copyTones = [];
            const start = OscillatorNode.prototype.start;
            OscillatorNode.prototype.start = function(...args) {
                window.copyTones.push(this.frequency.value);
                return start.apply(this, args);
            };
        """)
        page.goto(origin + "/automation-v3", wait_until="domcontentloaded")
        button = page.locator("[data-copy-notice-sound]")
        self.assertEqual("false", button.get_attribute("aria-pressed"))
        self.assertEqual([], page.evaluate("() => window.copyTones"))
        button.click()
        page.wait_for_selector('[data-copy-notice-sound][aria-pressed="true"]')
        ActivityStore(root).record(PEER_NOTICE, "owner", event_id="a" * 64, source="synthetic.audio.smoke")
        page.wait_for_function("() => window.copyTones.length === 1", timeout=12_000)
        self.assertEqual(1, page.locator("[data-copy-notice-list] [data-notice-id]").count())
        with page.expect_response(lambda response: response.url.endswith("/automation-v3/copy-notices"), timeout=10_000):
            pass
        self.assertEqual([660], page.evaluate("() => window.copyTones"))
        button.click()
        self.assertEqual("false", button.get_attribute("aria-pressed"))
