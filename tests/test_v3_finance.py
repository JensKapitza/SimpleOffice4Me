from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.business_document_generation import _invoice_store_path, invoice
from app.document_store import atomic_json_write
from app.v3_finance import (
    FinanceLifecycleStore,
    finalize_invoice_tracked,
)


class V3FinanceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = FinanceLifecycleStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def _write_invoice(
        self,
        invoice_id: str = "invoice-1",
        *,
        status: str = "draft",
        number: str = "DRAFT-2026-0001",
        contact_id: str = "contact-1",
        payments=None,
        credit_notes=None,
    ):
        row = {
            "invoice_id": invoice_id,
            "invoice_number": number,
            "contact_id": contact_id,
            "status": status,
            "issue_date": "2026-09-29",
            "due_date": "2099-12-31",
            "currency": "EUR",
            "totals": {"gross": "100.00"},
            "payments": list(payments or []),
            "credit_notes": list(credit_notes or []),
            "history": [],
        }
        atomic_json_write(_invoice_store_path(self.root, invoice_id), row)
        return row

    def test_offer_to_order_preserves_frozen_predecessor_snapshot(self):
        offer = self.store.create(
            "offer",
            "alice",
            contact_id="c1",
            title="Angebot Server",
            working={"amount": "100.00", "description": "Original"},
        )
        with self.assertRaises(ValueError):
            self.store.convert(offer.lifecycle_id, "order", "alice")

        offer = self.store.transition(offer.lifecycle_id, "final", "alice")
        self.store.transition(offer.lifecycle_id, "sent", "alice")
        offer = self.store.transition(offer.lifecycle_id, "accepted", "alice")
        order = self.store.convert(offer.lifecycle_id, "order", "alice")

        self.assertEqual(offer.lifecycle_id, order.predecessor_id)
        self.assertEqual("Original", order.working["description"])
        self.assertEqual("Original", offer.snapshot["description"])
        with self.assertRaises(ValueError):
            self.store.update_draft(
                offer.lifecycle_id,
                "alice",
                working={"description": "Changed"},
            )

    def test_invalid_state_transition_is_rejected(self):
        row = self.store.create("delivery_note", "alice", title="L1")
        with self.assertRaises(ValueError):
            self.store.transition(row.lifecycle_id, "delivered", "alice")

    def test_invoice_successor_requires_matching_existing_draft(self):
        order = self.store.create(
            "order",
            "alice",
            contact_id="contact-1",
            title="Auftrag",
            working={"amount": "100.00"},
        )
        order = self.store.transition(order.lifecycle_id, "confirmed", "alice")

        with self.assertRaises(ValueError):
            self.store.convert(order.lifecycle_id, "invoice", "alice")

        self._write_invoice("invoice-1", contact_id="contact-1")
        successor = self.store.convert(
            order.lifecycle_id,
            "invoice",
            "alice",
            external_id="invoice-1",
        )
        self.assertEqual("invoice", successor.kind)
        self.assertEqual("invoice-1", successor.external_id)
        self.assertEqual(order.lifecycle_id, successor.predecessor_id)

        self._write_invoice("invoice-2", contact_id="other")
        with self.assertRaises(ValueError):
            self.store.convert(
                order.lifecycle_id,
                "invoice",
                "alice",
                external_id="invoice-2",
            )

    def test_parallel_lifecycle_finalization_is_rejected(self):
        row = self._write_invoice()
        lifecycle, first = self.store.begin_finalization(row, "alice")
        self.assertEqual("finalizing", lifecycle.status)
        self.assertEqual("finalizing", first.state)
        with self.assertRaisesRegex(ValueError, "already finalizing"):
            self.store.begin_finalization(row, "bob")

    def test_stale_or_forced_finalization_can_recover_to_draft(self):
        row = self._write_invoice()
        lifecycle, attempt = self.store.begin_finalization(row, "alice")
        recovered = self.store.recover_finalization(
            row,
            "admin",
            force=True,
        )
        self.assertEqual("draft", recovered.status)
        self.assertEqual(
            "recovered",
            self.store.attempt(attempt.attempt_id).state,
        )

    def test_failed_tracked_finalization_restores_draft_number(self):
        self._write_invoice()

        def failing_finalizer(root, invoice_id, actor):
            current = invoice(root, invoice_id)
            current["invoice_number"] = "2026-0001"
            current["status"] = "draft"
            atomic_json_write(_invoice_store_path(root, invoice_id), current)
            raise ValueError("render failed")

        with mock.patch(
            "app.business_documents.finalize_invoice",
            side_effect=failing_finalizer,
        ):
            with self.assertRaisesRegex(ValueError, "render failed"):
                finalize_invoice_tracked(self.root, "invoice-1", "alice")

        current = invoice(self.root, "invoice-1")
        self.assertEqual("DRAFT-2026-0001", current["invoice_number"])
        lifecycle = self.store.by_external("invoice", "invoice-1")
        self.assertEqual("failed", lifecycle.status)
        attempt = self.store.latest_attempt("invoice-1")
        self.assertEqual("failed", attempt.state)
        self.assertEqual("2026-0001", attempt.reserved_number)

    def test_process_interruption_remains_visible_until_recovery(self):
        self._write_invoice()
        with mock.patch(
            "app.business_documents.finalize_invoice",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                finalize_invoice_tracked(self.root, "invoice-1", "alice")

        lifecycle = self.store.by_external("invoice", "invoice-1")
        self.assertEqual("finalizing", lifecycle.status)
        self.assertEqual(
            "finalizing",
            self.store.latest_attempt("invoice-1").state,
        )
        recovered = self.store.recover_finalization(
            invoice(self.root, "invoice-1"),
            "admin",
            force=True,
        )
        self.assertEqual("draft", recovered.status)

    def test_payment_projection_tracks_partial_and_paid(self):
        row = self._write_invoice(
            status="open",
            number="2026-0001",
            payments=[{"amount": "40.00"}],
        )
        partial = self.store.sync_invoice(row, "alice")
        self.assertEqual("partial", partial.status)

        row["payments"] = [{"amount": "100.00"}]
        paid = self.store.sync_invoice(row, "alice")
        self.assertEqual("paid", paid.status)

    def test_full_credit_note_cancels_lifecycle_but_keeps_invoice_reference(self):
        row = self._write_invoice(
            status="open",
            number="2026-0001",
            credit_notes=[
                {
                    "credit_note_id": "credit-1",
                    "gross": "100.00",
                }
            ],
        )
        cancelled = self.store.sync_invoice(row, "alice")
        self.assertEqual("cancelled", cancelled.status)
        self.assertEqual("credit-1", cancelled.correction_id)
        self.assertEqual("invoice-1", cancelled.external_id)
        with self.assertRaises(ValueError):
            self.store.transition(
                cancelled.lifecycle_id,
                "draft",
                "alice",
            )

    def test_final_invoice_cannot_be_cancelled_without_correction_projection(self):
        row = self._write_invoice(status="open", number="2026-0001")
        lifecycle = self.store.sync_invoice(row, "alice")
        self.assertEqual("final", lifecycle.status)
        with self.assertRaises(ValueError):
            self.store.transition(
                lifecycle.lifecycle_id,
                "cancelled",
                "alice",
            )


if __name__ == "__main__":
    unittest.main()
