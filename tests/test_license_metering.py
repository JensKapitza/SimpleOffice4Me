import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.license_master_store import MasterLicenseStore
from app.license_metering import LicenseStore, feature_for_endpoint


class LicenseMeteringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "documents"
        self.root.mkdir()
        self.store = LicenseStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_endpoint_features_are_bounded_to_billable_areas(self):
        self.assertEqual("documents", feature_for_endpoint("documents.dashboard"))
        self.assertEqual("calendar", feature_for_endpoint("caldav.calendar_home"))
        self.assertEqual("contacts", feature_for_endpoint("carddav.addressbook"))
        self.assertEqual("sync", feature_for_endpoint("federation_http.capabilities"))
        self.assertEqual("projects", feature_for_endpoint("personnel.index"))
        self.assertEqual("", feature_for_endpoint("auth.login"))

    def test_usage_counts_active_users_and_feature_requests(self):
        self.store.record_usage(1, "documents")
        self.store.record_usage(1, "documents")
        self.store.record_usage(2, "documents")
        self.store.record_usage(2, "calendar")
        overview = self.store.overview()
        self.assertEqual(2, overview["active_users"])
        self.assertEqual({"users": 2, "requests": 3}, overview["usage"]["documents"])
        self.assertEqual({"users": 1, "requests": 1}, overview["usage"]["calendar"])

    def test_finalized_month_is_immutable_and_uses_distinct_feature_users(self):
        self.store.set_prices({"user": 100, "documents": 25, "calendar": 50})
        old_month = "2020-01"
        with self.store._db() as db:
            db.executemany(
                "INSERT INTO license_usage(month,user_id,feature,request_count,last_used_at) VALUES(?,?,?,?,?)",
                [
                    (old_month, 1, "documents", 8, 1),
                    (old_month, 2, "documents", 3, 1),
                    (old_month, 2, "calendar", 4, 1),
                ],
            )
        with patch("app.license_metering.installation_id", return_value="00000000-0000-4000-8000-000000000001"):
            first = self.store.finalize_month(old_month)
            second = self.store.finalize_month(old_month)
        self.assertEqual(2, first["user_count"])
        self.assertEqual(300, first["total_cents"])
        self.assertEqual(2, first["usage"]["documents"]["users"])
        self.assertEqual(1, first["usage"]["calendar"]["users"])
        self.assertEqual(first["report_hash"], second["report_hash"])

    def test_blocked_state_does_not_disable_store_and_invoice_remains_visible(self):
        state = self.store.set_state("blocked", reason="billing", message="Rechnung offen")
        self.assertTrue(state["bad_client"])
        invoice = self.store.save_invoice({
            "invoice_id": "INV-1", "service": "license", "month": "2026-08",
            "amount_cents": 1299, "currency": "EUR", "due_date": "2026-09-15",
            "status": "open", "document_url": "/documents/invoice-1",
        })
        self.assertEqual("open", invoice["status"])
        self.assertEqual(1, len(self.store.invoices(open_only=True)))
        self.store.record_usage(1, "documents")
        self.assertEqual(1, self.store.overview()["active_users"])


class MasterLicenseStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "documents"
        self.root.mkdir()
        self.store = MasterLicenseStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _report(report_hash="a" * 64):
        return {
            "installation_id": "00000000-0000-4000-8000-000000000001",
            "month": "2026-08", "user_count": 2,
            "usage": {"documents": {"users": 2, "requests": 10}},
            "prices_cents": {"user": 100}, "total_cents": 200,
            "report_hash": report_hash,
        }

    def test_same_month_report_is_idempotent_but_conflicting_hash_is_rejected(self):
        first = self.store.save_report(self._report())
        second = self.store.save_report(self._report())
        self.assertEqual(first["report_hash"], second["report_hash"])
        with self.assertRaises(ValueError):
            self.store.save_report(self._report("b" * 64))

    def test_master_can_mark_client_bad_without_deleting_usage(self):
        report = self.store.save_report(self._report())
        state = self.store.set_client_state(report["installation_id"], "blocked", "invoice", "Bitte zahlen")
        self.assertEqual("blocked", state["state"])
        reports = self.store.reports()
        self.assertEqual(1, len(reports))
        self.assertEqual("blocked", reports[0]["state"]["state"])


if __name__ == "__main__":
    unittest.main()
