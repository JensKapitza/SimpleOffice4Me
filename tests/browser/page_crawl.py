"""Page discovery helpers for the manual Playwright screenshot test."""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlparse


CRAWL_SKIP_PREFIXES = (
    "/api/",
    "/caldav",
    "/carddav",
    "/webdav",
    "/dav/",
    "/federation/",
    "/s3",
    "/.well-known/",
    "/static/",
)
CRAWL_SKIP_EXACT = {
    "/favicon.ico",
    "/auth/login",
    "/auth/logout",
    "/auth/register",
}
CRAWL_SKIP_AUTH_PREFIXES = (
    "/auth/google",
)
CRAWL_SKIP_SUFFIXES = (
    ".7z", ".bin", ".csv", ".doc", ".docx", ".eml", ".gif", ".gz", ".ics",
    ".jpeg", ".jpg", ".js", ".json", ".odt", ".ods", ".odp", ".pdf", ".png",
    ".ppt", ".pptx", ".svg", ".tar", ".txt", ".vcf", ".webmanifest", ".webp",
    ".xls", ".xlsx", ".xml", ".zip",
)
CRAWL_VOLATILE_QUERY_KEYS = {
    "cursor", "date", "day", "end", "from", "limit", "month", "offset", "order",
    "page", "q", "query", "search", "sort", "start", "to", "week", "year",
}
CRAWL_SENSITIVE_QUERY_KEYS = {
    "code", "key", "secret", "sig", "signature", "state", "token",
}
MAX_QUERY_VARIANTS_PER_PATH = max(
    1, int(os.environ.get("BROWSER_MAX_QUERY_VARIANTS_PER_PATH", "20"))
)


def _compact(value: str) -> str:
    return " ".join((value or "").split())


def same_primary_origin(url: str, base_url: str) -> bool:
    candidate = urlparse(url)
    base = urlparse(base_url)
    return candidate.scheme == base.scheme and candidate.netloc == base.netloc


def canonical_browser_url(url: str) -> str:
    clean = urldefrag(url)[0]
    parsed = urlparse(clean)
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in CRAWL_VOLATILE_QUERY_KEYS
    ]
    return parsed._replace(
        query=urlencode(sorted(filtered_query)),
        fragment="",
    ).geturl()


def browser_page_candidate(url: str, base_url: str) -> bool:
    if not url or not same_primary_origin(url, base_url):
        return False
    parsed = urlparse(url)
    path = parsed.path or "/"
    lowered = path.casefold()
    query_keys = {
        key.casefold()
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
    }
    if query_keys & CRAWL_SENSITIVE_QUERY_KEYS:
        return False
    if path in CRAWL_SKIP_EXACT or any(
        path.startswith(prefix) for prefix in CRAWL_SKIP_PREFIXES
    ):
        return False
    if any(path.startswith(prefix) for prefix in CRAWL_SKIP_AUTH_PREFIXES):
        return False
    if lowered.endswith(CRAWL_SKIP_SUFFIXES):
        return False
    if any(
        token in lowered
        for token in ("/download", "/export", "/blob", "/chunks/", "/stream")
    ):
        return False
    return True


def load_route_inventory(
    summary: dict[str, object],
    *,
    base_url: str,
    route_inventory_path: Path,
) -> list[dict[str, str]]:
    route_summary: dict[str, object] = {
        "file": str(route_inventory_path),
        "loaded": False,
        "route_count": 0,
        "browser_candidate_count": 0,
        "static_browser_candidate_count": 0,
        "dynamic_browser_candidate_count": 0,
        "routes": [],
    }
    summary["route_inventory"] = route_summary
    if not route_inventory_path.exists():
        return []

    payload = json.loads(route_inventory_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Route-Inventar ist kein JSON-Objekt.")
    route_summary.update(payload)
    route_summary["loaded"] = True

    seeds: list[dict[str, str]] = []
    for item in payload.get("routes", []):
        if (
            not isinstance(item, dict)
            or not item.get("browser_candidate")
            or item.get("dynamic")
        ):
            continue
        rule = str(item.get("rule", ""))
        if not rule.startswith("/"):
            continue
        href = canonical_browser_url(
            urljoin(base_url + "/", rule.lstrip("/"))
        )
        if browser_page_candidate(href, base_url):
            seeds.append(
                {
                    "label": str(item.get("endpoint") or rule),
                    "href": href,
                    "source": "route-inventory",
                }
            )
    return seeds


def discover_links(page, *, base_url: str) -> list[dict[str, str]]:
    raw_links = page.locator("a[href]").evaluate_all(
        """elements => elements.map(element => ({
            label: (element.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim(),
            href: element.href,
            download: element.hasAttribute('download')
        }))"""
    )
    discovered: list[dict[str, str]] = []
    for item in raw_links:
        if item.get("download"):
            continue
        href = canonical_browser_url(str(item.get("href", "")))
        if not browser_page_candidate(href, base_url):
            continue
        label = _compact(str(item.get("label", ""))) or urlparse(href).path or href
        discovered.append({"label": label, "href": href, "source": "crawl"})
    return discovered


def write_screenshot_gallery(
    summary: dict[str, object],
    *,
    output_dir: Path,
) -> None:
    pages: list[dict[str, object]] = summary["pages"]  # type: ignore[assignment]
    cards: list[str] = []
    for entry in pages:
        screenshot = str(entry.get("screenshot") or "")
        if not screenshot:
            continue
        label = html.escape(
            str(entry.get("label") or entry.get("requested_url") or "Seite")
        )
        requested = html.escape(str(entry.get("requested_url") or ""))
        final_url = html.escape(str(entry.get("final_url") or ""))
        status = html.escape(str(entry.get("status") or ""))
        source = html.escape(str(entry.get("source") or ""))
        cards.append(
            "<article><h2>" + label + "</h2>"
            + "<p>HTTP " + status + " · " + source + "</p>"
            + "<p><code>" + requested + "</code></p>"
            + (
                "<p><code>" + final_url + "</code></p>"
                if final_url != requested
                else ""
            )
            + '<a href="' + html.escape(screenshot)
            + '"><img loading="lazy" src="' + html.escape(screenshot)
            + '" alt="' + label + '"></a></article>'
        )

    markup = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SimpleOffice4Me Browser Screenshots</title><style>
body{font-family:system-ui,sans-serif;margin:24px;background:#f5f6f8;color:#111}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px}article{background:#fff;border:1px solid #d9dde5;border-radius:10px;padding:16px;box-shadow:0 2px 8px #0001}h1{margin:0 0 20px}h2{font-size:1rem;margin:0 0 8px}p{font-size:.82rem;overflow-wrap:anywhere}img{display:block;width:100%;height:auto;border:1px solid #ddd;border-radius:6px}code{font-size:.75rem}</style></head><body>
<h1>SimpleOffice4Me Browser Screenshots</h1><p>""" + html.escape(str(len(cards))) + """ HTML-Seiten mit Screenshot.</p><main>""" + "\n".join(cards) + """</main></body></html>
"""
    (output_dir / "index.html").write_text(markup, encoding="utf-8")


def update_route_coverage(summary: dict[str, object]) -> None:
    inventory = summary.get("route_inventory")
    if not isinstance(inventory, dict) or not inventory.get("loaded"):
        summary["route_coverage"] = {"available": False}
        return

    visited_paths: set[str] = set()
    for entry in summary.get("pages", []):
        if not isinstance(entry, dict):
            continue
        for key in ("requested_url", "final_url"):
            value = str(entry.get(key) or "")
            if value:
                visited_paths.add(urlparse(value).path)

    covered = 0
    uncovered_static: list[dict[str, object]] = []
    uncovered_dynamic: list[dict[str, object]] = []
    for item in inventory.get("routes", []):
        if not isinstance(item, dict) or not item.get("browser_candidate"):
            continue
        regex = str(item.get("path_regex") or "")
        matched = (
            any(re.match(regex, path) for path in visited_paths)
            if regex
            else False
        )
        item["covered_by_browser"] = matched
        if matched:
            covered += 1
        elif item.get("dynamic"):
            uncovered_dynamic.append(
                {"rule": item.get("rule"), "endpoint": item.get("endpoint")}
            )
        else:
            uncovered_static.append(
                {"rule": item.get("rule"), "endpoint": item.get("endpoint")}
            )

    summary["route_coverage"] = {
        "available": True,
        "covered_candidates": covered,
        "candidate_routes": inventory.get("browser_candidate_count", 0),
        "uncovered_static": uncovered_static,
        "uncovered_dynamic": uncovered_dynamic,
    }
