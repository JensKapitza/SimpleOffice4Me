"""Ordinary signed-in entry and exact served native scanner asset."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from bs4 import BeautifulSoup

from app import app
from app.capture_inbox_store import CaptureInboxStore
from tests.http_api_fixture import PublicHttpTestCase


class InventoryIntakeNativeContractTests(PublicHttpTestCase):
    def test_signed_in_native_button_does_not_replace_manual_or_legacy_controls(self):
        path = "/inventory/capture/intake"
        self.assertEqual(302, app.test_client().get(path).status_code)
        switch = self.client.post("/documents/settings/language", data={"language": "de"},
                                  headers=self._csrf_headers(self.client, path))
        self.assertEqual(302, switch.status_code)
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        form = BeautifulSoup(response.data, "html.parser").select_one("#intake-capture")
        button = form.select_one("button[data-intake-native-scan][hidden]")
        self.assertEqual("button", button["type"])
        self.assertEqual("Barcode scannen", button.get_text(strip=True))
        help_id, status_id = button["aria-describedby"].split()
        self.assertIn("ein Foto wählen", form.select_one("#" + help_id).get_text())
        self.assertEqual("status", form.select_one("#" + status_id)["role"])
        self.assertIsNotNone(form.select_one('input#intake-barcode[name="barcode"]'))
        self.assertIsNotNone(form.select_one('input[type="file"][name="photo"]'))
        self.assertIsNotNone(form.select_one('button[type="submit"]'))
        self.assertIsNone(form.select_one("#start-barcode, #shopping-barcode-scan, #barcode"))
        self.assertIn(b"/static/js/inventory_intake_native_scan.js", response.data)
        asset = self.client.get("/static/js/inventory_intake_native_scan.js")
        self.addCleanup(asset.close)
        self.assertEqual(200, asset.status_code)
        self.assertEqual(Path("static/js/inventory_intake_native_scan.js").read_bytes(), asset.data)
        legacy = self.client.get("/inventory")
        self.assertEqual(200, legacy.status_code)
        self.assertIn(b'id="start-barcode"', legacy.data)
        self.assertNotIn(b"inventory_intake_native_scan.js", legacy.data)

        switch = self.client.post("/documents/settings/language", data={"language": "en"},
                                  headers=self._csrf_headers(self.client, path))
        self.assertEqual(302, switch.status_code)
        english = self.client.get(path)
        soup = BeautifulSoup(english.data, "html.parser")
        self.assertEqual("Scan barcode", soup.select_one("[data-intake-native-scan]").get_text(strip=True))
        self.assertIn("choose a photo", soup.select_one("[data-intake-native-scan-help]").get_text())

    @unittest.skipUnless(shutil.which("node"), "Node is required for the in-process barcode port fixture")
    def test_fake_native_result_stays_local_until_the_signed_in_draft_form_is_submitted(self):
        path = "/inventory/capture/intake"
        home = self.client.get(path)
        self.assertEqual(200, home.status_code)
        soup = BeautifulSoup(home.data, "html.parser")
        form = soup.select_one("#intake-capture")
        asset_url = soup.select_one('script[src$="/inventory_intake_native_scan.js"]')["src"]
        with self.client.get(asset_url) as asset:
            script = asset.data
        result = subprocess.run(
            [shutil.which("node"), "tests/fixtures/inventory_intake_native_dom.cjs"],
            input=script, capture_output=True, timeout=10, check=True,
        )
        staged = json.loads(result.stdout)
        self.assertEqual({"staged": "036000291452", "effects": 0}, staged)
        store = CaptureInboxStore(app.config["DOCUMENT_ROOT"])
        self.assertEqual([], store.list_intake("owner"))
        reply = self.client.post(form["action"], data={
            "_csrf_token": form.select_one('input[name="_csrf_token"]')["value"],
            "capture_token": form.select_one('input[name="capture_token"]')["value"],
            "barcode": staged["staged"],
        }, headers={"Accept": "application/json"})
        self.assertEqual(201, reply.status_code)
        self.assertTrue(reply.json["ok"])
        drafts = store.list_intake("owner")
        self.assertEqual(1, len(drafts))
        self.assertEqual(staged["staged"], drafts[0]["intake"]["manual"]["barcode"])
