"""Offline validation tests for the browser-assisted bank export adapter."""
import unittest

from app.finance_browser_download import (
    MAX_DOWNLOAD_BYTES,
    _allowed_santander_url,
    _checked_download,
)


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


if __name__ == "__main__":
    unittest.main()
