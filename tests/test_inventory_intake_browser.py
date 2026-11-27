"""Real Chromium acceptance with typed synthetic barcodes, no device or provider."""
from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import os
import threading
import unittest
from unittest import mock

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

from PIL import Image
from werkzeug.serving import make_server

from app import app
from app.capture_inbox_store import CaptureInboxStore
from app.library.store import LibraryStore
from app.object_store import ObjectStore
from tests.http_api_fixture import PublicHttpTestCase

INTAKE = "/inventory/capture/intake"
FIRST = "9780140328721"
SECOND = "9780261103573"


class InventoryIntakeBrowserTests(PublicHttpTestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            message = "Use the project's [test,web-export] extras and Playwright full Chromium."
            if os.getenv("SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED") == "1":
                raise RuntimeError(message)
            raise unittest.SkipTest(message)  # Standard unit discovery is browser-independent.
        if os.getenv("SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED") != "1":
            raise unittest.SkipTest("Set SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED=1 for the Chromium gate")

    def setUp(self):
        super().setUp()
        self.root = app.config["DOCUMENT_ROOT"]
        self.objects = ObjectStore(self.root)
        self.library = LibraryStore(self.root)
        self.first = self._existing("Existing A", "0-14-032872-6", "First existing shelf")
        self._existing("Existing B", FIRST, "Second existing shelf")
        self._existing("Different edition", SECOND, "Different existing shelf")
        self._existing("Other person's edition", FIRST, "Do not show this path", actor="other")
        self.selected = self.library.create_location("Today's box", "owner")["location_id"]
        self.next_box = self.library.create_location("Next batch box", "owner")["location_id"]
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._shutdown)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def _existing(self, title, code, place, *, actor="owner"):
        return self.objects.create({"name": title, "type": "book", "identifier": code, "location": place}, actor)

    def _shutdown(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)
        self.server.server_close()

    def _sign_in(self, page):
        page.goto(self.origin + "/auth/login")
        german = page.locator('form button[name="language"][value="de"]')
        if german.is_enabled():
            with page.expect_navigation(wait_until="domcontentloaded"):
                german.click()
        expect(page.locator("html")).to_have_attribute("lang", "de")
        username = page.locator('input[name="username"]')
        username.fill("owner")
        page.locator('input[name="password"]').fill("test-only-api-password")
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.locator("form").filter(has=username).locator('button[type="submit"]').click()
        page.goto(self.origin + INTAKE, wait_until="domcontentloaded")
        expect(page.locator("html")).to_have_attribute("lang", "de")
        expect(page.locator("#intake-capture")).to_be_visible()

    @staticmethod
    def _scan(page, barcode, expected_count):
        page.locator("#intake-barcode").fill(barcode)
        page.locator("#intake-capture button[type=submit]").tap()
        cards = page.locator("article[id^=draft-]")
        expect(cards).to_have_count(expected_count)
        return cards.nth(expected_count - 1)

    @staticmethod
    @contextmanager
    def _mobile_context(playwright):
        configured = os.getenv("SIMPLEOFFICE_INTAKE_CHROMIUM")
        engine = {"executable_path": configured} if configured else {"channel": "chromium"}
        browser = playwright.chromium.launch(headless=True, **engine)
        try:
            context = browser.new_context(viewport={"width": 390, "height": 844},
                                          is_mobile=True, has_touch=True, service_workers="block")
            try:
                yield context
            finally:
                context.close()
        finally:
            browser.close()

    def test_mobile_card_shows_title_source_before_and_after_confirming(self):
        with sync_playwright() as playwright, self._mobile_context(playwright) as context:
            page = context.new_page()
            page.route("**/*", lambda route: route.continue_()
                       if route.request.url.startswith(self.origin + "/") else route.abort())
            self._sign_in(page)
            photo = BytesIO()
            Image.new("RGB", (32, 32), "white").save(photo, "PNG")
            page.locator("#intake-photo").set_input_files({
                "name": "synthetic-label.png", "mimeType": "image/png", "buffer": photo.getvalue(),
            })
            card = page.locator("article[id^=draft-]")
            expect(card).to_have_count(1)
            editor = card.locator("[data-intake-editor]")
            editor.locator('input[name="title"]').fill("Manual label")
            editor.locator('select[name="kind"]').select_option("object")
            with page.expect_navigation(wait_until="domcontentloaded"):
                editor.get_by_role("button", name="Entwurf aktualisieren").tap()

            title = card.locator("[data-intake-card-title]")
            expect(title).to_have_text("Manual label")
            # Only the OCR adapter is a fixture here. The HTTPS smoke exercises real Tesseract.
            with mock.patch("app.object_vision._run_tesseract", return_value={
                "status": "completed", "text": "Titel: Label from photo",
            }), page.expect_navigation(wait_until="domcontentloaded"):
                card.get_by_role("button", name="Text lokal erkennen").tap()
            with page.expect_navigation(wait_until="domcontentloaded"):
                card.get_by_role("button", name="Titel von OCR auswählen", exact=True).tap()
            expect(title).to_have_text("Label from photo")
            expect(card.locator("[data-intake-title-source]")).to_have_text("OCR")
            expect(editor.locator('input[name="title"]')).to_have_value("Manual label")
            review = card.locator('[data-intake-review-field="title"]')
            expect(review).to_contain_text("Label from photo")
            expect(review).to_contain_text("OCR")
            self.assertFalse(any(item["name"] == "Label from photo" for item in self.objects.objects()))

            with page.expect_navigation(wait_until="domcontentloaded"):
                editor.get_by_role("button", name="Als neues Exemplar lokal speichern").tap()
            expect(card.locator("[data-intake-state]")).to_have_text("Bestätigt")
            expect(review).to_contain_text("OCR")
            expect(card.get_by_role("link", name="Inventareintrag öffnen")).to_be_visible()
            self.assertEqual(1, sum(item["name"] == "Label from photo" for item in self.objects.objects()))

    def test_mobile_mixed_batch_destination_is_optional_and_only_applies_to_future_drafts(self):
        original = self.objects.object(self.first["object_id"])
        with sync_playwright() as playwright, self._mobile_context(playwright) as context:
            page = context.new_page()
            page.route("**/*", lambda route: route.continue_()
                       if route.request.url.startswith(self.origin + "/") else route.abort())
            self._sign_in(page)
            batch = page.locator("#intake-batch-location")
            expect(batch).to_have_value("")
            batch.select_option(self.selected)
            first = self._scan(page, FIRST, 1)
            expect(first.locator('select[name="location_id"]')).to_have_value(self.selected)
            expect(batch).to_have_value(self.selected)

            photo = BytesIO()
            Image.new("RGB", (32, 32), "orange").save(photo, "PNG")
            page.locator("#intake-photo").set_input_files({
                "name": "synthetic-item.png", "mimeType": "image/png", "buffer": photo.getvalue(),
            })
            cards = page.locator("article[id^=draft-]")
            expect(cards).to_have_count(2)
            second = cards.nth(1)
            expect(second.locator('select[name="location_id"]')).to_have_value(self.selected)
            expect(second.locator("img")).to_be_visible()

            # Correct one draft without affecting the selection for the next scan.
            first.locator('select[name="location_id"]').select_option("")
            with page.expect_navigation(wait_until="domcontentloaded"):
                first.get_by_role("button", name="Entwurf aktualisieren").tap()
            expect(first.locator('select[name="location_id"]')).to_have_value("")
            expect(batch).to_have_value(self.selected)

            batch.select_option(self.next_box)
            third = self._scan(page, "036000291452", 3)
            expect(third.locator('select[name="location_id"]')).to_have_value(self.next_box)
            # The picker can be present before deferred scripts have restored it.
            expect(batch).to_be_enabled()
            expect(batch).to_have_value(self.next_box)
            batch.select_option("")
            expect(batch).to_have_value("")
            fourth = self._scan(page, SECOND, 4)
            fourth_id = fourth.get_attribute("id").removeprefix("draft-")
            stored = CaptureInboxStore(self.root).get(fourth_id, "owner")
            self.assertEqual("", stored["intake"]["manual"]["location_id"])
            expect(fourth.locator('select[name="location_id"]')).to_have_value("")
            expect(batch).to_have_value("")
            expect(cards.nth(0).locator('select[name="location_id"]')).to_have_value("")
            expect(cards.nth(1).locator('select[name="location_id"]')).to_have_value(self.selected)
            expect(cards.nth(2).locator('select[name="location_id"]')).to_have_value(self.next_box)
            self.assertEqual(original, self.objects.object(self.first["object_id"]))
            entries = CaptureInboxStore(self.root).list_intake("owner")
            self.assertEqual(4, len(entries))
            self.assertTrue(all(not entry["intake"]["confirmed"] for entry in entries))

    def test_mobile_exact_copy_review_and_ordered_next_scan(self):
        original = self.objects.object(self.first["object_id"])
        with sync_playwright() as playwright, self._mobile_context(playwright) as context:
            page = context.new_page()
            outbound = []

            def local_only(route):
                if route.request.url.startswith(self.origin + "/"):
                    route.continue_()
                else:
                    outbound.append(route.request.url)
                    route.abort()

            page.route("**/*", local_only)
            self._sign_in(page)
            card = self._scan(page, FIRST, 1)
            draft_id = card.get_attribute("id").removeprefix("draft-")
            results = card.locator("[data-intake-existing]")
            expect(results.locator("li")).to_have_count(2)
            expect(results).not_to_contain_text("Other person's edition")
            expect(results).not_to_contain_text("Do not show this path")
            expect(results).to_contain_text("First existing shelf")

            card.locator('input[name="title"]').fill("Additional personal copy")
            card.locator('select[name="kind"]').select_option("book")
            card.locator('select[name="location_id"]').select_option(self.selected)
            with page.expect_navigation(wait_until="domcontentloaded"):
                results.get_by_role("button", name="Existing A ansehen", exact=False).tap()
            self.assertIn("/existing/", page.url)
            expect(page.locator("main")).to_contain_text("First existing shelf")
            expect(page.locator("main")).not_to_contain_text("Second existing shelf")
            page.get_by_role("link", name="Zurück zum Entwurf").tap()
            page.wait_for_url(self.origin + INTAKE + "#draft-" + draft_id)
            card = page.locator("#draft-" + draft_id)
            expect(card.locator('input[name="title"]')).to_have_value("Additional personal copy")
            expect(card.locator('select[name="location_id"]')).to_have_value(self.selected)

            card.locator('input[name="barcode"]').fill(SECOND)
            submit = card.get_by_role("button", name="Als neues Exemplar lokal speichern")
            with page.expect_navigation(wait_until="domcontentloaded"):
                submit.tap()
            expect(card.locator("[data-intake-existing]")).to_contain_text("Different edition")
            expect(card.locator("[data-intake-existing]")).not_to_contain_text("Existing A")
            expect(card.locator('select[name="location_id"]')).to_have_value(self.selected)
            self.assertFalse(CaptureInboxStore(self.root).get(draft_id, "owner")["intake"]["confirmed"])

            form = card.locator("[data-intake-editor]")
            replay_payload = form.evaluate("(form) => Object.fromEntries(new FormData(form))")
            confirm_url = submit.get_attribute("formaction")
            with page.expect_navigation(wait_until="domcontentloaded"):
                submit.tap()
            expect(card.get_by_role("link", name="Inventareintrag öffnen")).to_be_visible()
            confirmed = CaptureInboxStore(self.root).get(draft_id, "owner")["intake"]["confirmed"]
            new_copy = confirmed["object_id"]
            self.assertNotEqual(self.first["object_id"], new_copy)
            self.assertEqual(self.selected, self.library.assignment(new_copy)["location_id"])
            replay = context.request.post(self.origin + confirm_url, form=replay_payload, headers={"Accept": "application/json"})
            self.assertEqual(200, replay.status)
            self.assertEqual(new_copy, replay.json()["draft"]["confirmed"]["object_id"])

            next_card = self._scan(page, FIRST, 2)
            next_id = next_card.get_attribute("id").removeprefix("draft-")
            expect(next_card.locator('select[name="location_id"]')).to_have_value("")
            next_card.locator('select[name="location_id"]').select_option(self.next_box)
            with page.expect_navigation(wait_until="domcontentloaded"):
                next_card.get_by_role("button", name="Entwurf aktualisieren").tap()
            expect(next_card.locator('select[name="location_id"]')).to_have_value(self.next_box)
            entries = CaptureInboxStore(self.root).list_intake("owner")
            self.assertEqual([draft_id, next_id], [row["entry_id"] for row in entries])
            self.assertEqual(self.next_box, entries[1]["intake"]["manual"]["location_id"])
            self.assertEqual(self.selected, self.library.assignment(new_copy)["location_id"])
            self.assertEqual(original, self.objects.object(self.first["object_id"]))
            self.assertEqual([], outbound)
