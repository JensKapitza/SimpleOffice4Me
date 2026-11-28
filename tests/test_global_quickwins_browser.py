"""Exercise global form enhancements in a real browser."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import unittest
from threading import Thread


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the browser test extra")
class GlobalQuickWinsBrowserTests(unittest.TestCase):
    def test_length_counter_is_outside_implicit_label_and_updates(self):
        from playwright.sync_api import Error, sync_playwright

        script = (ROOT / "static/js/global_quickwins.js").read_bytes()
        page_html = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Form hints</title></head>
<body>
  <label>Message <input id="message" maxlength="10"></label>
  <label>Optional <input id="optional" maxlength="8"></label>
  <script src="/global_quickwins.js"></script>
</body></html>"""

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                body = script if self.path == "/global_quickwins.js" else page_html
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/javascript; charset=utf-8" if self.path == "/global_quickwins.js" else "text/html; charset=utf-8",
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch()
            except Error as exc:
                if "Executable doesn't exist" in str(exc):
                    self.skipTest("install Playwright Chromium")
                raise
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                page.wait_for_selector(".so-length-counter")
                self.assertEqual(
                    0,
                    page.locator("label .so-length-counter").count(),
                    "block-level counters must not become children of implicit labels",
                )
                counter = page.locator("label:has(#message) + .so-length-counter")
                self.assertEqual("0/10", counter.inner_text())
                page.locator("#message").fill("hello")
                self.assertEqual("5/10", counter.inner_text())
                self.assertIn(
                    counter.get_attribute("id"),
                    page.locator("#message").get_attribute("aria-describedby"),
                )
            finally:
                browser.close()
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
