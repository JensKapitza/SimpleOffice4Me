#!/usr/bin/env python3
"""Manual Playwright smoke test for SimpleOffice4Me.

The test creates the first local admin through the real registration form,
logs in, opens core navigation pages (or every navigation link), stores
full-page screenshots and writes a machine-readable summary.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urldefrag, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080").rstrip("/")
SCOPE = os.environ.get("BROWSER_SCREENSHOT_SCOPE", "core").strip().lower()
FAIL_ON_CONSOLE_ERRORS = os.environ.get(
    "BROWSER_FAIL_ON_CONSOLE_ERRORS", "true"
).strip().lower() in {"1", "true", "yes", "on"}
OUTPUT_DIR = Path(os.environ.get("BROWSER_SCREENSHOT_DIR", "test-results/browser"))
USERNAME = os.environ.get("BROWSER_TEST_USERNAME", "browser-test-admin")
PASSWORD = os.environ.get("BROWSER_TEST_PASSWORD", "Browser-Test-2026!")

CORE_LABELS = (
    "Übersicht",
    "Projekte",
    "Aufgaben",
    "Chat",
    "Dokumente",
    "Kalender",
    "Kontakte",
    "Federation",
    "Sicherheit",
    "Einstellungen",
    "Administration",
    "Mini Services",
)


def compact(value: str) -> str:
    return " ".join((value or "").split())


def slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return safe or "page"


def same_origin(url: str) -> bool:
    base = urlparse(BASE_URL)
    candidate = urlparse(url)
    return candidate.scheme == base.scheme and candidate.netloc == base.netloc


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {
        "base_url": BASE_URL,
        "scope": SCOPE,
        "fail_on_console_errors": FAIL_ON_CONSOLE_ERRORS,
        "pages": [],
        "console_errors": [],
        "page_errors": [],
        "server_errors": [],
        "failures": [],
    }

    console_errors: list[dict[str, str]] = summary["console_errors"]  # type: ignore[assignment]
    page_errors: list[dict[str, str]] = summary["page_errors"]  # type: ignore[assignment]
    server_errors: list[dict[str, object]] = summary["server_errors"]  # type: ignore[assignment]
    failures: list[str] = summary["failures"]  # type: ignore[assignment]
    pages: list[dict[str, object]] = summary["pages"]  # type: ignore[assignment]

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                locale="de-DE",
                timezone_id="Europe/Berlin",
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.set_default_timeout(20_000)

            page.on(
                "console",
                lambda message: console_errors.append(
                    {"url": page.url, "text": message.text}
                )
                if message.type == "error"
                else None,
            )
            page.on(
                "pageerror",
                lambda error: page_errors.append(
                    {"url": page.url, "text": str(error)}
                ),
            )

            def record_response(response) -> None:
                if same_origin(response.url) and response.status >= 500:
                    server_errors.append(
                        {
                            "url": response.url,
                            "status": response.status,
                            "page": page.url,
                        }
                    )

            page.on("response", record_response)

            register_response = page.goto(
                f"{BASE_URL}/auth/register", wait_until="domcontentloaded"
            )
            if register_response is None or register_response.status >= 400:
                status = register_response.status if register_response else "no-response"
                raise RuntimeError(f"Registrierungsseite nicht erreichbar: {status}")

            page.locator("#username").fill(USERNAME)
            page.locator("#password").fill(PASSWORD)
            page.get_by_role("button", name="Lokales Konto erstellen").click()
            page.wait_for_url("**/auth/login")

            page.locator("#username").fill(USERNAME)
            page.locator("#password").fill(PASSWORD)
            page.get_by_role("button", name="Anmelden").click()
            page.wait_for_load_state("domcontentloaded")
            if "/auth/login" in page.url:
                raise RuntimeError("Anmeldung des Browser-Testbenutzers ist fehlgeschlagen.")

            page.wait_for_selector("nav[aria-label='Hauptnavigation']")

            raw_links = page.locator("a.nav-link").evaluate_all(
                """elements => elements.map(element => ({
                    label: (element.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim(),
                    href: element.href
                }))"""
            )

            deduplicated: list[dict[str, str]] = []
            seen_urls: set[str] = set()
            for item in raw_links:
                label = compact(str(item.get("label", "")))
                href = urldefrag(str(item.get("href", "")))[0]
                if not label or not href or not same_origin(href) or href in seen_urls:
                    continue
                seen_urls.add(href)
                deduplicated.append({"label": label, "href": href})

            if SCOPE == "all-navigation":
                selected = deduplicated
            elif SCOPE == "core":
                by_label = {item["label"]: item for item in deduplicated}
                selected = []
                missing = []
                for label in CORE_LABELS:
                    item = by_label.get(label)
                    if item is None:
                        missing.append(label)
                    else:
                        selected.append(item)
                if missing:
                    failures.append(
                        "Kernnavigation fehlt: " + ", ".join(missing)
                    )
            else:
                raise RuntimeError(
                    f"Unbekannter BROWSER_SCREENSHOT_SCOPE: {SCOPE!r}"
                )

            if not selected:
                raise RuntimeError("Keine Navigationsseiten für den Screenshot-Test gefunden.")

            for number, item in enumerate(selected, start=1):
                label = item["label"]
                url = item["href"]
                entry: dict[str, object] = {
                    "label": label,
                    "requested_url": url,
                }
                try:
                    response = page.goto(url, wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=3_000)
                    except PlaywrightTimeoutError:
                        pass
                    page.wait_for_timeout(200)

                    status = response.status if response is not None else None
                    entry["status"] = status
                    entry["final_url"] = page.url

                    filename = f"{number:02d}-{slug(label)}.png"
                    page.screenshot(
                        path=str(OUTPUT_DIR / filename),
                        full_page=True,
                        animations="disabled",
                    )
                    entry["screenshot"] = filename

                    if status is None or status >= 400:
                        failures.append(
                            f"{label}: HTTP {status if status is not None else 'unbekannt'}"
                        )
                    if "/auth/login" in page.url:
                        failures.append(f"{label}: unerwartet zur Anmeldung umgeleitet")
                except PlaywrightError as exc:
                    entry["error"] = str(exc)
                    failures.append(f"{label}: Browserfehler: {exc}")
                pages.append(entry)

            context.close()
            browser.close()

    except Exception as exc:
        failures.append(f"Fataler Browser-Testfehler: {type(exc).__name__}: {exc}")

    if page_errors:
        failures.append(f"{len(page_errors)} JavaScript-Page-Error(s) erkannt.")
    if server_errors:
        failures.append(f"{len(server_errors)} HTTP-5xx-Antwort(en) erkannt.")
    if FAIL_ON_CONSOLE_ERRORS and console_errors:
        failures.append(f"{len(console_errors)} Browser-Console-Error(s) erkannt.")

    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "pages": len(pages),
                "console_errors": len(console_errors),
                "page_errors": len(page_errors),
                "server_errors": len(server_errors),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
