import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from werkzeug.datastructures import MultiDict

from app.business_documents import _build_invoice_lines, _cii_xml, _invoice_totals
from app.object_store import ObjectStore


class InvoiceCatalogTest(unittest.TestCase):
    def _values(self, name: str, **extra):
        values = {"name": name, "type": "Leistung", "status": "active", "description": name}
        values.update(extra)
        return values

    def test_sequence_ids_are_monotonic_and_display_width_grows(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ObjectStore(temp)
            first = store.create(self._values("Eins"), "tester")
            self.assertEqual(1, first["sequence_id"])
            self.assertEqual("1", first["display_id"])
            for number in range(2, 21):
                store.create(self._values(f"Objekt {number}"), "tester")
            objects = store.objects()
            self.assertEqual("01", objects[0]["display_id"])
            self.assertEqual("20", objects[-1]["display_id"])
            self.assertEqual("002", ObjectStore.format_sequence(2, 3))
            self.assertEqual("111", ObjectStore.format_sequence(111, 3))

    def test_old_and_zero_padded_sequence_references_resolve_same_object(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ObjectStore(temp)
            first = store.create(self._values("Eins", use_in_invoice="1", invoice_description="Eins", net_price="1", vat_rate="19"), "tester")
            for number in range(2, 111):
                store.create(self._values(f"Objekt {number}"), "tester")
            self.assertEqual("001", store.object(first["object_id"])["display_id"])
            for reference in ("1", "01", "001", "0001", "#001"):
                resolved = store.object(reference)
                self.assertEqual(first["object_id"], resolved["object_id"])
                self.assertEqual(1, resolved["sequence_id"])
            candidates = store.invoice_candidates("0001")
            self.assertEqual(first["object_id"], candidates[0]["object_id"])
            self.assertEqual("1", candidates[0]["original_id"])
            self.assertEqual("001", candidates[0]["id"])

    def test_sequence_is_not_reused_after_object_file_disappears(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ObjectStore(root)
            first = store.create(self._values("Eins"), "tester")
            (store.directory / f"{first['object_id']}.json").unlink()
            second = store.create(self._values("Zwei"), "tester")
            self.assertEqual(2, second["sequence_id"])

    def test_category_defaults_fill_missing_invoice_values(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ObjectStore(temp)
            category = store.create(self._values(
                "Dienstleistung",
                is_invoice_category="1",
                default_vat_rate="19",
                default_net_price="100",
                default_price_group="B2B",
            ), "tester")
            item = store.create(self._values(
                "Beratung",
                use_in_invoice="1",
                invoice_description="Beratungsstunde",
                category_object_id=category["object_id"],
                vat_rate="",
                net_price="",
                gross_price="",
            ), "tester")
            effective = store.invoice_effective(item)
            self.assertEqual("19.00", effective["vat_rate"])
            self.assertEqual("100.00", effective["net_price"])
            self.assertEqual("119.00", effective["gross_price"])
            self.assertEqual("B2B", effective["price_group"])

    def test_invoice_lines_snapshot_catalog_and_calculate_server_side(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ObjectStore(temp)
            item = store.create(self._values(
                "Beratung",
                use_in_invoice="1",
                invoice_description="Technische Beratung",
                net_price="100",
                vat_rate="19",
            ), "tester")
            form = MultiDict([
                ("line_object_id", item["object_id"]),
                ("line_description", ""),
                ("line_quantity", "2"),
                ("line_net_price", "100"),
                ("line_vat_rate", "19"),
                ("line_category", "Support"),
                ("line_project_id", "project-1"),
                ("line_source_type", "time_entry"),
                ("line_source_id", "entry-1"),
            ])
            lines = _build_invoice_lines(Path(temp), form)
            self.assertEqual("200.00", lines[0]["net_total"])
            self.assertEqual("38.00", lines[0]["tax_total"])
            self.assertEqual("238.00", lines[0]["gross_total"])
            self.assertEqual(item["object_id"], lines[0]["object_id"])
            self.assertEqual("Support", lines[0]["category"])
            self.assertEqual("project-1", lines[0]["project_id"])
            self.assertEqual("time_entry", lines[0]["project_source_type"])
            self.assertEqual("entry-1", lines[0]["project_source_id"])
            totals = _invoice_totals(lines)
            self.assertEqual("200.00", totals["net"])
            self.assertEqual("38.00", totals["tax"])
            self.assertEqual("238.00", totals["gross"])

    def test_cii_xml_contains_invoice_number_currency_and_totals(self):
        row = {
            "invoice_number": "2026-0001",
            "issue_date": "2026-08-26",
            "service_date": "2026-08-26",
            "due_date": "2026-09-09",
            "currency": "EUR",
            "payment_terms": "14 Tage netto",
            "seller": {"name": "Muster GmbH", "street": "Musterstr. 1", "postal": "47000", "city": "Duisburg", "country": "DE", "vat_id": "DE123456789", "iban": "DE001234", "bic": "TESTDEFF", "bank": "Bank"},
            "buyer": {"name": "Kunde GmbH", "label": "Kunde GmbH\nKundenweg 2\n47000 Duisburg", "street": "Kundenweg 2", "postal": "47000", "city": "Duisburg", "country": "DE", "vat_id": ""},
            "lines": [{"line_id": 1, "description": "Leistung", "object_name": "Leistung", "quantity": "1", "net_unit_price": "100.00", "vat_rate": "19.00", "net_total": "100.00", "tax_total": "19.00", "gross_total": "119.00"}],
            "totals": {"net": "100.00", "tax": "19.00", "gross": "119.00", "due": "119.00", "vat_groups": {"19.00": {"basis": "100.00", "tax": "19.00"}}},
        }
        xml = _cii_xml(row)
        root = ET.fromstring(xml)
        values = [((element.tag.rsplit("}", 1)[-1]), (element.text or "").strip()) for element in root.iter()]
        self.assertIn(("ID", "2026-0001"), values)
        self.assertIn(("InvoiceCurrencyCode", "EUR"), values)
        self.assertIn(("GrandTotalAmount", "119.00"), values)
        self.assertIn(("DuePayableAmount", "119.00"), values)


if __name__ == "__main__":
    unittest.main()
