"""Capture inbox UI wiring: duplicate linking, upload resume, entries (767)."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CaptureUiWiringTests(unittest.TestCase):
    def test_duplicate_adopt_offers_link_to_existing_object(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("Trotzdem zuordnen", script)
        self.assertIn("linkDuplicate", script)
        self.assertIn("Foto übernommen", script)
        self.assertIn("entry.assign_url", script)

    def test_multi_upload_continues_after_single_failure(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("failures.push", script)
        self.assertIn("von ${list.length} Fotos gespeichert", script)
        self.assertIn("renderEntry(payload.entry)", script)

    def test_library_links_to_photo_inbox(self) -> None:
        library = (ROOT / "templates/library/index.html").read_text(encoding="utf-8")
        self.assertIn("inventory.capture_inbox", library)
        self.assertIn("Fotoeingang", library)
        inventory = (ROOT / "templates/inventory/index.html").read_text(encoding="utf-8")
        self.assertIn("inventory.capture_inbox", inventory)

    def test_photo_role_select_posts_to_role_url(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("Fotorolle", script)
        self.assertIn("ROLE_LABELS", script)
        self.assertIn("entry.role_url", script)
        self.assertIn("Buchrücken", script)

    def test_batch_review_assigns_group_to_existing_book(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("Vorhandene Buch-ID", script)
        self.assertIn("Ganze Gruppe vorhandenem Buch zuordnen", script)
        self.assertIn("payload.assign_url", script)
        self.assertIn("Fotos, ", script)

    def test_batch_review_shows_duplicates_with_group_assign(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("combined.duplicates", script)
        self.assertIn("Gruppe diesem Buch zuordnen", script)
        self.assertIn("assignIdInput", script)
        self.assertIn("assignExistingBtn", script)

    def test_batch_cards_offer_resume_for_existing_groups(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("Fortsetzen", script)
        self.assertIn("currentBatch = batchId", script)

    def test_single_capture_sends_meta_consent_when_checked(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertGreaterEqual(script.count("consent_meta', '1'"), 3)

    def test_batch_review_has_click_only_marketplace_search(self) -> None:
        inbox = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        script = (ROOT / "static/js/capture_marketplace.js").read_text(encoding="utf-8")
        for snippet in (
            "nur auf Klick suchen, kein Automatismus",
            "dataset.marketplaceUrl",
            "'Amazon suchen'",
            "'eBay suchen'",
            "'Preis übernehmen'",
            "'Angebot ansehen'",
            "Bereits eingetragene Preiswerte ersetzen",
            "data-marketplace-overwrite",
            "SimpleOfficeCaptureMarketplace",
            "renderMarketplaceSection",
        ):
            self.assertIn(snippet, script)
        for snippet in (
            "'Preis (EUR-Richtwert)', 'market_price'",
            "'Währung', 'currency'",
            "'Preisquelle', 'price_source'",
        ):
            self.assertIn(snippet, inbox)
        self.assertEqual(
            1, script.count("searchMarketplace(provider)"))
        self.assertIn(
            "addEventListener('click', () => searchMarketplace(provider))",
            script)
        self.assertIn("data.append('market_price'", inbox)
        self.assertIn("data.append('currency'", inbox)
        self.assertIn("data.append('price_source'", inbox)
        self.assertIn(
            "window.SimpleOfficeCaptureMarketplace.renderMarketplaceSection",
            inbox)

    def test_marketplace_review_link_uses_shared_allowlist(self) -> None:
        script = (ROOT / "static/js/capture_marketplace.js").read_text(encoding="utf-8")
        self.assertIn("SimpleOfficeInventoryNavigation", script)
        self.assertIn("safeMarketplaceUrl(row.url)", script)
        self.assertIn("noopener noreferrer", script)
        self.assertNotIn("window.open", script)
        inbox = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertNotIn("window.open", inbox)

    def test_capture_template_wires_marketplace_url(self) -> None:
        template = (ROOT / "templates/inventory/capture.html").read_text(encoding="utf-8")
        self.assertIn('data-marketplace-url="{{ marketplace_url }}"', template)
        navigation = template.index("js/inventory_navigation.js")
        marketplace = template.index("js/capture_marketplace.js")
        inbox = template.index("js/capture_inbox.js")
        self.assertLess(navigation, marketplace)
        self.assertLess(marketplace, inbox)

    def test_priceless_result_offers_link_only(self) -> None:
        script = (ROOT / "static/js/capture_marketplace.js").read_text(encoding="utf-8")
        self.assertIn("const hasPrice = isValidPrice(row.market_price)", script)
        self.assertIn("take.disabled = !hasPrice", script)
        self.assertIn("keinen gültigen Preis", script)
        self.assertIn("if (cleanText(input.value) && !overwrite)", script)
        self.assertIn("Eigene Preiswerte behalten", script)

    def test_marketplace_price_take_is_fail_closed(self) -> None:
        script = (ROOT / "static/js/capture_marketplace.js").read_text(encoding="utf-8")
        self.assertIn("const isValidPrice = (value) => {", script)
        self.assertIn("Number.isFinite(amount) && amount >= 0", script)
        self.assertIn("if (!isValidPrice(row.market_price)) {", script)
        self.assertIn("normalizeCurrency(row.currency)", script)
        self.assertIn(".toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3)", script)

    def test_marketplace_price_source_is_provider_bound(self) -> None:
        script = (ROOT / "static/js/capture_marketplace.js").read_text(encoding="utf-8")
        self.assertIn("if (provider !== 'amazon' && provider !== 'ebay') {", script)
        self.assertNotIn("row.price_source", script)
        self.assertIn("if (row.provider !== provider) return;", script)
        self.assertIn(
            "take.addEventListener('click', () => applyMarketplacePrice(provider, row))",
            script)
        self.assertIn("'price_source', providerName]", script)

    def test_peer_stock_section_renders_only_with_route(self) -> None:
        script = (ROOT / "static/js/capture_peer_stock.js").read_text(encoding="utf-8")
        self.assertIn("if (!cleanText(dataset.peerStockUrl)) return;", script)

    def test_batch_review_has_click_only_peer_inventory_check(self) -> None:
        inbox = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        script = (ROOT / "static/js/capture_peer_stock.js").read_text(encoding="utf-8")
        self.assertIn(
            "window.SimpleOfficeCapturePeerStock.renderPeerInventorySection",
            inbox)
        for snippet in (
            "renderPeerInventorySection",
            "Freundesbestand (Inventarabgleich): nur auf Klick suchen",
            "dataset.peerInventoryUrl",
            "'Freundesbestand prüfen'",
            "match.display_name",
            "keine Aussage",
        ):
            self.assertIn(snippet, script)
        self.assertIn("if (!cleanText(dataset.peerInventoryUrl)) return;", script)
        self.assertEqual(
            1, script.count("addEventListener('click', () => askPeerInventory())"))

    def test_peer_inventory_renders_local_index_freshness(self) -> None:
        script = (ROOT / "static/js/capture_peer_stock.js").read_text(encoding="utf-8")
        for snippet in (
            "last_index_at",
            "Stand vom",
            "toLocaleString",
        ):
            self.assertIn(snippet, script)
        # No invented TTL, no peer polling, no extra personal data.
        self.assertNotIn("stale", script)
        self.assertNotIn("MAX_AGE", script)
        self.assertNotIn("maxAge", script)
        self.assertEqual(2, script.count("fetch("))

    def test_peer_stock_section_has_no_freshness_timestamp(self) -> None:
        script = (ROOT / "static/js/capture_peer_stock.js").read_text(encoding="utf-8")
        stock_start = script.index("const renderPeerStockSection")
        inventory_start = script.index("const renderPeerInventorySection")
        self.assertLess(stock_start, inventory_start)
        stock_section = script[stock_start:inventory_start]
        self.assertNotIn("last_index_at", stock_section)
        self.assertNotIn("Stand vom", stock_section)

    def test_capture_template_wires_peer_inventory_url(self) -> None:
        template = (ROOT / "templates/inventory/capture.html").read_text(encoding="utf-8")
        self.assertIn('data-peer-inventory-url="{{ peer_inventory_url }}"', template)

    def test_batch_review_has_click_only_peer_stock_check(self) -> None:
        inbox = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        script = (ROOT / "static/js/capture_peer_stock.js").read_text(encoding="utf-8")
        self.assertIn(
            "window.SimpleOfficeCapturePeerStock.renderPeerStockSection",
            inbox)
        for snippet in (
            "SimpleOfficeCapturePeerStock",
            "renderPeerStockSection",
            "nur auf Klick suchen, keine Auto-Abfrage",
            "dataset.peerStockUrl",
            "'Geteilte Dokumente suchen'",
            "Geteilte Dokumente: nur auf Klick suchen",
            "Titelabfragen werden nicht unterstützt",
            "Dokumenthinweis",
            "kein Besitznachweis",
            "keine Aussage",
        ):
            self.assertIn(snippet, script)
        self.assertEqual(1, script.count("askPeerStock()"))
        self.assertIn(
            "addEventListener('click', () => askPeerStock())",
            script)
        self.assertIn("data.append('identifier', identifier)", script)

    def test_capture_template_wires_peer_stock_url(self) -> None:
        template = (ROOT / "templates/inventory/capture.html").read_text(encoding="utf-8")
        self.assertIn('data-peer-stock-url="{{ peer_stock_url }}"', template)
        marketplace = template.index("js/capture_marketplace.js")
        peer_stock = template.index("js/capture_peer_stock.js")
        inbox = template.index("js/capture_inbox.js")
        self.assertLess(marketplace, peer_stock)
        self.assertLess(peer_stock, inbox)

    def test_no_ml_price_invention_in_capture_analysis(self) -> None:
        source = (ROOT / "app/inventory_capture.py").read_text(encoding="utf-8")
        head = source.index("def analyze_photo(")
        tail = source.index("def store_upload(")
        self.assertNotIn("market_price", source[head:tail])
        self.assertNotIn("price_source", source[head:tail])

    def test_ml_status_uses_shared_formatter_with_text_assignment(self) -> None:
        template = (ROOT / "templates/inventory/capture.html").read_text(encoding="utf-8")
        helper_pos = template.index("js/capture_ml_status.js")
        inbox_pos = template.index("js/capture_inbox.js")
        self.assertLess(helper_pos, inbox_pos)
        inbox = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("SimpleOfficeCaptureMl.formatMlStatus(payload)", inbox)
        self.assertIn("mlLine.textContent = window.SimpleOfficeCaptureMl", inbox)
        self.assertNotIn("mlLine.innerHTML", inbox)


class CaptureMlStatusNodeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_ml_status_lines_for_meta_and_ollama(self) -> None:
        result = subprocess.run(
            [shutil.which("node"), "--test",
             str(ROOT / "tests" / "capture_ml_status.test.cjs")],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
