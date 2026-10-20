"""Offline validation tests for the browser-assisted bank export adapter."""
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from app.finance_browser_download import (
    MAX_DOWNLOAD_BYTES,
    _allowed_santander_url,
    _checked_download,
    capture_santander_statement,
)


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


class _FakePage:
    def __init__(self, context):
        self.context = context
        self.listeners = {}

    def on(self, event, callback):
        self.listeners[event] = callback

    def goto(self, _url, **_kwargs):
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

    def sync_playwright(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def launch(self, **kwargs):
        self.launch_options = kwargs
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
        return {"playwright": package, "playwright.sync_api": api}


class BrowserBankDownloadTests(unittest.TestCase):
    def test_santander_host_is_exact_or_subdomain(self):
        self.assertTrue(_allowed_santander_url("https://www.santander.de/"))
        self.assertTrue(_allowed_santander_url("https://online.santander.de/export"))
        self.assertFalse(_allowed_santander_url("http://www.santander.de/"))
        self.assertFalse(_allowed_santander_url("https://santander.de.attacker.test/"))
        self.assertFalse(_allowed_santander_url("https://not-santander.de/"))

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

    def test_capture_accepts_manual_santander_popup_export_in_ephemeral_context(self):
        harness = _FakePlaywright()
        with patch.dict(sys.modules, harness.module_mocks()):
            result = capture_santander_statement(timeout_seconds=30)

        self.assertEqual("konto.csv", result.filename)
        self.assertTrue(result.content.startswith(b"Buchungstag;Betrag"))
        self.assertEqual({"headless": False, "downloads_path": harness.download_directory}, harness.launch_options)
        self.assertEqual({"accept_downloads": True}, harness.context_options)
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
            with patch("app.finance_browser_download.time.monotonic", side_effect=(0, 0, 31)):
                with self.assertRaisesRegex(TimeoutError, "Zeitlimit"):
                    capture_santander_statement(timeout_seconds=30)

        self.assertTrue(harness.browser.closed)


if __name__ == "__main__":
    unittest.main()
