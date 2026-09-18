import hashlib
import tempfile
import unittest
from pathlib import Path

from app.finance_store import FinanceStore


class FinanceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = FinanceStore(self.root)
        self.account = self.store.create_account({"kind": "bank", "name": "ING Giro", "institution": "ING", "iban": "DE02120300000000202051"}, "jens")

    def tearDown(self):
        self.temp.cleanup()

    def test_account_discovery_reuses_existing_iban(self):
        found = self.store.account_by_iban("DE02 1203 0000 0000 2020 51", "jens")
        self.assertEqual(self.account["account_id"], found["account_id"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.create_account({"name": "Doppelt", "iban": "DE02120300000000202051"}, "jens")

    def test_same_statement_file_is_idempotent(self):
        digest = hashlib.sha256(b"statement").hexdigest()
        first, created = self.store.create_import_batch(self.account["account_id"], {"source_type": "csv", "source_name": "konto.csv", "file_sha256": digest}, "jens")
        second, created_again = self.store.create_import_batch(self.account["account_id"], {"source_type": "csv", "source_name": "konto-copy.csv", "file_sha256": digest}, "jens")
        self.assertTrue(created); self.assertFalse(created_again); self.assertEqual(first["batch_id"], second["batch_id"])

    def test_bank_transaction_id_is_definitive_but_fingerprint_is_advisory(self):
        base = {"account_id": self.account["account_id"], "booking_date": "2026-09-17", "value_date": "2026-09-17", "amount_cents": -2999, "counterparty_name": "Beispiel GmbH", "counterparty_iban": "DE12500105170648489890", "purpose": "Einkauf", "bank_transaction_id": "bank-4711"}
        first, state = self.store.import_bank_transaction(base, "jens")
        same, state2 = self.store.import_bank_transaction(base, "jens")
        self.assertEqual("created", state); self.assertEqual("existing", state2); self.assertEqual(first["transaction_id"], same["transaction_id"])
        no_bank_id = dict(base); no_bank_id["bank_transaction_id"] = ""
        second, state3 = self.store.import_bank_transaction(no_bank_id, "jens")
        third, state4 = self.store.import_bank_transaction(no_bank_id, "jens")
        self.assertEqual("possible_duplicate", state3); self.assertEqual("possible_duplicate", state4)
        self.assertNotEqual(second["transaction_id"], third["transaction_id"])
        self.assertEqual(3, len(self.store.bank_transactions(self.account["account_id"], "jens")))

    def test_entry_source_is_idempotent_and_owner_scoped(self):
        values = {"account_id": self.account["account_id"], "direction": "expense", "amount_cents": 18500, "booking_date": "2026-09-17", "description": "Kita", "category": "Kinderbetreuung", "source_type": "bank_transaction", "source_id": "tx-1", "tax_year": 2026}
        row = self.store.create_entry(values, "jens")
        with self.assertRaisesRegex(ValueError, "already linked"): self.store.create_entry(values, "jens")
        self.assertEqual([row["entry_id"]], [x["entry_id"] for x in self.store.entries("jens")]); self.assertEqual([], self.store.entries("melanie"))

    def test_tags_and_allocations_do_not_duplicate_the_original_amount(self):
        entry = self.store.create_entry({"direction": "expense", "amount_cents": 14000, "booking_date": "2026-09-17", "description": "Strom", "category": "Energie"}, "jens")
        self.assertEqual(["Haushalt", "Ruby"], self.store.set_tags(entry["entry_id"], ["Haushalt", "Ruby", "haushalt"], "jens"))
        allocations = self.store.replace_allocations(entry["entry_id"], [{"target_type": "group", "target_id": "parents", "label": "Eltern", "share_basis_points": 7000}, {"target_type": "person", "target_id": "ruby", "label": "Ruby", "share_basis_points": 1500}, {"target_type": "person", "target_id": "amy", "label": "Amy", "share_basis_points": 1500}], "jens")
        self.assertEqual(10000, sum(x["share_basis_points"] for x in allocations))
        with self.assertRaisesRegex(ValueError, "outside the supported range"):
            self.store.replace_allocations(entry["entry_id"], [{"target_type": "person", "target_id": "ruby", "share_basis_points": 10001}], "jens")

    def test_audit_records_mutations(self):
        entry = self.store.create_entry({"direction": "income", "amount_cents": 10000, "booking_date": "2026-09-17", "description": "Rechnung"}, "jens")
        self.assertTrue(any(row["entity_id"] == entry["entry_id"] and row["event"] == "entry_created" for row in self.store.audit("jens")))


    def test_recurring_obligation_and_tax_year_workflow(self):
        obligation = self.store.create_obligation({
            "name": "Kita", "kind": "childcare", "direction": "expense",
            "amount_cents": 18500, "currency": "EUR", "recurrence_unit": "monthly",
            "starts_on": "2026-01-01", "due_day": 3, "counterparty_name": "Kita"
        }, "jens")
        self.assertEqual("childcare", obligation["kind"])
        self.assertEqual(1, len(self.store.obligations("jens")))
        self.assertEqual("collecting", self.store.set_tax_year_status(2026, "collecting", "jens")["status"])
        submitted = self.store.set_tax_year_status(2026, "submitted", "jens", submitted_on="2027-05-01")
        self.assertEqual("2027-05-01", submitted["submitted_on"])

    def test_bank_connection_never_accepts_pin_or_tan(self):
        connection = self.store.save_bank_connection({
            "provider": "fints", "institution": "ING", "login_id": "user-4711"
        }, "jens")
        self.assertEqual("user-4711", connection["login_id"])\n        self.assertNotIn("pin", connection)\n        self.assertNotIn("tan", connection)
        with self.assertRaises(ValueError):
            self.store.save_bank_connection({
                "provider": "fints", "institution": "ING", "login_id": "user-4712", "pin": "12345"
            }, "jens")

    def test_confirmed_transaction_match_is_idempotent_and_owner_scoped(self):
        tx, _ = self.store.import_bank_transaction({
            "account_id": self.account["account_id"], "booking_date": "2026-09-18",
            "amount_cents": -18500, "currency": "EUR", "purpose": "Kita September"
        }, "jens")
        first = self.store.confirm_transaction_match(tx["transaction_id"], "contract", "kita-contract", "jens", score=90)
        second = self.store.confirm_transaction_match(tx["transaction_id"], "contract", "kita-contract", "jens", score=10)
        self.assertEqual(first["match_id"], second["match_id"])
        self.assertEqual(1, len(self.store.transaction_matches(tx["transaction_id"], "jens")))
        with self.assertRaises(ValueError):
            self.store.confirm_transaction_match(tx["transaction_id"], "contract", "other", "melanie")


if __name__ == "__main__":
    unittest.main()
