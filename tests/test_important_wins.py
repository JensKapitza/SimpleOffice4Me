from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ImportantWinsRegressionTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_manifest_tracks_exactly_100_wins(self):
        text = self.read("docs/100-important-wins-2026-09.md")
        numbers = [int(value) for value in re.findall(r"(?m)^(\d+)\. ", text)]
        self.assertEqual(numbers, list(range(1, 101)))

    def test_csrf_is_same_origin_and_covers_xhr(self):
        text = self.read("static/js/security.js")
        self.assertIn("isSameOrigin", text)
        self.assertIn("formaction", text)
        self.assertIn("XMLHttpRequest.prototype.send", text)
        self.assertIn("X-CSRF-Token", text)

    def test_global_ui_has_form_and_mobile_safety(self):
        text = self.read("static/js/global_ui.js")
        self.assertIn('form.dataset.submitting === "true"', text)
        self.assertIn('aria-invalid', text)
        self.assertIn('loading = "lazy"', text)
        self.assertIn('table-responsive', text)

    def test_global_ui_adds_contextual_help_to_form_fields(self):
        text = self.read("static/js/global_ui.js")
        self.assertIn('root.querySelectorAll?.("input, select, textarea").forEach(enhanceFieldHelp)', text)
        self.assertIn('control.dataset.help', text)
        self.assertIn('data-bs-toggle', text)
        self.assertIn('aria-describedby', text)
        self.assertIn('MutationObserver', text)
        css = self.read("static/css/quality-wins.css")
        self.assertIn('.so-field-help', css)
        self.assertIn('cursor: help', css)

    def test_service_worker_keeps_sensitive_app_data_out_of_cache(self):
        text = self.read("static/service-worker.js")
        self.assertIn('CACHE_NAME = `${CACHE_PREFIX}v2`', text)
        self.assertIn('url.pathname.startsWith("/static/")', text)
        self.assertIn('request.headers.has("range")', text)
        self.assertIn('no-store', text)
        self.assertIn('navigationPreload.enable()', text)
        self.assertIn('Promise.all(SHELL_ASSETS.map', text)
        self.assertNotIn('Promise.allSettled(SHELL_ASSETS.map', text)
        self.assertNotIn('await self.skipWaiting()', text)

    def test_pwa_update_path_is_explicit(self):
        text = self.read("static/js/pwa.js")
        self.assertIn('updateViaCache: "none"', text)
        self.assertIn('SKIP_WAITING', text)
        self.assertIn('visibilitychange', text)
        self.assertIn('simpleoffice:pwa-update-available', text)

    def test_layout_loads_quality_layer_and_update_controls(self):
        text = self.read("templates/layout.html")
        self.assertIn("quality-wins.css", text)
        self.assertIn('id="network-offline-banner"', text)
        self.assertIn('id="pwa-update-banner"', text)
        self.assertIn('viewport-fit=cover', text)
        self.assertIn('role="status" aria-live="polite"', text)

    def test_android_wrapper_guards_api_and_preserves_state(self):
        text = self.read("android/apk/app/src/main/java/de/simpleoffice4me/android/MainActivity.java")
        self.assertIn("Build.VERSION.SDK_INT >= Build.VERSION_CODES.O", text)
        self.assertIn("WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)", text)
        self.assertIn("webView.restoreState(state)", text)
        self.assertIn("webView.saveState(outState)", text)
        self.assertIn("connection.setUseCaches(false)", text)
        self.assertIn("RUNTIME_VERSION", text)
        self.assertIn("mainFrameLoadFailed", text)

    def test_android_version_is_bumped(self):
        text = self.read("android/apk/app/build.gradle")
        self.assertRegex(text, r"versionCode\s+3\b")
        self.assertIn("versionName '1.0.2'", text)
        self.assertIn("buildConfig true", text)


if __name__ == "__main__":
    unittest.main()
