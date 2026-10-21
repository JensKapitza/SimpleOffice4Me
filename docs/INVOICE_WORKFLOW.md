# Invoice creation workflow

Invoices can be started from **Documents → Invoices → Create invoice** or from
an open task on that task's contact page. The customer selector searches
contacts the signed-in user may manage. A task-started invoice stays scoped to
the task's customer.

The existing invoice editor remains the single creation path. It supports
multiple manually entered or catalog/project/appointment-derived positions,
editing and removal, and moving positions up or down. Quantity units use the
UNECE codes offered in the editor (piece, hour, day, month, metre, kilogram,
litre and kilowatt hour); the server validates these codes before saving.
Net, VAT and gross totals are recalculated on the server using the existing
per-line two-decimal rounding rules.

Saving creates or updates an editable draft without consuming a final invoice
number. Drafts can be reopened from their invoice detail page and have a
watermarked PDF preview. Finalization uses the existing locked number sequence,
validation and immutable-invoice workflow. Starting from a task stores the task
identifier on the invoice; the task's contact page and invoice detail page show
the reciprocal navigation when the user is authorized to access both records.
Creating an invoice never completes the task automatically.

Invoice and task access is checked server-side. The invoice draft editor and
finalization routes retain the application's authenticated mutation/CSRF
protections. The customer, address, line values, VAT rates, unit codes, and
project/appointment sources continue to be validated by the existing invoice
backend.
