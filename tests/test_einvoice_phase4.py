"""Phase 4 (batch 1): finalization robustness T03/T10/T19.

T03: 20 parallel finalizations consume 20 unique invoice numbers.
T10: an error during finalization leaves no inconsistent document.
T19: a repeated call after a timeout creates no duplicate invoice.
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from itertools import count
from pathlib import Path
from unittest import mock

from werkzeug.datastructures import MultiDict

from app.business_documents import finalize_invoice, invoice, save_invoice_draft
from app.contact_store import ContactStore

SELLER = {
    "seller_name": "Seller", "seller_street": "Seller Street 2",
    "seller_postal": "10117", "seller_city": "Berlin", "currency": "EUR",
}


def _form() -> MultiDict:
    return MultiDict([
        ("address_mode", "manual"),
        ("billing_street", "Buyer Street 1"), ("billing_postal", "10115"),
        ("billing_city", "Berlin"), ("billing_country", "DE"),
        ("recipient_type", "private"), ("recipient_contact", "Customer"),
        ("line_description", "Consulting"), ("line_quantity", "1"),
        ("line_net_price", "10"), ("line_vat_rate", "19"),
    ])


def _draft(root: Path, contact_id: str) -> dict:
    with mock.patch("app.business_documents.business_settings", return_value=dict(SELLER)), \
            mock.patch("app.business_template_dispatch.active_template",
                       return_value={"template_id": "test"}):
        return save_invoice_draft(root, contact_id, _form(), "tester")


def _finalize_mocks(shared=None):
    """Mock the slow/fragile pipeline, keep numbering/persistence real."""
    shared = shared if shared is not None else {"ids": count(1), "lock": threading.Lock()}

    def _store(root, contact_id, subject, pdf, actor, kind, template_id, **kwargs):
        with shared["lock"]:
            number = next(shared["ids"])
        return {"document_id": f"stored-pdf-{number}"}

    return (
        mock.patch("app.business_documents._validate_project_sources"),
        mock.patch("app.business_documents._business_template",
                   return_value={"template_id": "test"}),
        mock.patch("app.business_documents._render_invoice_with_template",
                   return_value=b"visual"),
        mock.patch("app.business_documents._pdfa3_convert_detailed",
                   return_value=(b"pdfa", {"status": "pdfa3_created"})),
        mock.patch("app.business_documents._cii_xml", return_value=b"<Invoice />"),
        mock.patch("app.business_documents.embed_invoice_xml", return_value=b"hybrid"),
        mock.patch("app.business_documents._validate_hybrid",
                   return_value={"validated": True, "details": []}),
        mock.patch("app.business_documents._store_generated_pdf", side_effect=_store),
        mock.patch("app.business_documents.DocumentStore"),
    )


class FinalizationRobustnessTests(unittest.TestCase):
    def test_parallel_finalizations_consume_unique_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Customer"}, "tester")
            drafts = [_draft(root, contact["contact_id"]) for _ in range(20)]
            self.assertEqual(20, len({row["invoice_id"] for row in drafts}))
            results: dict[str, object] = {}
            failures: list[BaseException] = []
            barrier = threading.Barrier(20)
            # Patches live in the main thread: patch/unpatch is not
            # thread-safe, but attribute lookup by workers is.
            patches = _finalize_mocks()

            def _finalize(invoice_id: str) -> None:
                barrier.wait(timeout=30)
                try:
                    finalized, _ = finalize_invoice(root, invoice_id, "tester")
                    results[invoice_id] = finalized["invoice_number"]
                except BaseException as exc:  # noqa: BLE001 - collected, asserted below
                    failures.append(exc)

            threads = [threading.Thread(target=_finalize, args=(row["invoice_id"],))
                       for row in drafts]
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                    patches[5], patches[6], patches[7], patches[8]:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=120)
                    self.assertFalse(thread.is_alive(), "finalization thread hung")
            self.assertEqual([], failures)
            numbers = list(results.values())
            self.assertEqual(20, len(numbers))
            self.assertEqual(20, len(set(numbers)), f"duplicate numbers: {numbers}")
            for row in drafts:
                reread = invoice(root, row["invoice_id"])
                self.assertEqual("open", reread["status"])
                self.assertEqual(results[row["invoice_id"]], reread["invoice_number"])

    def test_failed_finalization_leaves_consistent_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Customer"}, "tester")
            row = _draft(root, contact["contact_id"])
            mocks = _finalize_mocks()
            with mocks[0], mocks[1], \
                    mock.patch("app.business_documents._render_invoice_with_template",
                               side_effect=RuntimeError("template exploded")), \
                    mocks[3], mocks[4], mocks[5], mocks[6], mocks[7], mocks[8]:
                with self.assertRaisesRegex(RuntimeError, "template exploded"):
                    finalize_invoice(root, row["invoice_id"], "tester")
            reread = invoice(root, row["invoice_id"])
            self.assertEqual("draft", reread["status"])
            self.assertIn("finalization_error", reread)
            self.assertNotIn("document_id", reread)
            kinds = [event.get("type") for event in reread.get("history", [])]
            self.assertIn("finalization_failed", kinds)
            self.assertNotIn("issued", kinds)

    def test_recall_after_timeout_keeps_single_invoice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Customer"}, "tester")
            row = _draft(root, contact["contact_id"])
            mocks = _finalize_mocks()
            with mocks[0], mocks[1], \
                    mock.patch("app.business_documents._render_invoice_with_template",
                               side_effect=[TimeoutError("timed out"), b"visual"]), \
                    mocks[3], mocks[4], mocks[5], mocks[6], mocks[7], mocks[8]:
                with self.assertRaises(TimeoutError):
                    finalize_invoice(root, row["invoice_id"], "tester")
                finalized, _ = finalize_invoice(root, row["invoice_id"], "tester")
            reread = invoice(root, row["invoice_id"])
            self.assertEqual("open", reread["status"])
            self.assertEqual(finalized["invoice_number"], reread["invoice_number"])
            issued = [event for event in reread.get("history", [])
                      if event.get("type") == "issued"]
            self.assertEqual(1, len(issued))
            assigned = [event for event in reread.get("history", [])
                        if event.get("type") == "number_assigned"]
            # Both attempts reference the same consumed number: no duplicate.
            self.assertTrue(assigned)
            for event in assigned:
                self.assertEqual(reread["invoice_number"], event["invoice_number"])


if __name__ == "__main__":
    unittest.main()
