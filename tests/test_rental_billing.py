import hashlib
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from app.rental_billing import RentalBillingStore, allocate_money
from app.rentals import _peer_allows_rental_send


class RentalBillingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RentalBillingStore(self.temp.name)
        self.store._object = lambda object_id: {"object_id": object_id, "name": object_id, "identifier": "", "location": "", "type": "Wohnung"}
        self.store._contact = lambda contact_id: {"contact_id": contact_id, "fields": {"display_name": f"Mieter {contact_id}", "email": ""}}
        group = self.store.create_group("Musterhaus", "", "admin")
        self.group_id = group["group_id"]
        self.store.add_group_unit(self.group_id, "o1", "EG", "admin")
        self.store.add_group_unit(self.group_id, "o2", "OG", "admin")

    def tearDown(self): self.temp.cleanup()

    def settlement(self):
        return self.store.create_settlement("Nebenkosten 2026", 2026, "2026-01-01", "2026-12-31", "admin", group_id=self.group_id)

    def test_cent_allocation_preserves_total(self):
        result = allocate_money(Decimal("10.00"), {"a": Decimal("1"), "b": Decimal("2")})
        self.assertEqual(Decimal("10.00"), sum(result.values()))
        self.assertEqual(Decimal("3.33"), result["a"])
        self.assertEqual(Decimal("6.67"), result["b"])

    def test_person_days_and_vacancy_are_traceable(self):
        self.store.add_tenancy("o1", "c1", "2026-01-01", "", "admin")
        self.store.add_tenancy("o2", "c2", "2026-07-01", "", "admin")
        self.store.add_metric("o1", "persons", "2", "2026-01-01", "", "admin", source_note="2 Personen gemeldet")
        self.store.add_metric("o2", "persons", "1", "2026-07-01", "", "admin", source_note="1 Person gemeldet")
        settlement = self.settlement()
        self.store.add_cost(settlement["settlement_id"], "Wasser", "Jahresrechnung", "1200", "2026-01-01", "2026-12-31", "person_days", "admin", source_note="Handeingabe aus Rechnung")
        result = self.store.calculate(settlement["settlement_id"])
        self.assertEqual("730", result["costs"][0]["weights"]["o1"])
        self.assertEqual("184", result["costs"][0]["weights"]["o2"])
        self.assertEqual("0.00", result["vacancy"]["o1"])
        self.assertEqual("0.00", result["vacancy"]["o2"])
        self.assertEqual(Decimal("1200.00"), sum(Decimal(v["costs"]) for v in result["tenants"].values()))

    def test_cost_period_is_prorated_to_settlement_period(self):
        self.store.add_tenancy("o1", "c1", "2026-01-01", "", "admin")
        settlement = self.store.create_settlement("Q1", 2026, "2026-01-01", "2026-03-31", "admin", object_id="o1")
        self.store.add_cost(settlement["settlement_id"], "Versicherung", "Okt bis März", "600", "2025-10-01", "2026-03-31", "equal", "admin", source_note="Beispiel")
        result = self.store.calculate(settlement["settlement_id"])
        self.assertEqual("296.70", result["costs"][0]["effective_amount"])
        self.assertEqual("296.70", result["tenants"]["c1"]["costs"])

    def test_manual_sources_require_explanation_and_metrics_cannot_overlap(self):
        with self.assertRaises(ValueError): self.store.add_metric("o1", "area", "60", "2026-01-01", "", "admin")
        self.store.add_metric("o1", "area", "60", "2026-01-01", "2026-06-30", "admin", source_note="Flächenberechnung")
        with self.assertRaises(ValueError): self.store.add_metric("o1", "area", "61", "2026-06-01", "", "admin", source_note="Überlappung")

    def test_approval_writes_hashable_artifacts_and_locks_version(self):
        self.store.add_tenancy("o1", "c1", "2026-01-01", "", "admin")
        settlement = self.settlement()
        self.store.add_cost(settlement["settlement_id"], "Grundsteuer", "Bescheid", "100", "2026-01-01", "2026-12-31", "direct", "admin", direct_object_id="o1", source_note="Handeingabe Test")
        approved = self.store.approve(settlement["settlement_id"], "admin")
        directory = self.store.approval_directory(settlement["settlement_id"])
        expected = hashlib.sha256((directory / "snapshot.json").read_bytes()).hexdigest()
        self.assertEqual(expected, approved["settlement"]["snapshot_sha256"])
        self.assertTrue((directory / "Freigabe-und-Berechnungsnachweis.pdf").is_file())
        self.assertTrue((directory / "Vermieter-Abrechnungsblatt.pdf").is_file())
        self.assertTrue((directory / "Mieterpaket-c1.zip").is_file())
        self.assertTrue((directory / "approval-manifest.json").is_file())
        manifest_before = hashlib.sha256((directory / "approval-manifest.json").read_bytes()).hexdigest()
        with self.assertRaises(ValueError): self.store.approve(settlement["settlement_id"], "admin")
        manifest_after = hashlib.sha256((directory / "approval-manifest.json").read_bytes()).hexdigest()
        self.assertEqual(manifest_before, manifest_after)
        with self.assertRaises(ValueError): self.store.add_cost(settlement["settlement_id"], "Müll", "nachträglich", "10", "2026-01-01", "2026-12-31", "equal", "admin", source_note="darf nicht mehr gehen")

    def test_failed_approval_leaves_no_published_artifacts_and_can_retry(self):
        self.store.add_tenancy("o1", "c1", "2026-01-01", "", "admin")
        settlement = self.settlement()
        settlement_id = settlement["settlement_id"]
        self.store.add_cost(settlement_id, "Grundsteuer", "Bescheid", "100", "2026-01-01", "2026-12-31", "direct", "admin", direct_object_id="o1", source_note="Handeingabe Test")
        directory = self.store.approval_directory(settlement_id)
        with patch.object(self.store, "_render_approval_pdf", side_effect=RuntimeError("render failed")):
            with self.assertRaises(RuntimeError):
                self.store.approve(settlement_id, "admin")
        self.assertEqual("draft", self.store.settlement(settlement_id)["status"])
        self.assertFalse(directory.exists())
        self.assertFalse(list(directory.parent.glob(f".{directory.name}.staging-*")))
        approved = self.store.approve(settlement_id, "admin")
        self.assertEqual("approved", approved["settlement"]["status"])
        self.assertTrue((directory / "approval-manifest.json").is_file())

    def test_rental_federation_policy_fails_closed(self):
        self.assertFalse(_peer_allows_rental_send(None))
        self.assertFalse(_peer_allows_rental_send({}))
        self.assertFalse(_peer_allows_rental_send({"documents": {}}))
        self.assertFalse(_peer_allows_rental_send({"documents": {"send": False}}))
        self.assertFalse(_peer_allows_rental_send({"documents": {"send": True}, "rentals": {"send": False}}))
        self.assertTrue(_peer_allows_rental_send({"documents": {"send": True}}))
        self.assertTrue(_peer_allows_rental_send({"documents": {"send": True}, "rentals": {"send": True}}))

    def test_tenant_document_manifest_redacts_internal_path(self):
        rows = RentalBillingStore._tenant_document_manifest({
            "doc-1": {"name": "rechnung.pdf", "path": "intern/kunden/2026/rechnung.pdf", "sha256": "a" * 64, "size": 1234}
        })
        self.assertEqual(1, len(rows))
        self.assertEqual("doc-1", rows[0]["document_id"])
        self.assertEqual("rechnung.pdf", rows[0]["name"])
        self.assertNotIn("path", rows[0])
        self.assertNotIn("intern", str(rows[0]))

    def test_tenants_outside_period_are_not_in_statement(self):
        self.store.add_tenancy("o1", "old", "2025-01-01", "2025-12-31", "admin")
        self.store.add_tenancy("o2", "current", "2026-01-01", "", "admin")
        settlement = self.settlement()
        self.store.add_cost(settlement["settlement_id"], "Müll", "Jahreskosten", "100", "2026-01-01", "2026-12-31", "direct", "admin", direct_object_id="o2", source_note="Test")
        result = self.store.calculate(settlement["settlement_id"])
        self.assertIn("current", result["tenants"]); self.assertNotIn("old", result["tenants"])

    def test_tenant_package_is_hard_blocked_before_approval(self):
        settlement = self.settlement()
        with self.assertRaises(ValueError): self.store.tenant_package(settlement["settlement_id"], "c1")


if __name__ == "__main__": unittest.main()
