# Invoice relationships and internal annotations

The invoice detail page can add typed references between **finalized invoices
of the same customer**: follow-up, supplement, advance/final, correction
reference, or project-related. Project-related links require at least one
shared project source. Links are informational only; they do not recalculate,
settle, renumber, rewrite, or regenerate either invoice or PDF. They are shown
from either invoice, may be removed by a user who can manage the customer, and
soft-removal plus creation events remain in the invoice relationship store.
Self-links, duplicate active links, and directed cycles are rejected.
The invoice overview can filter by project or by a linked invoice and includes
project and related invoice numbers in its text search. Only invoices and
relationships accessible to the current user are offered as filter choices.

An optional **reference invoice** may be selected while an invoice is still a
draft. The reference is checked against the customer's accessible, finalized
invoices. Its number is included in the draft preview and, at finalization, in
the PDF and structured invoice note. The reference is then part of that
immutable invoice snapshot.

Internal annotations are stored outside the invoice JSON and customer document.
Each edit appends a version with author and timestamp; edits require the
version currently displayed so concurrent changes fail rather than overwrite.
Removal creates a tombstone and preserves previous versions. Annotations are
not included in PDF, Factur-X/CII, e-mail attachments, or customer exports.

The sidecar SQLite store is under the application's private control directory,
uses a cross-process write lock, and records relation/note audit metadata. Note
text is not copied into the central audit event payload; the sidecar stores
versioned text and a SHA-256 audit digest. Invoice access and same-customer
checks are repeated server-side for every mutation and detail view. If a linked
invoice is unavailable or no longer accessible, the UI shows a generic state
without exposing its identifier or customer metadata.

The relationship list is bounded to 500 historical links per invoice; each
invoice permits up to 200 annotation threads and 50 versions per thread.
