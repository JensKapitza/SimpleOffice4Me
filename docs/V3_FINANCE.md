# Finance 3.0 lifecycle

Finance 3.0 is an additive orchestration layer. The existing invoice implementation remains authoritative for invoice calculation, yearly numbering, PDF generation, ZUGFeRD/XRechnung validation, payments, credit notes and write-offs.

## Document chain

Supported lifecycle kinds are offer, order, delivery_note and invoice.

Allowed successor links are:

- offer -> order
- order -> delivery_note or invoice
- delivery_note -> invoice

A successor stores only a reference to its predecessor. Generic business documents freeze their working payload into a snapshot when they leave draft state. Later conversions start from that snapshot; the source snapshot is not edited.

## Invoice adapter

Invoice lifecycle records reference the existing invoice_id. Creating a lifecycle invoice never allocates an invoice number and never generates a second PDF/XML representation.

When SIMPLEOFFICE_V3_FINANCE_ENABLED is active, the existing invoice form uses finalize_invoice_tracked. The wrapper:

1. creates or reuses the lifecycle invoice record;
2. records a finalizing attempt before invoking the existing finalize_invoice function;
3. lets the existing invoice sequence lock allocate the number;
4. lets the existing PDF/A-3, CII/ZUGFeRD validation and DocumentStore code create the legal artifact;
5. records success with invoice number and document ID;
6. records ordinary failures as failed and returns the invoice to an editable draft lifecycle;
7. intentionally leaves process-level interruptions in finalizing so operators can see and recover them.

If the legacy finalizer reserved a number and then returned the invoice to draft after an ordinary failure, the V3 wrapper restores the original DRAFT number in the editable invoice. The reserved sequence value is retained only in the finalization-attempt audit record and is never reused.

## Recovery

A failed attempt can be returned to draft. A finalizing attempt requires either the stale timeout or an explicit administrator force action. Recovery first checks the legacy invoice: if a legal invoice already exists, the lifecycle is reconciled forward instead of reverting it.

## Payments and corrections

Payments remain separate immutable payment entries in the existing invoice JSON. Finance 3.0 projects invoice_state into lifecycle states final, partial, paid, overdue and written_off.

A final invoice cannot be cancelled through a generic status transition. Full cancellation is projected only when the existing correction-document mechanism has fully credited the invoice. The lifecycle stores the credit_note_id as correction_id while preserving the original invoice reference and PDF/XML.

## Compatibility

With the capability disabled, all existing invoice routes and behavior remain unchanged. No new third-party dependency is required. Existing invoice APIs, PDF names, XML generation and validators remain the source of truth.
