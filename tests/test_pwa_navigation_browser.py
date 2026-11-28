"""Verify real Chromium service worker navigation after an interrupted preload."""
import importlib.util
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
from threading import Thread
import unittest


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional web-export extra")
class PwaNavigationBrowserTest(unittest.TestCase):
    def test_preload_failure_retries_only_navigation_get_before_offline_shell(self):
        from playwright.sync_api import Error, sync_playwright

        worker = (Path(__file__).resolve().parents[1] / "static" / "service-worker.js").read_bytes()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                preload = self.headers.get("Service-Worker-Navigation-Preload") is not None
                if self.path in ("/receipt", "/unreachable", "/unavailable", "/fast"):
                    requests.append((self.path, preload))
                if self.path == "/unreachable" or (self.path == "/receipt" and preload):
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                body = {
                    "/service-worker.js": worker,
                    "/static/offline.html": b"offline shell",
                    "/receipt": b"saved receipt",
                    "/unavailable": b"service unavailable",
                    "/fast": b"preloaded content",
                }.get(self.path, b"local fixture")
                self.send_response(503 if self.path == "/unavailable" else 200)
                self.send_header("Content-Type", "text/javascript" if self.path == "/service-worker.js" else "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(executable_path=os.environ.get("SIMPLEOFFICE_PWA_CHROMIUM") or None)
            except Error as exc:
                if "Executable doesn't exist" in str(exc):
                    self.skipTest("install Playwright Chromium")
                raise
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                page = browser.new_page()
                page.set_default_timeout(15000)
                page.goto(base)
                page.evaluate("() => navigator.serviceWorker.register('/service-worker.js')")
                page.wait_for_function("navigator.serviceWorker.controller !== null")
                for url, status, body in (
                    ("/receipt", 200, "saved receipt"),
                    ("/fast", 200, "preloaded content"),
                    ("/unavailable", 503, "service unavailable"),
                    ("/unreachable", 200, "offline shell"),
                ):
                    with self.subTest(url=url):
                        response = page.goto(base + url, wait_until="domcontentloaded")
                        self.assertEqual(status, response.status)
                        self.assertEqual(body, page.locator("body").inner_text())
                self.assertIn(("/receipt", True), requests)
                self.assertEqual(1, requests.count(("/receipt", False)))
                self.assertEqual(0, requests.count(("/fast", False)))
                self.assertEqual(0, requests.count(("/unavailable", False)))
                self.assertIn(("/unreachable", False), requests)
            finally:
                browser.close()
                server.shutdown()
                thread.join(timeout=5)
