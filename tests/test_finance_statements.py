import tempfile
import unittest
from pathlib import Path

from app.finance_statements import FinanceStatementImporter, parse_statement_bytes
from app.finance_store import FinanceStore


class FinanceStatementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = FinanceStore(self.root)
        self.account = self.store.create_account({
            "name": "Girokonto", "kind": "bank", "institution": "Testbank",
            "iban": "DE89370400440532013000", "currency": "EUR",
        }, "jens")
        self.importer = FinanceStatementImporter(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_csv_preview_and_commit_are_idempotent(self):
        raw = (
            "IBAN: DE89370400440532013000\n"
            "Buchungstag;Valuta;Auftraggeber/Empfänger;IBAN;Verwendungszweck;Betrag;Währung;EndToEndId\n"
            "01.09.2026;01.09.2026;Stadtwerke;DE12500105170648489890;Strom September;-85,40;EUR;E2E-1\n"
            "02.09.2026;02.09.2026;Arbeitgeber;;Gehalt;2500,00;EUR;E2E-2\n"
        ).encode()
        preview = self.importer.preview(raw, "konto.csv", "jens")
        self.assertEqual({"new": 2, "existing": 0, "possible_duplicate": 0, "unmatched_account": 0}, preview["summary"])
        result = self.importer.commit(raw, "konto.csv", "jens")
        self.assertEqual((2, 0), (result["created"], result["possible_duplicates"]))
        again = self.importer.commit(raw, "konto.csv", "jens")
        self.assertEqual("already_imported", again["status"])
        self.assertEqual(2, len(self.store.bank_transactions(self.account["account_id"], "jens")))

    def test_csv_requires_explicit_account_when_statement_has_no_iban(self):
        raw = b"Buchungsdatum;Betrag;Verwendungszweck\n2026-09-01;-10,00;Test\n"
        preview = self.importer.preview(raw, "export.csv", "jens")
        self.assertEqual(1, preview["summary"]["unmatched_account"])
        with self.assertRaisesRegex(ValueError, "keinem vorhandenen Konto"):
            self.importer.commit(raw, "export.csv", "jens")
        result = self.importer.commit(raw, "export.csv", "jens", account_id=self.account["account_id"])
        self.assertEqual(1, result["created"])

    def test_camt053_extracts_account_refs_and_transaction(self):
        raw = b'''<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt>
<Id>STATEMENT-1</Id><Acct><Id><IBAN>DE89370400440532013000</IBAN></Id><Ccy>EUR</Ccy></Acct>
<Ntry><Amt Ccy="EUR">12.34</Amt><CdtDbtInd>DBIT</CdtDbtInd><BookgDt><Dt>2026-09-03</Dt></BookgDt><ValDt><Dt>2026-09-03</Dt></ValDt><AcctSvcrRef>BANK-1</AcctSvcrRef>
<NtryDtls><TxDtls><Refs><EndToEndId>E2E-CAMT</EndToEndId><MndtId>MANDATE-1</MndtId></Refs><RltdPties><Cdtr><Nm>Versicherung AG</Nm></Cdtr><CdtrAcct><Id><IBAN>DE12500105170648489890</IBAN></Id></CdtrAcct></RltdPties><RmtInf><Ustrd>Police 4711</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
</Stmt></BkToCstmrStmt></Document>'''
        statements = parse_statement_bytes(raw, "statement.xml")
        self.assertEqual(1, len(statements))
        statement = statements[0]
        self.assertEqual("DE89370400440532013000", statement.account_iban)
        row = statement.transactions[0]
        self.assertEqual(-1234, row["amount_cents"])
        self.assertEqual("Versicherung AG", row["counterparty_name"])
        self.assertEqual("E2E-CAMT", row["end_to_end_id"])
        self.assertEqual("MANDATE-1", row["mandate_id"])

    def test_camt052_report_is_supported(self):
        raw = b'''<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.052.001.08"><BkToCstmrAcctRpt><Rpt><Id>R1</Id><Acct><Id><IBAN>DE89370400440532013000</IBAN></Id><Ccy>EUR</Ccy></Acct><Ntry><Amt Ccy="EUR">1.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><BookgDt><Dt>2026-09-04</Dt></BookgDt></Ntry></Rpt></BkToCstmrAcctRpt></Document>'''
        statement = parse_statement_bytes(raw, "camt052.xml")[0]
        self.assertEqual(100, statement.transactions[0]["amount_cents"])

    def test_mt940_parses_debit_and_structured_purpose(self):
        raw = (
            ":20:STARTUMSE\n:25:DE89370400440532013000\n:28C:00001/001\n"
            ":60F:C260831EUR1000,00\n"
            ":61:2609010901D85,40NDDTNONREF//BANKREF1\n"
            ":86:105?00LASTSCHRIFT?20STROM SEPTEMBER?32STADTWERKE\n"
            ":62F:C260901EUR914,60\n"
        ).encode("ascii")
        statement = parse_statement_bytes(raw, "konto.sta")[0]
        self.assertEqual("mt940", statement.format)
        self.assertEqual("DE89370400440532013000", statement.account_iban)
        row = statement.transactions[0]
        self.assertEqual(-8540, row["amount_cents"])
        self.assertEqual("BANKREF1", row["bank_transaction_id"])
        self.assertIn("STROM SEPTEMBER", row["purpose"])
        self.assertEqual("STADTWERKE", row["counterparty_name"])

    def test_preview_marks_existing_bank_id_without_writing(self):
        raw = (
            "Buchungsdatum;Betrag;Verwendungszweck;Transaktions-ID\n"
            "2026-09-01;-10,00;Test;BANK-EXISTING\n"
        ).encode()
        first = self.importer.commit(raw, "first.csv", "jens", account_id=self.account["account_id"])
        self.assertEqual(1, first["created"])
        same_rows_other_file = raw + b"\n"
        preview = self.importer.preview(same_rows_other_file, "second.csv", "jens", account_id=self.account["account_id"])
        self.assertEqual(1, preview["summary"]["existing"])
        self.assertEqual(1, len(self.store.bank_transactions(self.account["account_id"], "jens")))


if __name__ == "__main__":
    unittest.main()
