#!/usr/bin/env python3
"""Manual Playwright smoke test for SimpleOffice4Me.

The test creates the first local admin through the real registration form,
logs in, opens core navigation pages (or every navigation link), stores
full-page screenshots and writes a machine-readable summary.
"""

from __future__ import annotations

import hashlib
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
PEER_BASE_URL = os.environ.get("PEER_BASE_URL", "").rstrip("/")
PEER_TO_PEER = bool(PEER_BASE_URL)
PEER_A_DOCKER_URL = os.environ.get(
    "BROWSER_PEER_A_DOCKER_URL", "http://peer-a:8080"
).rstrip("/")
PEER_B_DOCKER_URL = os.environ.get(
    "BROWSER_PEER_B_DOCKER_URL", "http://peer-b:8080"
).rstrip("/")
PEER_SHARED_TOKEN = os.environ.get(
    "BROWSER_PEER_SHARED_TOKEN", "browser-p2p-not-a-secret"
)
SCOPE = os.environ.get("BROWSER_SCREENSHOT_SCOPE", "core").strip().lower()
FAIL_ON_CONSOLE_ERRORS = os.environ.get(
    "BROWSER_FAIL_ON_CONSOLE_ERRORS", "true"
).strip().lower() in {"1", "true", "yes", "on"}
OUTPUT_DIR = Path(os.environ.get("BROWSER_SCREENSHOT_DIR", "test-results/browser"))
HTML_DIR = OUTPUT_DIR / "html"
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


def slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return safe or "page"


def same_origin(url: str) -> bool:
    candidate = urlparse(url)
    allowed = {urlparse(BASE_URL)}
    if PEER_BASE_URL:
        allowed.add(urlparse(PEER_BASE_URL))
    return any(
        candidate.scheme == base.scheme
        and candidate.netloc == base.netloc
        for base in allowed
    )


def write_html_snapshot(
    page,
    label: str,
    prefix: str,
    summary: dict[str, object],
    failures: list[str],
) -> None:
    markup = page.content()
    filename = f"{slug(prefix)}-{slug(label)}.html"
    path = HTML_DIR / filename
    path.write_text(
        markup + ("\n" if not markup.endswith("\n") else ""),
        encoding="utf-8",
    )
    snapshots: list[dict[str, object]] = summary["html5"]["snapshots"]  # type: ignore[index,assignment]
    has_doctype = bool(
        re.match(r"^\s*<!doctype\s+html(?:\s[^>]*)?>", markup, re.IGNORECASE)
    )
    snapshots.append(
        {
            "label": label,
            "url": page.url,
            "file": str(path.relative_to(OUTPUT_DIR)),
            "html5_doctype": has_doctype,
        }
    )
    if not has_doctype:
        failures.append(f"{label}: HTML5-Doctype fehlt.")


def attach_peer_diagnostics(
    page,
    label: str,
    summary: dict[str, object],
) -> None:
    console_errors: list[dict[str, str]] = summary["console_errors"]  # type: ignore[assignment]
    page_errors: list[dict[str, str]] = summary["page_errors"]  # type: ignore[assignment]
    server_errors: list[dict[str, object]] = summary["server_errors"]  # type: ignore[assignment]

    def record_console(message) -> None:
        if message.type == "error":
            console_errors.append(
                {"browser": label, "url": page.url, "text": message.text}
            )

    def record_page_error(error) -> None:
        page_errors.append(
            {"browser": label, "url": page.url, "text": str(error)}
        )

    def record_response(response) -> None:
        if same_origin(response.url) and response.status >= 500:
            server_errors.append(
                {
                    "browser": label,
                    "url": response.url,
                    "status": response.status,
                    "page": page.url,
                }
            )

    page.on("console", record_console)
    page.on("pageerror", record_page_error)
    page.on("response", record_response)


def register_and_login(page, base_url: str, username: str) -> None:
    response = page.goto(
        f"{base_url}/auth/register", wait_until="domcontentloaded"
    )
    if response is None or response.status >= 400:
        status = response.status if response else "no-response"
        raise RuntimeError(f"Registrierungsseite nicht erreichbar: {status}")
    page.locator("#username").fill(username)
    page.locator("#password").fill(PASSWORD)
    page.get_by_role(
        "button", name="Lokales Konto erstellen", exact=True
    ).click()
    page.wait_for_url("**/auth/login")
    page.locator("#username").fill(username)
    page.locator("#password").fill(PASSWORD)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_load_state("domcontentloaded")
    if "/auth/login" in page.url:
        raise RuntimeError(f"Anmeldung für {username} ist fehlgeschlagen.")
    page.wait_for_selector("nav[aria-label='Hauptnavigation']")


def configure_peer(
    page,
    *,
    base_url: str,
    peer_id: str,
    peer_label: str,
    peer_docker_url: str,
) -> None:
    response = page.goto(
        f"{base_url}/admin/federation?view=peers", wait_until="domcontentloaded"
    )
    if response is None or response.status >= 400:
        status = response.status if response else "no-response"
        raise RuntimeError(f"Federation-Seite nicht erreichbar: {status}")

    form = page.locator("form").filter(
        has=page.locator("input[name='peer_id']")
    ).filter(
        has=page.locator("input[name='base_url']")
    ).first
    form.locator("input[name='peer_id']").fill(peer_id)
    form.locator("input[name='label']").fill(peer_label)
    form.locator("input[name='base_url']").fill(peer_docker_url)
    form.locator("input[name='token']").fill(PEER_SHARED_TOKEN)
    advanced_policy = form.locator("details").filter(
        has=page.locator("textarea[name='policy_json']")
    ).first
    if advanced_policy.count() and advanced_policy.get_attribute("open") is None:
        advanced_policy.locator("summary").click()
    form.locator("textarea[name='policy_json']").fill(
        json.dumps(
            {
                "documents": {
                    "send": True,
                    "receive": True,
                    "seed": True,
                },
                "chat": {"send": True, "receive": True},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    enabled = form.locator("input[name='enabled']")
    if not enabled.is_checked():
        enabled.check()
    form.get_by_role(
        "button", name="Peer speichern", exact=True
    ).click()
    page.wait_for_load_state("domcontentloaded")
    if "Federation-Peer gespeichert." not in page.locator("body").inner_text():
        raise RuntimeError(f"Peer {peer_id} wurde nicht gespeichert.")

    row = page.locator("tr").filter(has_text=peer_id).first
    row.get_by_role(
        "button", name="Verbindung testen", exact=True
    ).click()
    page.wait_for_load_state("domcontentloaded")
    if "Peer erreichbar:" not in page.locator("body").inner_text():
        raise RuntimeError(f"Peer {peer_id} ist nicht erreichbar.")


def record_p2p_check(
    summary: dict[str, object],
    name: str,
    ok: bool,
    detail: str = "",
) -> None:
    checks: list[dict[str, object]] = summary["peer_to_peer"]["checks"]  # type: ignore[index,assignment]
    checks.append({"name": name, "ok": ok, "detail": detail})


def run_peer_to_peer(
    page_a,
    browser,
    summary: dict[str, object],
    failures: list[str],
) -> None:
    if not PEER_TO_PEER:
        return

    title = f"Browser P2P {RUN_ID}"
    message_a = f"Peer A nach Peer B {RUN_ID}"
    message_b = f"Peer B nach Peer A {RUN_ID}"

    chat_filename = f"p2p-browser-{RUN_ID}.txt"
    chat_payload = (
        "SimpleOffice4Me peer-to-peer browser test\n"
        f"run={RUN_ID}\n"
        "direction=peer-a-to-peer-b\n"
    ).encode("utf-8")
    chat_fixture = FIXTURE_DIR / chat_filename
    chat_fixture.write_bytes(chat_payload)
    chat_sha256 = hashlib.sha256(chat_payload).hexdigest()

    peer_b_filename = f"peer-b-only-{RUN_ID}.bin"
    peer_b_seed = hashlib.sha256(
        f"simpleoffice-p2p-peer-b:{RUN_ID}".encode("utf-8")
    ).digest()
    peer_b_payload = bytearray(
        (
            "SimpleOffice4Me peer B exclusive federation payload\n"
            f"run={RUN_ID}\n"
        ).encode("utf-8")
    )
    counter = 0
    while len(peer_b_payload) < 256 * 1024:
        peer_b_payload.extend(
            hashlib.sha256(
                peer_b_seed + counter.to_bytes(4, "big")
            ).digest()
        )
        counter += 1
    peer_b_fixture = FIXTURE_DIR / peer_b_filename
    peer_b_fixture.write_bytes(bytes(peer_b_payload))
    peer_b_sha256 = hashlib.sha256(peer_b_payload).hexdigest()

    peer_summary: dict[str, object] = summary["peer_to_peer"]  # type: ignore[assignment]
    peer_summary.update(
        {
            "chat_title": title,
            "chat_fixture": chat_filename,
            "chat_fixture_sha256": chat_sha256,
            "peer_b_fixture": peer_b_filename,
            "peer_b_fixture_sha256": peer_b_sha256,
        }
    )

    context_b = browser.new_context(
        viewport={"width": 1440, "height": 1000},
        locale="de-DE",
        timezone_id="Europe/Berlin",
        reduced_motion="reduce",
        accept_downloads=True,
    )
    page_b = context_b.new_page()
    page_b.set_default_timeout(20_000)
    attach_peer_diagnostics(page_b, "peer-b", summary)

    try:
        register_and_login(page_b, PEER_BASE_URL, PEER_USERNAME)
        configure_peer(
            page_a,
            base_url=BASE_URL,
            peer_id="peer-b",
            peer_label="Browser Peer B",
            peer_docker_url=PEER_B_DOCKER_URL,
        )
        configure_peer(
            page_b,
            base_url=PEER_BASE_URL,
            peer_id="peer-a",
            peer_label="Browser Peer A",
            peer_docker_url=PEER_A_DOCKER_URL,
        )
        record_p2p_check(
            summary, "peers_connected", True, "peer-a <-> peer-b"
        )
        write_html_snapshot(
            page_a, "Federation Peer A", "p2p", summary, failures
        )
        write_html_snapshot(
            page_b, "Federation Peer B", "p2p", summary, failures
        )

        page_a.goto(f"{BASE_URL}/chat", wait_until="domcontentloaded")
        page_a.locator(
            "button[data-bs-target='#new-chat-modal']"
        ).first.click()
        new_chat_modal = page_a.locator("#new-chat-modal")
        new_chat_modal.wait_for(state="visible")
        new_chat_modal.locator("#chat-title").fill(title)
        remote_peer_select = new_chat_modal.locator("#remote-peer")
        remote_details = remote_peer_select.locator(
            "xpath=ancestor::details[1]"
        )
        if remote_details.get_attribute("open") is None:
            remote_details.locator("summary").click()
        remote_peer_select.wait_for(state="visible")
        remote_peer_select.select_option("peer-b")
        new_chat_modal.locator("#remote-users").fill(PEER_USERNAME)
        page_a.get_by_role(
            "button", name="Chat anlegen", exact=True
        ).click()
        page_a.wait_for_url(
            re.compile(r".*/chat/rooms/[0-9a-f-]+$")
        )
        room_a_url = page_a.url
        record_p2p_check(
            summary, "chat_created", True, room_a_url
        )

        page_a.locator("#chat-body").fill(message_a)
        page_a.locator("#chat-files").set_input_files(
            str(chat_fixture)
        )
        private_files = page_a.locator(
            "input[name='private_files']"
        )
        if private_files.is_checked():
            private_files.uncheck()
        page_a.get_by_role(
            "button", name="Senden", exact=True
        ).click()
        page_a.wait_for_load_state("domcontentloaded")
        page_a.get_by_text(message_a, exact=True).wait_for()
        page_a.locator(
            "a[href*='/chat/attachments/']"
        ).filter(has_text=chat_filename).last.wait_for()
        record_p2p_check(
            summary,
            "chat_file_sent_a_to_b",
            True,
            chat_filename,
        )
        write_html_snapshot(
            page_a, "Chat Peer A gesendet", "p2p", summary, failures
        )
        page_a.screenshot(
            path=str(OUTPUT_DIR / "p2p-peer-a-chat.png"),
            full_page=True,
            animations="disabled",
        )

        page_b.goto(
            f"{PEER_BASE_URL}/chat",
            wait_until="domcontentloaded",
        )
        room_link = page_b.locator(
            "a[data-chat-row]"
        ).filter(has_text=title).first
        room_link.wait_for()
        room_link.click()
        page_b.wait_for_load_state("domcontentloaded")
        page_b.get_by_text(message_a, exact=True).wait_for()
        page_b.locator(
            "a[href*='/chat/attachments/']"
        ).filter(has_text=chat_filename).last.wait_for()
        record_p2p_check(
            summary,
            "chat_received_on_b",
            True,
            message_a,
        )

        download_link = page_b.locator(
            "a[href*='/chat/attachments/']"
        ).filter(has_text=chat_filename).last
        with page_b.expect_download() as download_info:
            download_link.click()
        downloaded_path = DOWNLOAD_DIR / f"peer-b-{chat_filename}"
        download_info.value.save_as(str(downloaded_path))
        downloaded_sha256 = hashlib.sha256(
            downloaded_path.read_bytes()
        ).hexdigest()
        peer_summary["browser_download_sha256"] = downloaded_sha256
        if downloaded_sha256 != chat_sha256:
            raise RuntimeError(
                "Browser-Download hat eine abweichende SHA-256."
            )
        record_p2p_check(
            summary,
            "browser_download_verified",
            True,
            downloaded_sha256,
        )

        page_b.locator("#chat-body").fill(message_b)
        page_b.get_by_role(
            "button", name="Senden", exact=True
        ).click()
        page_b.wait_for_load_state("domcontentloaded")
        page_b.get_by_text(message_b, exact=True).wait_for()
        record_p2p_check(
            summary,
            "chat_reply_sent_b_to_a",
            True,
            message_b,
        )
        write_html_snapshot(
            page_b,
            "Chat Peer B empfangen und geantwortet",
            "p2p",
            summary,
            failures,
        )
        page_b.screenshot(
            path=str(OUTPUT_DIR / "p2p-peer-b-chat.png"),
            full_page=True,
            animations="disabled",
        )

        page_a.goto(room_a_url, wait_until="domcontentloaded")
        page_a.get_by_text(message_b, exact=True).wait_for()
        record_p2p_check(
            summary,
            "chat_reply_received_on_a",
            True,
            message_b,
        )

        page_b.goto(
            f"{PEER_BASE_URL}/documents/",
            wait_until="domcontentloaded",
        )
        upload_form = page_b.locator("form").filter(
            has=page_b.locator(
                "input[type='file'][name='files']"
            )
        ).first
        archive = upload_form.locator("input[name='archive']")
        if archive.count() and archive.is_checked():
            archive.uncheck()
        upload_form.locator(
            "input[type='file'][name='files']"
        ).set_input_files(str(peer_b_fixture))
        upload_form.get_by_role(
            "button",
            name="Vollständig importieren",
            exact=True,
        ).click()
        page_b.wait_for_load_state("domcontentloaded")
        upload_result = compact(page_b.locator("body").inner_text())
        if "Datei(en) vollständig und hashbasiert importiert." not in upload_result:
            raise RuntimeError(
                "Der Upload auf Peer B wurde nicht erfolgreich bestätigt."
            )
        quick_search = page_b.locator("input[name='quick']")
        quick_search.fill(peer_b_filename)
        page_b.get_by_role(
            "button", name="Finden", exact=True
        ).click()
        page_b.wait_for_load_state("domcontentloaded")
        page_b.get_by_text(
            peer_b_filename, exact=False
        ).first.wait_for()
        record_p2p_check(
            summary,
            "peer_b_document_uploaded",
            True,
            f"{peer_b_filename} sha256={peer_b_sha256}",
        )
        write_html_snapshot(
            page_b,
            "Dokumentupload nur Peer B",
            "p2p",
            summary,
            failures,
        )

        page_a.goto(
            f"{BASE_URL}/admin/federation?view=peers",
            wait_until="domcontentloaded",
        )
        peer_row = page_a.locator("tr").filter(
            has_text="peer-b"
        ).first
        peer_row.get_by_role(
            "button", name="Dateiindex holen", exact=True
        ).click()
        page_a.wait_for_load_state("domcontentloaded")
        page_a.goto(
            f"{BASE_URL}/admin/federation?view=files",
            wait_until="domcontentloaded",
        )

        remote_section = page_a.locator("#remote-files")
        remote_row = remote_section.locator("tr").filter(
            has_text=peer_b_filename
        ).first
        remote_row.wait_for()
        remote_text = compact(
            remote_row.inner_text()
        ).casefold()
        if peer_b_sha256[:12] not in remote_text:
            raise RuntimeError(
                "Remote-Katalog enthält nicht die erwartete SHA-256."
            )
        record_p2p_check(
            summary,
            "remote_catalog_contains_peer_b_document",
            True,
            f"{peer_b_filename} sha256={peer_b_sha256}",
        )
        remote_row.get_by_role(
            "button", name="Vormerken", exact=True
        ).click()
        page_a.wait_for_load_state("domcontentloaded")
        page_a.goto(
            f"{BASE_URL}/admin/federation?view=transfers",
            wait_until="domcontentloaded",
        )
        page_a.get_by_role(
            "button", name="Queue abarbeiten", exact=True
        ).click()
        page_a.wait_for_load_state("domcontentloaded")

        queue_section = page_a.locator(
            "section.card"
        ).filter(
            has_text="Download-Warteschlange"
        ).first
        queue_row = queue_section.locator("tr").filter(
            has_text=peer_b_filename
        ).first
        queue_row.wait_for()
        queue_text = compact(
            queue_row.inner_text()
        ).casefold()
        if "complete" not in queue_text:
            raise RuntimeError(
                f"Peer-to-Peer-Download ist nicht complete: {queue_text}"
            )
        body_text = page_a.locator("body").inner_text()
        if (
            "Download-Pipeline:" not in body_text
            or "fertig" not in body_text
        ):
            raise RuntimeError(
                "Download-Pipeline meldet keinen erfolgreichen Abschluss."
            )

        network_event = page_a.locator("tr").filter(
            has_text="scoped_content_blocks_reused"
        ).first
        if network_event.count() != 1:
            raise RuntimeError("Das erfolgreiche V2-Federation-Ereignis fehlt.")
        network_event_text = compact(network_event.text_content() or "")
        network_match = re.search(
            r"network_bytes['\"\s:]+(\d+)",
            network_event_text,
        )
        network_bytes = int(network_match.group(1)) if network_match else 0
        if network_bytes <= 0:
            raise RuntimeError(
                "Der Federation-Download hat keine Netzwerkbytes von Peer B übertragen."
            )
        peer_summary["peer_to_peer_network_bytes"] = network_bytes
        record_p2p_check(
            summary,
            "peer_to_peer_pull_complete",
            True,
            (
                "Peer-B-exklusive Datei über Federation geladen; "
                f"network_bytes={network_bytes}; "
                f"SHA-256-Endprüfung={peer_b_sha256}"
            ),
        )
        write_html_snapshot(
            page_a,
            "Federation P2P Download",
            "p2p",
            summary,
            failures,
        )
        page_a.screenshot(
            path=str(
                OUTPUT_DIR
                / "p2p-peer-a-federation-download.png"
            ),
            full_page=True,
            animations="disabled",
        )
    finally:
        context_b.close()


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {
        "base_url": BASE_URL,
        "peer_base_url": PEER_BASE_URL,
        "scope": SCOPE,
        "fail_on_console_errors": FAIL_ON_CONSOLE_ERRORS,
        "pages": [],
        "console_errors": [],
        "page_errors": [],
        "server_errors": [],
        "failures": [],
        "html5": {"doctype": "html5", "snapshots": []},
        "peer_to_peer": {"enabled": PEER_TO_PEER, "checks": []},
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
            page.get_by_role("button", name="Lokales Konto erstellen", exact=True).click()
            page.wait_for_url("**/auth/login")

            page.locator("#username").fill(USERNAME)
            page.locator("#password").fill(PASSWORD)
            page.get_by_role("button", name="Anmelden", exact=True).click()
            page.wait_for_load_state("domcontentloaded")
            if "/auth/login" in page.url:
                raise RuntimeError("Anmeldung des Browser-Testbenutzers ist fehlgeschlagen.")

            page.wait_for_selector("nav[aria-label='Hauptnavigation']")

            raw_links = page.locator(
                "a.nav-link:not(.dropdown-toggle), a.dropdown-item"
            ).evaluate_all(
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
                    write_html_snapshot(
                        page,
                        label,
                        f"peer-a-{number:02d}",
                        summary,
                        failures,
                    )

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
