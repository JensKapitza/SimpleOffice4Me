from pathlib import Path
import importlib.util
import os
import threading
import unittest

from werkzeug.serving import make_server

from app import app
from tests.http_api_fixture import PublicHttpTestCase

ROOT = Path(__file__).resolve().parents[1]


class GlobalQuickWinsTests(unittest.TestCase):
    def test_global_layer_is_loaded_and_has_expected_helpers(self):
        layout = (ROOT / "templates/layout.html").read_text(encoding="utf-8")
        script = (ROOT / "static/js/global_quickwins.js").read_text(encoding="utf-8")
        self.assertIn("global_quickwins.js", layout)
        for marker in ("given-name", "family-name", "postal-code", "so-length-counter", "so-required-marker", "aria-current", "scope"):
            self.assertIn(marker, script)
        self.assertIn("MutationObserver", script)
        self.assertIn('attributeFilter: ["disabled"]', script)
        self.assertIn("syncDisabledState", script)
        self.assertNotIn('"street-address"', script)


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional web-export extra")
class GlobalQuickWinsBrowserTests(PublicHttpTestCase):
    def test_length_hints_keep_wrapped_serial_labels_valid_and_described(self):
        from playwright.sync_api import Error, sync_playwright

        cookie_name = app.config["SESSION_COOKIE_NAME"]
        cookie = self.client.get_cookie(cookie_name)
        self.assertIsNotNone(cookie)
        with make_server("127.0.0.1", 0, app, threaded=True) as server, sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(executable_path=os.environ.get("SIMPLEOFFICE_TEST_CHROMIUM") or None)
            except Error as exc:
                if "Executable doesn't exist" in str(exc):
                    self.skipTest("install Playwright Chromium")
                raise
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                context = browser.new_context(service_workers="block", viewport={"width": 360, "height": 900})
                context.add_cookies([{"name": cookie_name, "value": cookie.value, "url": origin}])
                page = context.new_page()
                page.goto(origin + "/datalogger/serial", wait_until="load", timeout=15_000)
                for selector, name, maximum in (
                    ("[data-input]", "Nachricht", 8192),
                    ('[name="gateway_ip"]', "Gateway-IP", 15),
                    ('[name="unit"]', "Einheit", 40),
                ):
                    control = page.locator(selector)
                    page.wait_for_function(
                        "selector => { const el = document.querySelector(selector); "
                        "const anchor = el?.closest('label') || el; "
                        "return anchor?.nextElementSibling?.classList.contains('so-length-counter'); }",
                        arg=selector,
                        timeout=5_000,
                    )
                    hint = control.evaluate_handle("(el) => (el.closest('label') || el).nextElementSibling")
                    hint.wait_for_element_state("visible", timeout=5_000)
                    self.assertEqual("DIV", hint.evaluate("(el) => el.tagName"))
                    self.assertIn("so-length-counter", hint.get_attribute("class").split())
                    self.assertEqual("polite", hint.get_attribute("aria-live"))
                    self.assertIn(hint.get_attribute("id"), control.get_attribute("aria-describedby").split())
                    accessible_label = control.evaluate("(el) => el.labels[0].textContent")
                    self.assertIn(name, accessible_label)
                    self.assertNotIn(hint.inner_text(), accessible_label)
                    number_box, input_box = hint.bounding_box(), control.bounding_box()
                    self.assertGreaterEqual(number_box["y"], input_box["y"] + input_box["height"] - 1)
                    self.assertAlmostEqual(input_box["x"], number_box["x"], delta=2)
                    self.assertAlmostEqual(input_box["width"], number_box["width"], delta=2)
                    control.fill("42")
                    self.assertEqual(f"2/{maximum}", hint.inner_text())
                self.assertEqual(0, page.locator("main[data-serial-terminal] label .so-length-counter").count())
            finally:
                browser.close()
                server.shutdown()
                thread.join(timeout=5)



if __name__ == "__main__":
    unittest.main()
