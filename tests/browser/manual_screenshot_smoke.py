#!/usr/bin/env python3
"""Manual Playwright smoke test for SimpleOffice4Me.

The test creates the first local admin through the real registration form,
logs in, inventories live Flask GET routes, crawls reachable internal pages,
stores full-page screenshots and writes a machine-readable summary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from browser_artifacts import make_html_snapshot_writer, slug
from layout_checks import check_readable_layout
from mcp_smoke import run_mcp_smoke
from litellm_smoke import run_litellm_smoke
from peer_to_peer_smoke import run_peer_to_peer
from page_crawl import (
    CrawlFrontier,
    MAX_QUERY_VARIANTS_PER_PATH,
    browser_page_candidate,
    canonical_browser_url,
    discover_links,
    install_navigation_observer,
    load_route_inventory,
    same_primary_origin,
    route_coverage_failures,
    update_route_coverage,
    write_screenshot_gallery,
    write_tested_url_manifest,
)


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080").rstrip("/")
PEER_BASE_URL = os.environ.get("PEER_BASE_URL", "").rstrip("/")
PEER_TO_PEER = bool(PEER_BASE_URL)
PEER_A_DOCKER_URL = os.environ.get(
    "BROWSER_PEER_A_DOCKER_URL", "http://peer-a:8080"
).rstrip("/")
PEER_B_DOCKER_URL = os.environ.get(
    "BROWSER_PEER_B_DOCKER_URL", "http://peer-b:8080"
).rstrip("/")
PEER_A_TOKEN = os.environ.get("BROWSER_PEER_A_TOKEN", "browser-peer-a-not-a-secret")
PEER_B_TOKEN = os.environ.get("BROWSER_PEER_B_TOKEN", "browser-peer-b-not-a-secret")
SCOPE = os.environ.get("BROWSER_SCREENSHOT_SCOPE", "all-pages").strip().lower()
ROUTE_INVENTORY_PATH = Path(os.environ.get("BROWSER_ROUTE_INVENTORY", "test-results/browser/route-inventory.json"))
MAX_PAGES = max(1, int(os.environ.get("BROWSER_MAX_PAGES", "2500")))
FAIL_ON_CONSOLE_ERRORS = os.environ.get(
    "BROWSER_FAIL_ON_CONSOLE_ERRORS", "true"
).strip().lower() in {"1", "true", "yes", "on"}
OUTPUT_DIR = Path(os.environ.get("BROWSER_SCREENSHOT_DIR", "test-results/browser"))
HTML_DIR = OUTPUT_DIR / "html"
write_html_snapshot = make_html_snapshot_writer(OUTPUT_DIR)
DOWNLOAD_DIR = OUTPUT_DIR / "downloads"
FIXTURE_DIR = OUTPUT_DIR / "fixtures"
USERNAME = os.environ.get("BROWSER_TEST_USERNAME", "browser-test-admin")
PEER_USERNAME = os.environ.get(
    "BROWSER_PEER_TEST_USERNAME", "browser-peer-admin"
)
PASSWORD = os.environ.get("BROWSER_TEST_PASSWORD", "Browser-Test-2026!")
RUN_ID = re.sub(
    r"[^A-Za-z0-9_.-]+",
    "-",
    os.environ.get("GITHUB_RUN_ID", "local"),
)[:80] or "local"

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


def same_origin(url: str) -> bool:
    return same_primary_origin(url, BASE_URL) or (
        bool(PEER_BASE_URL) and same_primary_origin(url, PEER_BASE_URL)
    )



def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {
        "base_url": BASE_URL,
        "peer_base_url": PEER_BASE_URL,
        "scope": SCOPE,
        "max_pages": MAX_PAGES,
        "max_query_variants_per_path": MAX_QUERY_VARIANTS_PER_PATH,
        "crawl_skipped_query_variants": 0,
        "fail_on_console_errors": FAIL_ON_CONSOLE_ERRORS,
        "pages": [],
        "console_errors": [],
        "page_errors": [],
        "server_errors": [],
        "failures": [],
        "html5": {"doctype": "html5", "snapshots": []},
        "peer_to_peer": {"enabled": PEER_TO_PEER, "checks": []},
        "mcp_ui": {"checks": []},
    }

    frontier = CrawlFrontier(
        summary,
        base_url=BASE_URL,
        max_query_variants=MAX_QUERY_VARIANTS_PER_PATH,
    )

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
            install_navigation_observer(context)
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
            page.get_by_role("button", name="Lokales Konto erstellen", exact=True).click()
            page.wait_for_url("**/auth/login")

            page.locator("#username").fill(USERNAME)
            page.locator("#password").fill(PASSWORD)
            page.get_by_role("button", name="Anmelden", exact=True).click()
            page.wait_for_load_state("domcontentloaded")
            if "/auth/login" in page.url:
                raise RuntimeError("Anmeldung des Browser-Testbenutzers ist fehlgeschlagen.")

            page.wait_for_selector("nav[aria-label='Hauptnavigation']")

            run_mcp_smoke(page, output_dir=OUTPUT_DIR, run_id=RUN_ID, checks=summary["mcp_ui"]["checks"])
            run_litellm_smoke(page, output_dir=OUTPUT_DIR, checks=summary.setdefault("litellm_ui", {"checks": []})["checks"])
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(f"{BASE_URL}/documents/", wait_until="domcontentloaded")
            run_mcp_smoke(page, output_dir=OUTPUT_DIR, run_id=RUN_ID, checks=summary["mcp_ui"]["checks"])
            run_litellm_smoke(page, output_dir=OUTPUT_DIR, checks=summary.setdefault("litellm_ui", {"checks": []})["checks"])
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(f"{BASE_URL}/documents/", wait_until="domcontentloaded")

            raw_links = page.locator(
                "a.nav-link:not(.dropdown-toggle), a.dropdown-item"
            ).evaluate_all(
                """elements => elements.map(element => ({
                    label: (element.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim(),
                    href: element.href
                }))"""
            )

            navigation: list[dict[str, str]] = []
            seen_navigation_urls: set[str] = set()
            for item in raw_links:
                label = compact(str(item.get("label", "")))
                href = canonical_browser_url(str(item.get("href", "")))
                if (
                    not label
                    or not href
                    or not browser_page_candidate(href, BASE_URL)
                    or href in seen_navigation_urls
                ):
                    continue
                seen_navigation_urls.add(href)
                navigation.append(
                    {"label": label, "href": href, "source": "navigation"}
                )

            route_seeds = load_route_inventory(
                summary,
                base_url=BASE_URL,
                route_inventory_path=ROUTE_INVENTORY_PATH,
            )
            if SCOPE == "all-pages":
                selected = [*navigation, *route_seeds]
            elif SCOPE == "all-navigation":
                selected = navigation
            elif SCOPE == "core":
                by_label = {item["label"]: item for item in navigation}
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
                raise RuntimeError("Keine Browser-Seiten für den Screenshot-Test gefunden.")

            queue = frontier.queue
            enqueue = frontier.enqueue
            if SCOPE == "all-pages":
                page.on("request", frontier.observe_document_request)

            for item in selected:
                enqueue(item)

            number = 0
            while queue:
                if number >= MAX_PAGES:
                    failures.append(
                        f"Browser-Crawl hat das Limit von {MAX_PAGES} Seiten erreicht; "
                        f"{len(queue)} URL(s) blieben ungeprüft."
                    )
                    break

                item = queue.popleft()
                number += 1
                label = item["label"]
                url = item["href"]
                entry: dict[str, object] = {
                    "label": label,
                    "requested_url": url,
                    "source": item.get("source", "unknown"),
                }
                try:
                    response = page.goto(url, wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=3_000)
                    except PlaywrightTimeoutError:
                        pass
                    page.wait_for_timeout(150)

                    status = response.status if response is not None else None
                    content_type = (
                        response.headers.get("content-type", "")
                        if response is not None
                        else ""
                    )
                    entry["status"] = status
                    entry["content_type"] = content_type
                    entry["final_url"] = page.url

                    if not same_primary_origin(page.url, BASE_URL):
                        entry["external_redirect"] = True
                        failures.append(
                            f"{label}: Weiterleitung außerhalb der Testinstanz ({page.url})"
                        )
                        pages.append(entry)
                        continue

                    is_html = "text/html" in content_type.casefold()
                    entry["html"] = is_html
                    if is_html:
                        entry["layout_checks"] = check_readable_layout(page)
                        for check in entry["layout_checks"]:
                            if check["collapsed_text"]:
                                failures.append(f"{label}: zusammengedrückter Text bei {check['viewport_width']}px")
                        filename = f"{number:04d}-{slug(label)}.png"
                        page.screenshot(
                            path=str(OUTPUT_DIR / filename),
                            full_page=True,
                            animations="disabled",
                        )
                        entry["screenshot"] = filename
                        write_html_snapshot(
                            page,
                            label,
                            f"peer-a-{number:04d}",
                            summary,
                            failures,
                        )

                        if SCOPE == "all-pages":
                            discovered_targets = discover_links(page, base_url=BASE_URL)
                            entry["discovered_urls"] = len(discovered_targets)
                            for discovered in discovered_targets:
                                enqueue(discovered)

                    source = str(entry.get("source") or "")
                    status_is_failure = (
                        status is None
                        or status >= 500
                        or status == 404
                        or (source != "route-inventory" and status >= 400)
                    )
                    if status_is_failure:
                        failures.append(
                            f"{label}: HTTP {status if status is not None else 'unbekannt'} ({url})"
                        )
                    elif status is not None and status >= 400:
                        entry["access_limited"] = True
                    if "/auth/login" in page.url:
                        failures.append(
                            f"{label}: unerwartet zur Anmeldung umgeleitet ({url})"
                        )
                except PlaywrightError as exc:
                    entry["error"] = str(exc)
                    failures.append(f"{label}: Browserfehler: {exc}")
                pages.append(entry)

            if PEER_TO_PEER:
                try:
                    run_peer_to_peer(
                        page,
                        browser,
                        summary,
                        failures,
                    )
                except Exception as exc:
                    record_p2p_check(
                        summary,
                        "peer_to_peer_flow",
                        False,
                        f"{type(exc).__name__}: {exc}",
                    )
                    failures.append(
                        "Peer-to-Peer-Browser-Test: "
                        f"{type(exc).__name__}: {exc}"
                    )

            context.close()
            browser.close()

    except Exception as exc:
        failures.append(f"Fataler Browser-Testfehler: {type(exc).__name__}: {exc}")

    update_route_coverage(summary)
    if SCOPE == "all-pages":
        failures.extend(
            route_coverage_failures(summary)
        )
    write_tested_url_manifest(summary, output_dir=OUTPUT_DIR)
    write_screenshot_gallery(summary, output_dir=OUTPUT_DIR)

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
                "html_snapshots": len(summary["html5"]["snapshots"]),  # type: ignore[index]
                "route_coverage": summary.get("route_coverage"),
                "discovery": summary.get("discovery"),
                "peer_to_peer": PEER_TO_PEER,
                "p2p_checks": summary["peer_to_peer"]["checks"],  # type: ignore[index]
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
