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

from .settings_store import (
    DEFAULT_BANK_BROWSER_TIMEOUT_SECONDS,
    MAX_FINANCE_IMPORT_LIMIT_BYTES,
    MAX_BANK_BROWSER_TIMEOUT_SECONDS,
    MIN_BANK_BROWSER_TIMEOUT_SECONDS,
)

MAX_DOWNLOAD_BYTES = MAX_FINANCE_IMPORT_LIMIT_BYTES
SUPPORTED_EXTENSIONS = {".csv", ".xml", ".sta", ".mt940", ".txt"}
SANTANDER_START_URL = "https://www.santander.de/"


@dataclass(frozen=True)
class CapturedStatement:
    filename: str
    content: bytes


def _allowed_santander_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and (host == "santander.de" or host.endswith(".santander.de"))
    )


def _guard_santander_navigation(route) -> None:
    """Block top-level and frame navigations away from the verified bank domain."""
    request = route.request
    if request.is_navigation_request() and not _allowed_santander_url(request.url):
        route.abort()
        return
    route.continue_()


def _checked_download(filename: str, data: bytes) -> CapturedStatement:
    name = Path(filename.replace("\\", "/")).name
    if not name or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("Nicht unterstütztes Kontoauszugsformat")
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("Kontoauszug ist leer oder größer als 25 MiB")
    # A .csv extension is not sufficient: login pages and error documents must
    # never be treated as bank statements.
    from .finance_statements import parse_statement_bytes
    parse_statement_bytes(data, name)
    return CapturedStatement(name, data)


def _remaining_timeout_ms(deadline: float) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Zeitlimit für den Santander-Export überschritten")
    return max(1, int(remaining * 1000))


def capture_santander_statement(
    *, timeout_seconds: int = DEFAULT_BANK_BROWSER_TIMEOUT_SECONDS,
) -> CapturedStatement:
    """Wait for a user-initiated statement export in a visible Santander browser.

    Requires the optional Playwright package and installed Chromium browser.
    Does not automate authentication, bypass challenges, or submit transactions.
    The returned bytes must be passed to FinanceStatementImporter.preview and
    only committed after explicit user confirmation.
    """
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not MIN_BANK_BROWSER_TIMEOUT_SECONDS <= timeout_seconds <= MAX_BANK_BROWSER_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "Zeitlimit muss zwischen "
            f"{MIN_BANK_BROWSER_TIMEOUT_SECONDS} und {MAX_BANK_BROWSER_TIMEOUT_SECONDS} Sekunden liegen"
        )
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Browser-Abruf benötigt das optionale Paket playwright") from exc

    deadline = time.monotonic() + timeout_seconds
    with TemporaryDirectory(prefix="simpleoffice-bank-") as directory:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    headless=False,
                    downloads_path=directory,
                    timeout=_remaining_timeout_ms(deadline),
                )
            except PlaywrightTimeoutError as exc:
                raise TimeoutError("Zeitlimit beim Start des Santander-Browsers überschritten") from exc
            try:
                context = browser.new_context(accept_downloads=True)
                context.route("**/*", _guard_santander_navigation)
                page = context.new_page()
                downloads = []
                context.on("page", lambda popup: popup.on("download", lambda item: downloads.append(item)))
                page.on("download", lambda item: downloads.append(item))
                try:
                    page.goto(
                        SANTANDER_START_URL,
                        wait_until="domcontentloaded",
                        timeout=_remaining_timeout_ms(deadline),
                    )
                except PlaywrightTimeoutError as exc:
                    raise TimeoutError("Santander-Seite wurde nicht innerhalb des Zeitlimits geladen") from exc
                while True:
                    remaining_seconds = deadline - time.monotonic()
                    if remaining_seconds <= 0:
                        raise TimeoutError("Keine Kontoauszugsdatei innerhalb des Zeitlimits heruntergeladen")
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
                    if not any(not tab.is_closed() for tab in context.pages):
                        raise ValueError("Bankbrowser wurde geschlossen")
                    wait_milliseconds = max(1, min(250, int(remaining_seconds * 1000)))
                    next(tab for tab in context.pages if not tab.is_closed()).wait_for_timeout(wait_milliseconds)
            except PlaywrightTimeoutError as exc:
                raise TimeoutError("Santander-Export wurde nicht innerhalb des Zeitlimits abgeschlossen") from exc
            finally:
                browser.close()
