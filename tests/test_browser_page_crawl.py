from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


BROWSER_DIR = Path(__file__).resolve().parent / "browser"
if str(BROWSER_DIR) not in sys.path:
    sys.path.insert(0, str(BROWSER_DIR))

from page_crawl import (  # noqa: E402
    CrawlFrontier,
    browser_page_candidate,
    canonical_browser_url,
    load_route_inventory,
    normalize_discovered_targets,
    route_coverage_failures,
    update_route_coverage,
    write_tested_url_manifest,
)


class BrowserPageCrawlTests(unittest.TestCase):
    def test_canonical_url_keeps_view_and_drops_volatile_query(self):
        actual = canonical_browser_url(
            "http://127.0.0.1:8080/admin/federation"
            "?month=2026-09&view=transfers&page=4#queue"
        )
        self.assertEqual(
            "http://127.0.0.1:8080/admin/federation?view=transfers",
            actual,
        )

    def test_browser_candidate_rejects_non_pages_and_sensitive_links(self):
        base = "http://127.0.0.1:8080"
        rejected = (
            "https://example.invalid/settings",
            f"{base}/api/v3/capabilities",
            f"{base}/documents/abc/download",
            f"{base}/auth/google",
            f"{base}/share/abc?token=secret",
            f"{base}/service-worker.js",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(browser_page_candidate(url, base))

        self.assertTrue(
            browser_page_candidate(
                f"{base}/admin/federation?view=files",
                base,
            )
        )

    def test_dynamic_discovery_keeps_only_safe_same_origin_pages(self):
        base = "http://127.0.0.1:8080"
        raw = [
            {
                "label": "Kontakt",
                "href": f"{base}/contacts/abc",
                "source": "dom-link",
            },
            {
                "label": "Kontakt duplicate",
                "href": f"{base}/contacts/abc#details",
                "source": "history-api",
            },
            {
                "label": "Frame",
                "href": f"{base}/admin/health",
                "source": "dom-frame",
            },
            {
                "label": "GET form",
                "href": f"{base}/documents/search?q=ignored&view=compact",
                "source": "dom-get-form",
            },
            {
                "label": "API",
                "href": f"{base}/api/v3/capabilities",
                "source": "dom-link",
            },
            {
                "label": "Secret",
                "href": f"{base}/share/abc?token=secret",
                "source": "dom-link",
            },
            {
                "label": "Download",
                "href": f"{base}/documents/abc/export",
                "source": "dom-link",
            },
            {
                "label": "External",
                "href": "https://example.invalid/admin",
                "source": "dom-link",
            },
            {
                "label": "Other port",
                "href": "http://127.0.0.1:8081/admin",
                "source": "dom-link",
            },
        ]

        discovered = normalize_discovered_targets(raw, base_url=base)

        self.assertEqual(
            [
                {
                    "label": "Kontakt",
                    "href": f"{base}/contacts/abc",
                    "source": "dom-link",
                },
                {
                    "label": "Frame",
                    "href": f"{base}/admin/health",
                    "source": "dom-frame",
                },
                {
                    "label": "GET form",
                    "href": f"{base}/documents/search?view=compact",
                    "source": "dom-get-form",
                },
            ],
            discovered,
        )

    def test_dynamic_discovery_skips_download_marked_links(self):
        base = "http://127.0.0.1:8080"
        discovered = normalize_discovered_targets(
            [
                {
                    "label": "Export",
                    "href": f"{base}/reports/view",
                    "source": "dom-link",
                    "download": True,
                }
            ],
            base_url=base,
        )
        self.assertEqual([], discovered)

    def test_frontier_bounds_queries_and_reports_rejections(self):
        base = "http://127.0.0.1:8080"
        summary = {"crawl_skipped_query_variants": 0}
        frontier = CrawlFrontier(summary, base_url=base, max_query_variants=1)

        self.assertTrue(
            frontier.enqueue(
                {"label": "One", "href": f"{base}/reports?view=one", "source": "dom-link"}
            )
        )
        self.assertFalse(
            frontier.enqueue(
                {"label": "Duplicate", "href": f"{base}/reports?view=one#x", "source": "history-api"}
            )
        )
        self.assertFalse(
            frontier.enqueue(
                {"label": "Two", "href": f"{base}/reports?view=two", "source": "dom-link"}
            )
        )
        self.assertFalse(
            frontier.enqueue(
                {"label": "External", "href": "https://example.invalid/reports", "source": "dom-link"}
            )
        )

        self.assertEqual(1, summary["crawl_skipped_query_variants"])
        self.assertEqual(1, frontier.discovery["duplicate_urls"])
        self.assertEqual(1, frontier.discovery["rejected_urls"])
        self.assertEqual({"dom-link": 1}, frontier.discovery["queued_by_source"])
        self.assertEqual(1, len(frontier.queue))

    def test_route_inventory_seeds_static_pages_only(self):
        payload = {
            "route_count": 3,
            "browser_candidate_count": 3,
            "static_browser_candidate_count": 2,
            "dynamic_browser_candidate_count": 1,
            "routes": [
                {
                    "rule": "/mail",
                    "endpoint": "mail_routes.index",
                    "dynamic": False,
                    "browser_candidate": True,
                    "path_regex": "^/mail$",
                },
                {
                    "rule": "/admin/federation",
                    "endpoint": "admin.federation",
                    "dynamic": False,
                    "browser_candidate": True,
                    "path_regex": "^/admin/federation$",
                },
                {
                    "rule": "/contacts/<contact_id>",
                    "endpoint": "documents.contact",
                    "dynamic": True,
                    "browser_candidate": True,
                    "path_regex": "^/contacts/[^/]+$",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp) / "routes.json"
            inventory.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            summary: dict[str, object] = {}
            seeds = load_route_inventory(
                summary,
                base_url="http://127.0.0.1:8080",
                route_inventory_path=inventory,
            )

        self.assertEqual(
            {
                "http://127.0.0.1:8080/mail",
                "http://127.0.0.1:8080/admin/federation",
            },
            {item["href"] for item in seeds},
        )
        self.assertTrue(summary["route_inventory"]["loaded"])

    def test_route_coverage_failures_require_all_static_pages(self):
        summary: dict[str, object] = {
            "route_coverage": {
                "available": True,
                "uncovered_static": [
                    {"rule": "/new-page", "endpoint": "new.page"},
                ],
                "uncovered_dynamic": [
                    {"rule": "/contacts/<contact_id>", "endpoint": "contacts.detail"},
                ],
            }
        }

        failures = route_coverage_failures(summary)
        self.assertEqual(1, len(failures))
        self.assertIn("/new-page", failures[0])

        # Dynamic route patterns without a concrete fixture are diagnostics, not failures.
        self.assertEqual(failures, route_coverage_failures(summary))

    def test_tested_url_manifest_maps_urls_to_screenshots(self):
        summary: dict[str, object] = {
            "pages": [
                {
                    "status": 200,
                    "html": True,
                    "source": "route-inventory",
                    "screenshot": "0001-settings.png",
                    "requested_url": "http://127.0.0.1:8080/settings",
                    "final_url": "http://127.0.0.1:8080/settings",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            write_tested_url_manifest(summary, output_dir=output)
            manifest = (output / "tested-urls.tsv").read_text(encoding="utf-8")

        self.assertIn("0001-settings.png", manifest)
        self.assertIn("http://127.0.0.1:8080/settings", manifest)

    def test_route_coverage_matches_reachable_dynamic_page(self):
        summary: dict[str, object] = {
            "pages": [
                {
                    "requested_url":
                        "http://127.0.0.1:8080/contacts/contact-123",
                    "final_url":
                        "http://127.0.0.1:8080/contacts/contact-123",
                }
            ],
            "route_inventory": {
                "loaded": True,
                "browser_candidate_count": 2,
                "routes": [
                    {
                        "rule": "/contacts/<contact_id>",
                        "endpoint": "documents.contact",
                        "dynamic": True,
                        "browser_candidate": True,
                        "path_regex": "^/contacts/[^/]+$",
                    },
                    {
                        "rule": "/settings/mail",
                        "endpoint": "mail_routes.settings",
                        "dynamic": False,
                        "browser_candidate": True,
                        "path_regex": "^/settings/mail$",
                    },
                ],
            },
        }

        update_route_coverage(summary)

        coverage = summary["route_coverage"]
        self.assertEqual(1, coverage["covered_candidates"])
        self.assertEqual(
            [{"rule": "/settings/mail", "endpoint": "mail_routes.settings"}],
            coverage["uncovered_static"],
        )
        self.assertEqual([], coverage["uncovered_dynamic"])


if __name__ == "__main__":
    unittest.main()
