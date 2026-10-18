"""User-assisted bank statement downloads through an ephemeral visible browser.

The user enters credentials and approves MFA on the bank's own site. No login
secrets, cookies, storage state, or browser profile are persisted by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse
import time

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".csv", ".xml", ".sta", ".mt940", ".txt"}
SANTANDER_START_URL = "https://www.santander.de/"


@dataclass(frozen=True)
class CapturedStatement:
    filename: str
    content: bytes


def _allowed_santander_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host == "santander.de" or host.endswith(".santander.de"))


def _checked_download(filename: str, data: bytes) -> CapturedStatement:
    name = Path(filename.replace("\\", "/")).name
    if not name or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("Nicht unterstütztes Kontoauszugsformat")
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("Kontoauszug ist leer oder größer als 25 MiB")
    return CapturedStatement(name, data)


def capture_santander_statement(*, timeout_seconds: int = 300) -> CapturedStatement:
    """Wait for a user-initiated statement export in a visible Santander browser.

    Requires the optional Playwright package and installed Chromium browser.
    Does not automate authentication, bypass challenges, or submit transactions.
    The returned bytes must be passed to FinanceStatementImporter.preview and
    only committed after explicit user confirmation.
    """
    if not 30 <= timeout_seconds <= 1800:
        raise ValueError("Zeitlimit muss zwischen 30 und 1800 Sekunden liegen")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Browser-Abruf benötigt das optionale Paket playwright") from exc

    with TemporaryDirectory(prefix="simpleoffice-bank-") as directory:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False, downloads_path=directory)
            try:
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                downloads = []
                page.on("download", lambda item: downloads.append(item))
                page.goto(SANTANDER_START_URL, wait_until="domcontentloaded", timeout=30000)
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline:
                    if downloads:
                        download = downloads.pop(0)
                        if not _allowed_santander_url(download.url):
                            download.cancel()
                            raise ValueError("Download stammt nicht von einer Santander-Domain")
                        filename = download.suggested_filename
                        if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
                            download.cancel()
                            raise ValueError("Nicht unterstütztes Kontoauszugsformat")
                        path = download.path()
                        if path is None:
                            raise ValueError("Bankdownload wurde abgebrochen")
                        if Path(path).stat().st_size > MAX_DOWNLOAD_BYTES:
                            raise ValueError("Kontoauszug ist größer als 25 MiB")
                        return _checked_download(filename, Path(path).read_bytes())
                    if not context.pages:
                        raise ValueError("Bankbrowser wurde geschlossen")
                    page.wait_for_timeout(250)
                raise TimeoutError("Keine Kontoauszugsdatei innerhalb des Zeitlimits heruntergeladen")
            finally:
                browser.close()
