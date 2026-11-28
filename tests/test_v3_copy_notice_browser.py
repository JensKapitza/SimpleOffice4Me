"""Explicit opt-in and one real Web Audio node on the authenticated copy feed."""
import importlib.util
from io import BytesIO
import os
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
import threading
import unittest

from werkzeug.serving import make_server

from app import app
from app.db import ensure_auth_database
from app.document_store import DocumentStore
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from app.settings_store import SettingsStore
from app.todo_store import TodoStore
from app.v3_activity import ActivityStore
from app.v3_copy_notices import NOTICE
from app.workflow_peer_notices import PEER_NOTICE


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional web-export extra")
class CopyNoticeSoundBrowserTest(unittest.TestCase):
    def test_paired_and_real_local_copy_sound_after_explicit_choice(self):
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
                SettingsStore(root).save({"v3": {
                    "v3.activity": True, "v3.automation": True, "v3.federation": True, "v3.jobs": True,
                }}, "owner")
                with app.app_context():
                    FederationStore(root).save_peer(
                        "preview-peer", "Preview Peer", "https://example.invalid", token_urlsafe(30),
                        policy={"data_classes": {"workflow_notices": {"send": True}}},
                    )
                    FederationTrustStore(root).set_trust(
                        "preview-peer", trust_level="NORMAL", verification="VERIFIED_MUTUAL",
                    )
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
                    self._verify_delivery_preview(context.new_page(), origin, root)
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
        read = page.locator("[data-copy-read-button]")
        notice = page.locator("[data-copy-notice-list] [data-notice-id]")
        self.assertEqual("0", notice.get_attribute("data-notice-read"))
        with page.expect_response(lambda response: response.url.endswith("/copy-notices/read")) as receipt:
            read.focus()
            page.keyboard.press("Enter")
        self.assertEqual(204, receipt.value.status)
        page.wait_for_selector('[data-notice-read="1"]')
        self.assertEqual("Read", notice.locator("[data-notice-read-label]").inner_text())
        self.assertEqual("Visible notices marked as read.", page.locator("[data-copy-read-status]").inner_text())
        page.reload(wait_until="domcontentloaded")
        self.assertEqual("1", notice.get_attribute("data-notice-read"))
        self.assertEqual("false", button.get_attribute("aria-pressed"))
        source = page.locator("[data-copy-notice-sound-source]")
        source.select_option("local")
        csrf = page.locator("[data-copy-read-token]").input_value()
        headers = {"X-CSRF-Token": csrf}
        activate = page.request.post(
            origin + "/automation-v3/copy-notice", headers=headers, form={"source": "browser"}, max_redirects=0,
        )
        self.assertEqual(302, activate.status)
        button.click()
        page.wait_for_selector('[data-copy-notice-sound][aria-pressed="true"]')
        preview = page.locator("[data-copy-notice-test-sound]")
        preview.click()
        page.wait_for_function("() => window.copyTones.length === 1")
        ActivityStore(root).record(PEER_NOTICE, "owner", event_id="b" * 64, source="synthetic.audio.smoke")
        page.wait_for_selector(f'[data-notice-id="{"b" * 64}"]', timeout=12_000)
        self.assertEqual([660], page.evaluate("() => window.copyTones"))
        (root / "copies").mkdir()
        document = DocumentStore(root).import_upload(BytesIO(b"audio smoke"), "synthetic.txt", "owner")
        copied = page.request.post(
            origin + f"/documents/{document['document_id']}/copy", headers=headers,
            form={"destination_folder": "copies"}, max_redirects=0,
        )
        self.assertEqual(302, copied.status)
        self.assertEqual(b"audio smoke", (root / "copies" / "synthetic.txt").read_bytes())
        page.wait_for_function("() => window.copyTones.length === 2", timeout=12_000)
        source.select_option("all")
        self.assertEqual("false", button.get_attribute("aria-pressed"))
        self.assertTrue(preview.is_disabled())

        tasks = TodoStore(root)
        self.assertEqual([], tasks.items("owner"))
        recorded, = ActivityStore(root).list(actor="owner", event_type=NOTICE)
        local_notice = page.locator(f'[data-copy-notice-list] [data-notice-id="{recorded.event_id}"]')
        with page.expect_response(lambda response: response.url.endswith("/copy-notices/task")) as followup:
            local_notice.locator('button[type="submit"]').click()
        self.assertEqual(303, followup.value.status)
        page.wait_for_url("**/tasks/#task-*")
        task, = tasks.items("owner")
        self.assertEqual("Kopie prüfen / Review copy", task["title"])
        self.assertTrue(page.locator(f"#task-{task['id']}").is_visible())

    def _verify_delivery_preview(self, page, origin, root):
        from app.v3_automation import AutomationStore
        from app.v3_jobs import JobStore

        rules = AutomationStore(root)
        jobs = JobStore(root)
        existing_ids = {rule.rule_id for rule in rules.list("owner")}
        existing_jobs = jobs.list()
        page.goto(origin + "/automation-v3", wait_until="domcontentloaded")
        page.locator("#copy-send-destination").fill("b" * 32)
        page.locator("#copy-send-source").select_option("job")
        page.locator("#copy-send-delay").select_option("30")
        page.locator("#copy-send-deadline").select_option("600")
        page.locator("#copy-send-zone").fill("Europe/Berlin")
        page.locator('button[name="operation"][value="preview"]').click()
        page.wait_for_selector("[data-peer-rule-preview]")
        self.assertEqual(3, page.locator("[data-peer-rule-preview] time").count())
        self.assertEqual("job", page.locator("#copy-send-source").input_value())
        self.assertEqual("600", page.locator("#copy-send-deadline").input_value())
        self.assertEqual(existing_ids, {rule.rule_id for rule in rules.list("owner")})
        self.assertEqual(existing_jobs, jobs.list())
        page.locator('button[name="operation"][value="save"]').click()
        page.wait_for_url(origin + "/automation-v3")
        added, = [rule for rule in rules.list("owner") if rule.rule_id not in existing_ids]
        self.assertEqual("document.copy_job_completed", added.trigger["name"])
        self.assertEqual(existing_jobs, jobs.list())
