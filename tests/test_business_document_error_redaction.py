from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.business_documents import _pdfa3_convert_detailed, _validate_hybrid, finalize_invoice
from app.contact_store import ContactStore
from app.document_store import CONTROL_DIR


class BusinessDocumentErrorRedactionTests(unittest.TestCase):
    def _write_invoice(self, root: Path, invoice_id: str, *, contact_id: str = "") -> Path:
        directory = root / CONTROL_DIR / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{invoice_id}.json"
        row = {
            "invoice_id": invoice_id,
            "contact_id": contact_id,
            "status": "draft",
            "invoice_number": "RE-2026-0001",
            "template_id": "template-1",
            "buyer": {},
            "seller": {},
            "history": [],
            "zugferd": {},
            "totals": {"gross": "10.00"},
            "currency": "EUR",
        }
        path.write_text(json.dumps(row), encoding="utf-8")
        return path

    def test_finalization_error_is_redacted_from_record_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            invoice_id = "customer-secret-invoice-id"
            path = self._write_invoice(root, invoice_id)
            secret_error = "renderer failed for /srv/customer/private/credentials.txt"
            with (
                mock.patch("app.business_documents._validate_project_sources"),
                mock.patch("app.business_documents.business_settings", return_value={}),
                mock.patch("app.business_documents._business_template", side_effect=RuntimeError(secret_error)),
                self.assertLogs("app.business_documents", level="ERROR") as captured,
            ):
                with self.assertRaisesRegex(RuntimeError, "Serverprotokoll prüfen"):
                    finalize_invoice(root, invoice_id, "tester")

            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("draft", persisted["status"])
            self.assertEqual(
                "Rechnungsfinalisierung fehlgeschlagen. Bitte Serverprotokoll prüfen.",
                persisted["finalization_error"],
            )
            self.assertNotIn(secret_error, json.dumps(persisted))
            self.assertEqual("RuntimeError", persisted["history"][-1]["error_type"])
            log_output = "\n".join(captured.output)
            self.assertNotIn(secret_error, log_output)
            self.assertNotIn(invoice_id, log_output)

    def test_zugferd_error_is_redacted_and_pdf_only_invoice_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Max Muster"}, "tester")
            invoice_id = "invoice-zugferd-error"
            path = self._write_invoice(root, invoice_id, contact_id=contact["contact_id"])
            row = json.loads(path.read_text(encoding="utf-8"))
            row["buyer"] = {
                "street": "Musterstraße 1", "postal": "10115", "city": "Berlin", "country": "DE",
            }
            path.write_text(json.dumps(row), encoding="utf-8")
            secret_error = "private token /srv/customer/secret.txt"
            with (
                mock.patch("app.business_documents._validate_project_sources"),
                mock.patch("app.business_documents.business_settings", return_value={}),
                mock.patch("app.business_documents._business_template", return_value={"template_id": "template-1"}),
                mock.patch("app.business_documents._render_invoice_with_template", return_value=b"visual"),
                mock.patch("app.business_documents._pdfa3_convert_detailed", side_effect=RuntimeError(secret_error)),
                mock.patch("app.business_documents._store_generated_pdf", return_value={"document_id": "document-1"}),
                mock.patch("app.business_documents.DocumentStore"),
            ):
                finalized, _document = finalize_invoice(root, invoice_id, "tester")

            self.assertEqual("open", finalized["status"])
            self.assertEqual("pdf_only", finalized["zugferd"]["status"])
            self.assertNotIn(secret_error, json.dumps(finalized))
            self.assertIn("Einzelheiten stehen im Serverprotokoll", finalized["zugferd"]["error"])

    def test_validator_process_output_is_not_logged_or_returned(self) -> None:
        secret_output = "customer invoice XML at /srv/customer/private/token.txt"
        completed = mock.Mock(returncode=1, stdout=secret_output, stderr=secret_output)
        with mock.patch.dict("os.environ", {"SIMPLEOFFICE_ZUGFERD_VALIDATOR": "custom-validator"}), \
                mock.patch("app.business_document_generation.shutil.which", return_value="/usr/bin/verapdf"), \
                mock.patch("app.business_document_generation.subprocess.run", return_value=completed), \
                self.assertLogs("app.business_documents", level="ERROR") as captured:
            result = _validate_hybrid(b"pdf-bytes", b"<Invoice />")

        self.assertFalse(result["validated"])
        self.assertEqual("", result["pdfa_output"])
        self.assertEqual("", result["xml_output"])
        self.assertNotIn(secret_output, json.dumps(result))
        self.assertNotIn(secret_output, "\n".join(captured.output))

    def test_pdfa_conversion_does_not_log_or_return_process_output(self) -> None:
        secret_output = "ghostscript read /srv/customer/private/token.txt"
        completed = mock.Mock(returncode=1, stdout=secret_output, stderr=secret_output)
        with mock.patch("app.business_document_generation.shutil.which", return_value="/usr/bin/gs"), \
                mock.patch.object(Path, "is_file", autospec=True,
                                  side_effect=lambda path: path.name == "srgb.icc"), \
                mock.patch("app.business_document_generation.subprocess.run", return_value=completed), \
                self.assertLogs("app.business_documents", level="ERROR") as captured:
            _pdf, details = _pdfa3_convert_detailed(b"pdf-bytes")

        self.assertEqual("ghostscript_pdfa_failed", details["status"])
        self.assertEqual("", details["stdout"])
        self.assertEqual("", details["stderr"])
        self.assertNotIn(secret_output, json.dumps(details))
        self.assertNotIn(secret_output, "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
