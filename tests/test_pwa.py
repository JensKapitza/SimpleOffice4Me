import json
import unittest
from pathlib import Path

from app import app


class PwaTestCase(unittest.TestCase):
    def test_manifest_declares_installable_android_app(self):
        manifest = json.loads((Path(app.static_folder) / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual("standalone", manifest["display"])
        self.assertEqual("/", manifest["start_url"])
        self.assertEqual({"any"}, {icon["sizes"] for icon in manifest["icons"]})
        self.assertEqual({"image/svg+xml"}, {icon["type"] for icon in manifest["icons"]})

    def test_feature_manifests_have_distinct_identity_and_entrypoints(self):
        expected = {
            "manifest-clock.webmanifest": ("/pwa/clock", "/personnel?pwa=clock", "Stempeluhr"),
            "manifest-files.webmanifest": ("/pwa/files", "/documents/?pwa=files", "Dateien"),
            "manifest-slideshow.webmanifest": ("/pwa/slideshow", "/documents/images?pwa=slideshow", "Diashow"),
        }
        identities = set()
        for filename, (identity, start_url, short_name) in expected.items():
            manifest = json.loads((Path(app.static_folder) / filename).read_text(encoding="utf-8"))
            self.assertEqual(identity, manifest["id"])
            self.assertEqual(start_url, manifest["start_url"])
            self.assertEqual(short_name, manifest["short_name"])
            self.assertEqual("standalone", manifest["display"])
            self.assertEqual("/", manifest["scope"])
            identities.add(manifest["id"])
        self.assertEqual(3, len(identities))

    def test_service_worker_is_served_at_origin_root_without_long_cache(self):
        client = app.test_client()
        response = client.get("/service-worker.js")
        self.assertEqual(200, response.status_code)
        self.assertEqual("/", response.headers["Service-Worker-Allowed"])
        self.assertEqual("no-cache", response.headers["Cache-Control"])
        self.assertIn(b"contacts, HR, calendar or documents", response.data)

    def test_layout_registers_manifest_and_worker_bootstrap(self):
        layout = (Path(app.template_folder) / "layout.html").read_text(encoding="utf-8")
        self.assertIn("manifest.webmanifest", layout)
        self.assertIn("manifest-clock.webmanifest", layout)
        self.assertIn("manifest-files.webmanifest", layout)
        self.assertIn("manifest-slideshow.webmanifest", layout)
        self.assertIn("request.args.get('pwa'", layout)
        self.assertIn("js/pwa.js", layout)

    def test_slideshow_pwa_bootstrap_opens_existing_modal(self):
        script = (Path(app.static_folder) / "js" / "pwa.js").read_text(encoding="utf-8")
        self.assertIn('params.get("pwa") !== "slideshow"', script)
        self.assertIn('document.getElementById("slideshow")', script)
        self.assertIn("Modal.getOrCreateInstance", script)
