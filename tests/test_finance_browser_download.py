"""Offline validation tests for the browser-assisted bank export adapter."""
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

from app.finance_browser_download import (
    MAX_DOWNLOAD_BYTES,
    _BANK_BROWSER_SESSION_LOCK,
    _allowed_santander_url,
    _checked_download,
    _guard_santander_navigation,
    capture_santander_statement,
)
from app.finance_browser_proxy import (
    SantanderConnectProxy,
    _connect_public_santander_host,
    _public_addresses,
)


class _FakePlaywrightTimeoutError(Exception):
    pass


class _FakeDownload:
    def __init__(self, directory, url, filename, content):
        self.url = url
        self.suggested_filename = filename
        self.path_value = Path(directory) / filename
        self.path_value.write_bytes(content)
        self.cancelled = False

    def path(self):
        return str(self.path_value)

    def cancel(self):
        self.cancelled = True


class _FakeRequest:
    def __init__(self, url, *, navigation=True):
        self.url = url
        self.navigation = navigation

    def is_navigation_request(self):
        return self.navigation


class _FakeRoute:
    def __init__(self, url, *, navigation=True):
        self.request = _FakeRequest(url, navigation=navigation)
        self.aborted = False
        self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


class _FakePage:
    def __init__(self, context):
        self.context = context
        self.listeners = {}

    def on(self, event, callback):
        self.listeners[event] = callback

    def goto(self, _url, **_kwargs):
        self.context.harness.start_url = _url
        self.context.harness.navigation_timeout_ms = _kwargs.get("timeout")
        if self.context.harness.mode == "navigation_timeout":
            raise _FakePlaywrightTimeoutError("navigation timeout")
        if self.context.harness.mode == "popup":
            self.context.emit_popup_download()
        elif self.context.harness.mode == "direct":
            self.context.emit_page_download(self)

    def is_closed(self):
        return False

    def wait_for_timeout(self, _milliseconds):
        return None


class _FakeContext:
    def __init__(self, harness, downloads_path):
        self.harness = harness
        self.downloads_path = downloads_path
        self.pages = []
        self.listeners = {}
        self.options = None

    def new_page(self):
        page = _FakePage(self)
        self.pages.append(page)
        return page

    def on(self, event, callback):
        self.listeners[event] = callback

    def route(self, pattern, callback):
        self.harness.route_pattern = pattern
        self.harness.route_handler = callback

    def emit_page_download(self, page):
        download = self.harness.make_download(self.downloads_path)
        page.listeners["download"](download)

    def emit_popup_download(self):
        popup = _FakePage(self)
        self.pages.append(popup)
        self.listeners["page"](popup)
        self.emit_page_download(popup)


class _FakeBrowser:
    def __init__(self, harness, downloads_path):
        self.harness = harness
        self.downloads_path = downloads_path
        self.context = None
        self.closed = False

    def new_context(self, **kwargs):
        self.harness.context_options = kwargs
        self.context = _FakeContext(self.harness, self.downloads_path)
        return self.context

    def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self, mode="popup", *, url="https://www.santander.de/export"):
        self.mode = mode
        self.url = url
        self.chromium = self
        self.browser = None
        self.launch_options = None
        self.context_options = None
        self.download = None
        self.download_directory = ""
        self.start_url = ""
        self.navigation_timeout_ms = None
        self.route_pattern = None
        self.route_handler = None

    def sync_playwright(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def launch(self, **kwargs):
        self.launch_options = kwargs
        if self.mode == "launch_timeout":
            raise _FakePlaywrightTimeoutError("launch timeout")
        self.download_directory = kwargs["downloads_path"]
        self.browser = _FakeBrowser(self, self.download_directory)
        return self.browser

    def make_download(self, downloads_path):
        self.download = _FakeDownload(
            downloads_path,
            self.url,
            "konto.csv",
            b"Buchungstag;Betrag\n01.01.2026;1,00\n",
        )
        return self.download

    def module_mocks(self):
        package = ModuleType("playwright")
        package.__path__ = []
        api = ModuleType("playwright.sync_api")
        api.sync_playwright = self.sync_playwright
        api.TimeoutError = _FakePlaywrightTimeoutError
        return {"playwright": package, "playwright.sync_api": api}


class BrowserBankDownloadTests(unittest.TestCase):
    def test_santander_host_is_exact_or_subdomain(self):
        self.assertTrue(_allowed_santander_url("https://www.santander.de/"))
        self.assertTrue(_allowed_santander_url("https://online.santander.de/export"))
        self.assertFalse(_allowed_santander_url("http://www.santander.de/"))
        self.assertFalse(_allowed_santander_url("https://santander.de.attacker.test/"))
        self.assertFalse(_allowed_santander_url("https://not-santander.de/"))
        self.assertFalse(_allowed_santander_url("https://santander.de:444/"))
        self.assertFalse(_allowed_santander_url("https://user@santander.de/"))
        self.assertFalse(_allowed_santander_url("https://santander.de:invalid/"))

    def test_navigation_guard_blocks_external_navigation_and_subresources(self):
        external_navigation = _FakeRoute("https://login.attacker.test/", navigation=True)
        _guard_santander_navigation(external_navigation)
        self.assertTrue(external_navigation.aborted)
        self.assertFalse(external_navigation.continued)

        bank_navigation = _FakeRoute("https://online.santander.de/", navigation=True)
        _guard_santander_navigation(bank_navigation)
        self.assertFalse(bank_navigation.aborted)
        self.assertTrue(bank_navigation.continued)

        external_resource = _FakeRoute("https://cdn.example.test/app.js", navigation=False)
        _guard_santander_navigation(external_resource)
        self.assertTrue(external_resource.aborted)
        self.assertFalse(external_resource.continued)

        bank_resource = _FakeRoute("https://login.santander.de/mfa.js", navigation=False)
        _guard_santander_navigation(bank_resource)
        self.assertFalse(bank_resource.aborted)
        self.assertTrue(bank_resource.continued)

    def test_navigation_guard_blocks_insecure_subresources(self):
        for url in (
            "http://cdn.example.test/script.js",
            "file:///etc/passwd",
            "data:text/html,example",
            "ftp://cdn.example.test/script.js",
        ):
            with self.subTest(url=url):
                route = _FakeRoute(url, navigation=False)
                _guard_santander_navigation(route)
                self.assertTrue(route.aborted)
                self.assertFalse(route.continued)

    def test_navigation_guard_blocks_local_https_resources(self):
        for url in (
            "https://localhost/private",
            "https://localhost./private",
            "https://service.localhost/private",
            "https://127.0.0.1/private",
            "https://192.168.1.1/private",
            "https://[::1]/private",
            "https://169.254.169.254/latest/meta-data/",
            "https://user:password@cdn.example.test/script.js",
        ):
            with self.subTest(url=url):
                route = _FakeRoute(url, navigation=False)
                _guard_santander_navigation(route)
                self.assertTrue(route.aborted)
                self.assertFalse(route.continued)

    def test_navigation_guard_keeps_https_mfa_resources_available(self):
        resource = _FakeRoute("https://login.santander.de/challenge.js", navigation=False)
        _guard_santander_navigation(resource)
        self.assertTrue(resource.continued)
        self.assertFalse(resource.aborted)

    def test_dns_resolution_fails_closed_for_mixed_public_and_private_answers(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.20.30.40", 443)),
        ]
        with patch("app.finance_browser_proxy.socket.getaddrinfo", return_value=answers):
            with self.assertRaisesRegex(ValueError, "non-public"):
                _public_addresses("login.santander.de", 443)

    def test_dns_resolution_obeys_bounded_lookup_timeout(self):
        def slow_resolution(*_args, **_kwargs):
            time.sleep(0.05)
            return []

        with patch("app.finance_browser_proxy._DNS_LOOKUP_TIMEOUT_SECONDS", 0.005), \
             patch("app.finance_browser_proxy.socket.getaddrinfo", side_effect=slow_resolution):
            with self.assertRaisesRegex(TimeoutError, "bounded timeout"):
                _public_addresses("login.santander.de", 443)
        time.sleep(0.06)

    def test_connect_uses_the_numeric_address_from_the_validated_lookup(self):
        upstream = Mock()
        address = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, ("93.184.216.34", 443))
        with patch("app.finance_browser_proxy._public_addresses", return_value=[address]), \
             patch("app.finance_browser_proxy.socket.socket", return_value=upstream):
            self.assertIs(_connect_public_santander_host("login.santander.de", 443), upstream)

        upstream.connect.assert_called_once_with(("93.184.216.34", 443))
        upstream.setblocking.assert_called_once_with(False)

    def test_connect_proxy_rejects_split_horizon_answers_before_connecting(self):
        private_answer = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.10", 443)),
        ]
        with SantanderConnectProxy(deadline=time.monotonic() + 5) as proxy:
            host, port = proxy.browser_settings["server"].removeprefix("http://").split(":")
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(2)
            try:
                client.connect((host, int(port)))
                with patch(
                    "app.finance_browser_proxy.socket.getaddrinfo",
                    return_value=private_answer,
                ):
                    client.sendall(
                        b"CONNECT login.santander.de:443 HTTP/1.1\r\n"
                        b"Host: login.santander.de:443\r\n\r\n"
                    )
                    response = client.recv(128)
            finally:
                client.close()

        self.assertTrue(response.startswith(b"HTTP/1.1 502 Bad Gateway"))

    def test_connect_proxy_relays_large_bidirectional_payloads_with_backpressure(self):
        payload = b"request" * 75_000
        response_payload = b"response" * 65_000
        upstream_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        upstream_listener.bind(("127.0.0.1", 0))
        upstream_listener.listen(1)
        upstream_port = upstream_listener.getsockname()[1]
        received_request = bytearray()

        def serve_upstream():
            connection, _address = upstream_listener.accept()
            with connection:
                while len(received_request) < len(payload):
                    chunk = connection.recv(64 * 1024)
                    if not chunk:
                        break
                    received_request.extend(chunk)
                connection.sendall(response_payload)
                connection.shutdown(socket.SHUT_WR)

        upstream_thread = threading.Thread(target=serve_upstream, daemon=True)
        upstream_thread.start()
        try:
            with SantanderConnectProxy(deadline=time.monotonic() + 10) as proxy, patch(
                "app.finance_browser_proxy._connect_public_santander_host",
                side_effect=lambda _host, _port: socket.create_connection(
                    ("127.0.0.1", upstream_port), timeout=2,
                ),
            ):
                proxy_host, proxy_port = (
                    proxy.browser_settings["server"]
                    .removeprefix("http://")
                    .split(":")
                )
                client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client.settimeout(5)
                try:
                    client.connect((proxy_host, int(proxy_port)))
                    client.sendall(
                        b"CONNECT login.santander.de:443 HTTP/1.1\r\n"
                        b"Host: login.santander.de:443\r\n\r\n"
                    )
                    handshake = bytearray()
                    while b"\r\n\r\n" not in handshake:
                        handshake.extend(client.recv(256))
                    self.assertTrue(handshake.startswith(b"HTTP/1.1 200"))
                    client.sendall(payload)
                    client.shutdown(socket.SHUT_WR)
                    received_response = bytearray()
                    while True:
                        chunk = client.recv(64 * 1024)
                        if not chunk:
                            break
                        received_response.extend(chunk)
                finally:
                    client.close()
            upstream_thread.join(timeout=2)
        finally:
            upstream_listener.close()

        self.assertFalse(upstream_thread.is_alive())
        self.assertEqual(payload, bytes(received_request))
        self.assertEqual(response_payload, bytes(received_response))

    def test_download_checks_format_and_size(self):
        result = _checked_download("umsatz.csv", b"Buchungstag;Betrag\n01.01.2026;1,00\n")
        self.assertEqual(result.filename, "umsatz.csv")
        with self.assertRaises(ValueError):
            _checked_download("download.html", b"<html>login</html>")
        with self.assertRaises(ValueError):
            _checked_download("umsatz.csv", b"")
        with self.assertRaises(ValueError):
            _checked_download("umsatz.csv", b"x" * (MAX_DOWNLOAD_BYTES + 1))

    def test_rejects_html_disguised_as_csv(self):
        with self.assertRaises(ValueError):
            _checked_download("konto.csv", b"<html>Session expired</html>")

    def test_filename_drops_directory_components(self):
        result = _checked_download("../../konto.csv", b"Buchungstag;Betrag\n01.01.2026;1,00\n")
        self.assertEqual(result.filename, "konto.csv")

    def test_capture_rejects_invalid_timeout_values_before_loading_playwright(self):
        for invalid in (True, False, 29, 1801, 30.0):
            with self.subTest(timeout=invalid):
                with self.assertRaises(ValueError):
                    capture_santander_statement(timeout_seconds=invalid)

    def test_only_one_local_bank_browser_session_can_run_at_a_time(self):
        with _BANK_BROWSER_SESSION_LOCK:
            with self.assertRaisesRegex(RuntimeError, "already geöffnet"):
                capture_santander_statement(timeout_seconds=30)

    def test_bank_browser_session_lock_is_released_after_capture_failure(self):
        from app.finance_browser_download import CapturedStatement

        with patch(
            "app.finance_browser_download._capture_santander_statement",
            side_effect=RuntimeError("test browser failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "test browser failure"):
                capture_santander_statement(timeout_seconds=30)

        result = CapturedStatement("statement.csv", b"valid test content")
        with patch(
            "app.finance_browser_download._capture_santander_statement",
            return_value=result,
        ):
            self.assertIs(capture_santander_statement(timeout_seconds=30), result)

    def test_capture_accepts_manual_santander_popup_export_in_ephemeral_context(self):
        harness = _FakePlaywright()
        with patch.dict(sys.modules, harness.module_mocks()):
            result = capture_santander_statement(timeout_seconds=30)

        self.assertEqual("konto.csv", result.filename)
        self.assertTrue(result.content.startswith(b"Buchungstag;Betrag"))
        self.assertEqual("https://www.santander.de/", harness.start_url)
        self.assertGreaterEqual(harness.navigation_timeout_ms, 29_000)
        self.assertLessEqual(harness.navigation_timeout_ms, 30_000)
        launch_options = dict(harness.launch_options)
        self.assertGreaterEqual(launch_options.pop("timeout"), 29_000)
        proxy_settings = launch_options.pop("proxy")
        self.assertTrue(proxy_settings["server"].startswith("http://127.0.0.1:"))
        self.assertEqual("<-loopback>", proxy_settings["bypass"])
        browser_args = launch_options.pop("args")
        self.assertIn("--dns-prefetch-disable", browser_args)
        self.assertIn("--disable-quic", browser_args)
        self.assertIn(
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            browser_args,
        )
        self.assertEqual(
            {"headless": False, "downloads_path": harness.download_directory},
            launch_options,
        )
        self.assertEqual(
            {"accept_downloads": True, "service_workers": "block"},
            harness.context_options,
        )
        self.assertEqual("**/*", harness.route_pattern)
        blocked_redirect = _FakeRoute("https://login.attacker.test/", navigation=True)
        harness.route_handler(blocked_redirect)
        self.assertTrue(blocked_redirect.aborted)
        self.assertTrue(harness.browser.closed)
        self.assertFalse(Path(harness.download_directory).exists())

    def test_capture_rejects_non_santander_download_and_closes_browser(self):
        harness = _FakePlaywright(url="https://santander.de.attacker.test/export")
        with patch.dict(sys.modules, harness.module_mocks()):
            with self.assertRaisesRegex(ValueError, "nicht von einer Santander-Domain"):
                capture_santander_statement(timeout_seconds=30)

        self.assertTrue(harness.download.cancelled)
        self.assertTrue(harness.browser.closed)

    def test_capture_timeout_closes_visible_browser(self):
        harness = _FakePlaywright(mode="wait")
        with patch.dict(sys.modules, harness.module_mocks()):
            with patch("app.finance_browser_download.time.monotonic", side_effect=(0, 0, 0, 31)):
                with self.assertRaisesRegex(TimeoutError, "Zeitlimit"):
                    capture_santander_statement(timeout_seconds=30)

        self.assertTrue(harness.browser.closed)

    def test_navigation_uses_configured_deadline_and_converts_playwright_timeout(self):
        harness = _FakePlaywright(mode="navigation_timeout")
        with patch.dict(sys.modules, harness.module_mocks()):
            with self.assertRaisesRegex(TimeoutError, "Santander-Seite.*Zeitlimit"):
                capture_santander_statement(timeout_seconds=90)

        self.assertGreaterEqual(harness.navigation_timeout_ms, 89_000)
        self.assertLessEqual(harness.navigation_timeout_ms, 90_000)
        self.assertTrue(harness.browser.closed)

    def test_browser_launch_timeout_is_converted_to_export_timeout(self):
        harness = _FakePlaywright(mode="launch_timeout")
        with patch.dict(sys.modules, harness.module_mocks()):
            with patch("app.finance_browser_download.time.monotonic", side_effect=(0, 0)):
                with self.assertRaisesRegex(TimeoutError, "Browser"):
                    capture_santander_statement(timeout_seconds=30)

        self.assertEqual(30000, harness.launch_options["timeout"])
        self.assertIsNone(harness.browser)


if __name__ == "__main__":
    unittest.main()
