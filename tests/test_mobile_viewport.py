from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MobileViewportRegressionTests(unittest.TestCase):
    def test_layout_loads_mobile_viewport_assets(self):
        layout = (ROOT / "templates" / "layout.html").read_text(encoding="utf-8")
        self.assertIn("css/mobile-viewport.css", layout)
        self.assertIn("js/mobile_viewport.js", layout)
        self.assertLess(layout.index("js/global_ui.js"), layout.index("js/mobile_viewport.js"))

    def test_visual_viewport_tracks_android_keyboard_without_user_agent_sniffing(self):
        script = (ROOT / "static" / "js" / "mobile_viewport.js").read_text(encoding="utf-8")
        self.assertIn("window.visualViewport", script)
        self.assertIn("--so-keyboard-inset", script)
        self.assertIn("--so-visual-viewport-height", script)
        self.assertIn("rawInset >= 80", script)
        self.assertIn("scrollIntoView", script)
        self.assertIn('document.addEventListener("focusin"', script)
        self.assertNotIn("userAgent", script)

    def test_mobile_css_keeps_sticky_actions_above_keyboard(self):
        css = (ROOT / "static" / "css" / "mobile-viewport.css").read_text(encoding="utf-8")
        self.assertIn('html[data-so-keyboard-open="1"] .sticky-bottom', css)
        self.assertIn("bottom: var(--so-keyboard-inset)", css)
        self.assertIn("env(safe-area-inset-bottom)", css)
        self.assertIn("scroll-margin-bottom", css)
        self.assertIn(".modal-dialog-scrollable", css)

    def test_android_build_metadata_is_embedded_and_only_read_in_native_wrapper(self):
        script = (ROOT / "static" / "js" / "mobile_viewport.js").read_text(encoding="utf-8")
        gradle = (ROOT / "android" / "apk" / "app" / "build.gradle").read_text(encoding="utf-8")
        self.assertIn("if (!window.SimpleOfficeAndroid) return", script)
        self.assertIn('fetch("/static/android-build.json"', script)
        self.assertIn('cache: "no-store"', script)
        self.assertIn('badge.id = "android-build-info"', script)
        self.assertIn("APK ${version} · ${architecture}", script)
        self.assertIn("static/android-build.json", gradle)
        self.assertIn("architecture: architecture", gradle)
        self.assertIn("python: pythonRuntimeVersion", gradle)
        self.assertIn("min_sdk: 24", gradle)
        self.assertIn("target_sdk: 36", gradle)

    def test_native_android_shell_gets_a_global_history_back_button(self):
        script = (ROOT / "static" / "js" / "mobile_viewport.js").read_text(encoding="utf-8")
        self.assertIn('document.getElementById("android-history-back")', script)
        self.assertIn('button.id = "android-history-back"', script)
        self.assertIn('window.history.back()', script)
        self.assertIn('window.location.assign("/")', script)
        self.assertIn('aria-label", "Zurück"', script)


if __name__ == "__main__":
    unittest.main()