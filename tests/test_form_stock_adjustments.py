import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.form_store import FormStore, StockAdjustmentCommittedError


class ProductStockAdjustmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FormStore(Path(self.temp.name))
        self.product = self.store.save_record("product", {
            "article_number": "A-1", "name": "Widget", "stock": "10",
        }, "alice")

    def tearDown(self):
        self.temp.cleanup()

    def adjust(self, record=None, **overrides):
        record = record or self.store.record("product", self.product["record_id"])
        values = {
            "record_id": record["record_id"], "magnitude": "2", "direction": "add",
            "reason": "delivery received", "actor": "alice",
            "expected_updated_at": record["updated_at"], "request_id": "request_1234567890",
        }
        values.update(overrides)
        return self.store.adjust_product_stock(**values)

    def test_adjustment_updates_stock_and_embedded_audit_atomically(self):
        product, duplicate = self.adjust()
        self.assertFalse(duplicate)
        self.assertEqual("12", product["values"]["stock"])
        self.assertEqual({"before": "10", "after": "12", "actor": "alice", "reason": "delivery received"}, {
            key: product["stock_adjustments"][0][key]
            for key in ("before", "after", "actor", "reason")
        })
        snapshot_path = self.store.history.root / "snapshots" / "forms" / f"{product['record_id']}.json"
        history_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        self.assertNotIn("stock_adjustments", history_snapshot)
        self.assertEqual(1, history_snapshot["stock_adjustment_count"])
        self.assertEqual(product["stock_adjustments"][0], history_snapshot["last_stock_adjustment"])

    def test_exact_retry_is_idempotent_and_reused_id_with_other_input_is_rejected(self):
        first, _ = self.adjust()
        retry, duplicate = self.adjust(record=self.product)
        self.assertTrue(duplicate)
        self.assertEqual("12", retry["values"]["stock"])
        self.assertEqual(1, len(retry["stock_adjustments"]))
        with self.assertRaisesRegex(ValueError, "already used"):
            self.adjust(record=self.product, magnitude="3")

    def test_remove_underflow_and_invalid_inputs_are_rejected(self):
        for magnitude, direction, reason in [("0", "add", "reason"), ("-1", "add", "reason"), ("NaN", "add", "reason"), ("1", "wrong", "reason"), ("1", "remove", " ")]:
            with self.subTest(magnitude=magnitude, direction=direction):
                with self.assertRaises(ValueError):
                    self.adjust(magnitude=magnitude, direction=direction, reason=reason)
        with self.assertRaisesRegex(ValueError, "below zero"):
            self.adjust(magnitude="11", direction="remove")

    def test_legacy_blank_stock_is_zero_and_malformed_stock_is_not_reset(self):
        self._set_stock("")
        product, _ = self.adjust()
        self.assertEqual("2", product["values"]["stock"])
        self._set_stock("NaN")
        current = self.store.record("product", self.product["record_id"])
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.adjust(record=current, request_id="request_abcdefghijklm")
        repaired, _ = self.adjust(record=current, request_id="request_abcdefghijkln", direction="set", magnitude="5")
        self.assertEqual("5", repaired["values"]["stock"])
        self.assertEqual("NaN", repaired["stock_adjustments"][-1]["before"])

    def test_product_can_be_created_without_negative_or_unbounded_stock(self):
        for stock in ("-1", "1e999999999", "1e-999999999"):
            with self.subTest(stock=stock), self.assertRaises(ValueError):
                self.store.save_record("product", {"article_number": stock, "name": "bad", "stock": stock}, "alice")

    def test_history_failure_reports_committed_operation_and_retry_is_safe(self):
        with patch.object(self.store.history, "record", side_effect=OSError("disk unavailable")):
            with self.assertRaises(StockAdjustmentCommittedError):
                self.adjust()
        saved = self.store.record("product", self.product["record_id"])
        self.assertEqual("12", saved["values"]["stock"])
        retried, duplicate = self.adjust(record=self.product)
        self.assertTrue(duplicate)
        self.assertEqual("12", retried["values"]["stock"])

    def test_required_stock_field_is_preserved_during_ordinary_edit(self):
        definition = self.store.definition("product")
        definition["fields"] = [dict(field, required=True) if field["key"] == "stock" else field for field in definition["fields"]]
        self.store.save_definition(definition, "alice")
        edited = self.store.save_record("product", {"article_number": "A-1", "name": "Widget Plus"}, "alice",
                                        self.product["record_id"], expected_updated_at=self.product["updated_at"])
        self.assertEqual("10", edited["values"]["stock"])

    def test_stock_and_audit_survive_product_definition_field_removal(self):
        adjusted, _ = self.adjust()
        definition = self.store.definition("product")
        definition["fields"] = [field for field in definition["fields"] if field["key"] != "stock"]
        self.store.save_definition(definition, "alice")
        edited = self.store.save_record(
            "product", {"article_number": "A-1", "name": "Widget Plus"}, "alice",
            adjusted["record_id"], expected_updated_at=adjusted["updated_at"],
        )
        self.assertEqual("12", edited["values"]["stock"])
        self.assertEqual(adjusted["stock_adjustments"], edited["stock_adjustments"])

    def test_stale_adjustment_and_stale_ordinary_edit_cannot_overwrite_movement(self):
        product, _ = self.adjust()
        with self.assertRaisesRegex(ValueError, "changed since"):
            self.adjust(record=self.product, request_id="request_abcdefghijkl")
        with self.assertRaisesRegex(ValueError, "changed since"):
            self.store.save_record("product", {**product["values"], "stock": "10"}, "alice", product["record_id"], expected_updated_at=self.product["updated_at"])
        edited = self.store.save_record("product", {**product["values"], "name": "Widget Plus"}, "alice", product["record_id"], expected_updated_at=product["updated_at"])
        self.assertEqual("12", edited["values"]["stock"])
        self.assertEqual(product["stock_adjustments"], edited["stock_adjustments"])

    def test_two_concurrent_stale_movements_apply_only_one(self):
        outcomes = []
        barrier = threading.Barrier(2)

        def run(request_id):
            barrier.wait()
            try:
                outcomes.append(self.adjust(record=self.product, request_id=request_id)[0]["values"]["stock"])
            except ValueError as exc:
                outcomes.append(str(exc))

        threads = [threading.Thread(target=run, args=(f"request_{suffix:010d}",)) for suffix in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        fresh = self.store.record("product", self.product["record_id"])
        self.assertEqual("12", fresh["values"]["stock"])
        self.assertEqual(1, len(fresh["stock_adjustments"]))
        self.assertEqual(1, sum(isinstance(value, str) and "changed since" in value for value in outcomes))

    def test_non_product_record_cannot_be_adjusted(self):
        form = self.store.save_record("invoice", {
            "number": "INV-1", "date": "2026-11-18", "customer": "contact-x", "status": "offen",
            "line_items": [{"description": "Work", "quantity": "1", "unit_price": "0", "tax_rate": "0"}],
        }, "alice")
        with self.assertRaisesRegex(ValueError, "unknown product"):
            self.adjust(record={**form, "form_id": "product"})

    def _set_stock(self, value):
        payload = json.loads(self.store.records_path.read_text(encoding="utf-8"))
        record = next(row for row in payload["records"] if row["record_id"] == self.product["record_id"])
        record["values"]["stock"] = value
        self.store.records_path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
