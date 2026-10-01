"""Page discovery helpers for the manual Playwright screenshot test."""

from __future__ import annotations

import html
import json
import os
import re
from collections import deque
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
    "/mcp",
    "/network-boot/ipxe",
)
CRAWL_SKIP_EXACT = {
    "/favicon.ico",
    "/auth/login",
    "/auth/logout",
    "/auth/register",
    "/admin/activity",
    "/documents/mail/autoconfig",
    "/inventory/amazon",
    "/inventory/lookup",
    "/inventory/marketplace/search",
    "/mcp",
    "/network-boot/ipxe",
    "/resource-commander/api/range",
    "/shopping/barcode",
    "/vault/api/v1/search",
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
    1, int(os.environ.get("BROWSER_MAX_QUERY_VARIANTS_PER_PATH", "50"))
)

NAVIGATION_OBSERVER_SCRIPT = r"""
(() => {
  if (window.__simpleofficeCrawlObserverInstalled) return;
  window.__simpleofficeCrawlObserverInstalled = true;
  window.__simpleofficeCrawlUrls = window.__simpleofficeCrawlUrls || [];
  const remember = value => {
    if (value === null || value === undefined || value === '') return;
    try {
      const href = new URL(String(value), document.baseURI).href;
      if (!window.__simpleofficeCrawlUrls.includes(href)) {
        window.__simpleofficeCrawlUrls.push(href);
      }
    } catch (_) {}
  };
  for (const method of ['pushState', 'replaceState']) {
    const original = history[method];
    history[method] = function(state, title, url) {
      remember(url);
      return original.apply(this, arguments);
    };
  }
  addEventListener('popstate', () => remember(location.href));
})();
"""


def install_navigation_observer(context) -> None:
    """Capture client-side navigation targets before application scripts run."""
    context.add_init_script(NAVIGATION_OBSERVER_SCRIPT)


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
    if "/api/" in lowered:
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



class CrawlFrontier:
    """Bounded same-origin crawl queue with discovery diagnostics."""

    def __init__(
        self,
        summary: dict[str, object],
        *,
        base_url: str,
        max_query_variants: int,
    ) -> None:
        self.summary = summary
        self.base_url = base_url
        self.max_query_variants = max(1, int(max_query_variants))
        self.queue: deque[dict[str, str]] = deque()
        self.queued_urls: set[str] = set()
        self.queued_query_variants: dict[str, int] = {}
        self.queued_by_source: dict[str, int] = {}
        self.discovery: dict[str, object] = {
            "queued_by_source": self.queued_by_source,
            "duplicate_urls": 0,
            "rejected_urls": 0,
            "observed_document_requests": 0,
        }
        self.summary["discovery"] = self.discovery

    def enqueue(self, item: dict[str, str]) -> bool:
        href = canonical_browser_url(item["href"])
        if not browser_page_candidate(href, self.base_url):
            self.discovery["rejected_urls"] = int(self.discovery["rejected_urls"]) + 1
            return False
        if href in self.queued_urls:
            self.discovery["duplicate_urls"] = int(self.discovery["duplicate_urls"]) + 1
            return False

        parsed = urlparse(href)
        path_key = parsed.path or "/"
        if parsed.query:
            variants = self.queued_query_variants.get(path_key, 0)
            if variants >= self.max_query_variants:
                self.summary["crawl_skipped_query_variants"] = int(
                    self.summary.get("crawl_skipped_query_variants", 0)
                ) + 1
                return False
            self.queued_query_variants[path_key] = variants + 1

        source = _compact(str(item.get("source") or "unknown")) or "unknown"
        self.queued_urls.add(href)
        self.queue.append({**item, "href": href, "source": source})
        self.queued_by_source[source] = self.queued_by_source.get(source, 0) + 1
        return True

    def observe_document_request(self, request) -> None:
        if request.resource_type != "document":
            return
        self.discovery["observed_document_requests"] = (
            int(self.discovery["observed_document_requests"]) + 1
        )
        href = canonical_browser_url(request.url)
        self.enqueue(
            {
                "label": urlparse(href).path or href,
                "href": href,
                "source": "browser-document",
            }
        )


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


def normalize_discovered_targets(
    raw_targets: list[dict[str, object]],
    *,
    base_url: str,
) -> list[dict[str, str]]:
    """Normalize and filter browser-discovered navigation targets."""
    discovered: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_targets:
        if not isinstance(item, dict) or item.get("download"):
            continue
        href = canonical_browser_url(str(item.get("href", "")))
        if (
            not browser_page_candidate(href, base_url)
            or href in seen
        ):
            continue
        seen.add(href)
        label = _compact(str(item.get("label", ""))) or urlparse(href).path or href
        source = _compact(str(item.get("source", ""))) or "dom-link"
        discovered.append({"label": label, "href": href, "source": source})
    return discovered


def discover_links(page, *, base_url: str) -> list[dict[str, str]]:
    """Discover all safe same-origin navigation targets in the rendered page."""
    raw_targets = page.evaluate(
        """() => {
            const items = [];
            const text = element => (
                element?.getAttribute?.('aria-label')
                || element?.innerText
                || element?.textContent
                || ''
            ).replace(/\\s+/g, ' ').trim();
            const add = (element, href, source, label = '') => {
                if (!href) return;
                try {
                    items.push({
                        label: label || text(element),
                        href: new URL(href, document.baseURI).href,
                        source,
                        download: Boolean(element?.hasAttribute?.('download')),
                    });
                } catch (_) {}
            };

            document.querySelectorAll('a[href], area[href]').forEach(element => {
                add(element, element.getAttribute('href'), 'dom-link');
            });
            document.querySelectorAll('iframe[src], frame[src]').forEach(element => {
                add(element, element.getAttribute('src'), 'dom-frame');
            });
            document.querySelectorAll('form').forEach(form => {
                const method = (form.getAttribute('method') || 'get').toLowerCase();
                if (method === 'get') add(form, form.getAttribute('action') || location.href, 'dom-get-form');
            });
            document.querySelectorAll('button[formaction], input[formaction]').forEach(element => {
                const form = element.form;
                const method = (
                    element.getAttribute('formmethod')
                    || form?.getAttribute('method')
                    || 'get'
                ).toLowerCase();
                if (method === 'get') add(element, element.getAttribute('formaction'), 'dom-get-formaction');
            });
            document.querySelectorAll('a[data-href], [role="link"][data-href]').forEach(element => {
                add(element, element.getAttribute('data-href'), 'dom-data-href');
            });
            document.querySelectorAll('meta[http-equiv="refresh" i][content]').forEach(element => {
                const content = element.getAttribute('content') || '';
                const match = content.match(/(?:^|;)\\s*url\\s*=\\s*(?:"([^"]+)"|'([^']+)'|([^;]+))/i);
                const href = match && (match[1] || match[2] || match[3]);
                if (href) add(element, href.trim(), 'meta-refresh', document.title);
            });
            for (const href of (window.__simpleofficeCrawlUrls || [])) {
                add(null, href, 'history-api', document.title);
            }
            return items;
        }"""
    )
    if not isinstance(raw_targets, list):
        return []
    return normalize_discovered_targets(raw_targets, base_url=base_url)


def write_screenshot_gallery(
    summary: dict[str, object],
    *,
    output_dir: Path,
) -> None:
    pages: list[dict[str, object]] = summary["pages"]  # type: ignore[assignment]
    coverage = summary.get("route_coverage")
    coverage_text = ""
    if isinstance(coverage, dict) and coverage.get("available"):
        coverage_text = (
            f" · Routen {coverage.get('covered_candidates', 0)}/"
            f"{coverage.get('candidate_routes', 0)} abgedeckt"
            f" · statisch offen {coverage.get('uncovered_static_count', 0)}"
            f" · dynamisch offen {coverage.get('uncovered_dynamic_count', 0)}"
        )
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
        search_blob = html.escape(
            " ".join((label, requested, final_url, source)).casefold(),
            quote=True,
        )
        cards.append(
            '<article data-search="' + search_blob + '"><h2>' + label + "</h2>"
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
body{font-family:system-ui,sans-serif;margin:24px;background:#f5f6f8;color:#111}header{position:sticky;top:0;z-index:5;background:#f5f6f8;padding:0 0 16px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px}article{background:#fff;border:1px solid #d9dde5;border-radius:10px;padding:16px;box-shadow:0 2px 8px #0001}article[hidden]{display:none}h1{margin:0 0 8px}h2{font-size:1rem;margin:0 0 8px}p{font-size:.82rem;overflow-wrap:anywhere}input{width:min(720px,100%);box-sizing:border-box;padding:10px 12px;border:1px solid #b8bec9;border-radius:8px;background:#fff}img{display:block;width:100%;height:auto;border:1px solid #ddd;border-radius:6px}code{font-size:.75rem}</style></head><body>
<header><h1>SimpleOffice4Me Browser Screenshots</h1><p>""" + html.escape(str(len(cards))) + """ HTML-Seiten mit Screenshot""" + html.escape(coverage_text) + """.</p><input id="page-filter" type="search" placeholder="Seite, URL oder Quelle filtern" autocomplete="off"></header><main>""" + "\n".join(cards) + """</main>
<script>
const filter=document.getElementById('page-filter');
const cards=[...document.querySelectorAll('article[data-search]')];
filter.addEventListener('input',()=>{const q=filter.value.trim().toLocaleLowerCase('de');for(const card of cards){card.hidden=q!==''&&!card.dataset.search.includes(q);}});
</script></body></html>
"""
    (output_dir / "index.html").write_text(markup, encoding="utf-8")



def write_tested_url_manifest(
    summary: dict[str, object],
    *,
    output_dir: Path,
) -> None:
    """Write a compact, diff-friendly inventory of every attempted browser URL."""
    pages: list[dict[str, object]] = summary["pages"]  # type: ignore[assignment]
    rows = [
        "status\thtml\tsource\tscreenshot\trequested_url\tfinal_url",
    ]
    for entry in pages:
        rows.append(
            "\t".join(
                _compact(str(value)).replace("\t", " ")
                for value in (
                    entry.get("status", ""),
                    entry.get("html", ""),
                    entry.get("source", ""),
                    entry.get("screenshot", ""),
                    entry.get("requested_url", ""),
                    entry.get("final_url", ""),
                )
            )
        )
    (output_dir / "tested-urls.tsv").write_text(
        "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def route_coverage_failures(summary: dict[str, object]) -> list[str]:
    """Return actionable coverage failures for the all-pages browser run."""
    coverage = summary.get("route_coverage")
    if not isinstance(coverage, dict) or not coverage.get("available"):
        return ["Browser-Routenabdeckung ist nicht verfügbar."]

    failures: list[str] = []
    uncovered_static = coverage.get("uncovered_static", [])
    if isinstance(uncovered_static, list) and uncovered_static:
        sample = ", ".join(
            str(item.get("rule") or item.get("endpoint") or "?")
            for item in uncovered_static[:12]
            if isinstance(item, dict)
        )
        suffix = "" if len(uncovered_static) <= 12 else f" (+{len(uncovered_static) - 12} weitere)"
        failures.append(
            f"{len(uncovered_static)} statische Browser-Route(n) wurden nicht besucht: "
            f"{sample}{suffix}"
        )

    uncovered_dynamic = coverage.get("uncovered_dynamic", [])
    if require_dynamic and isinstance(uncovered_dynamic, list) and uncovered_dynamic:
        sample = ", ".join(
            str(item.get("rule") or item.get("endpoint") or "?")
            for item in uncovered_dynamic[:12]
            if isinstance(item, dict)
        )
        suffix = "" if len(uncovered_dynamic) <= 12 else f" (+{len(uncovered_dynamic) - 12} weitere)"
        failures.append(
            f"{len(uncovered_dynamic)} dynamische Browser-Route(n) wurden nicht materialisiert: "
            f"{sample}{suffix}"
        )
    return failures


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
        "visited_paths": len(visited_paths),
        "uncovered_static_count": len(uncovered_static),
        "uncovered_dynamic_count": len(uncovered_dynamic),
        "uncovered_static": uncovered_static,
        "uncovered_dynamic": uncovered_dynamic,
    }
