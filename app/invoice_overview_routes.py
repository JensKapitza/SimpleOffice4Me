"""Filter and export the invoices manageable by the current user."""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import date

from flask import Response, g, render_template, request

from .auth import login_required
from .business_document_generation import _actor, _root
from .csv_export import spreadsheet_safe_cell
from .invoice_collection import suggest_collection
from .invoice_relations import invoice_overview_data


def _date_bound(value: str) -> date | None:
    if not value:
        return None
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Expected YYYY-MM-DD")
    return parsed


def _csv_amount(value: object) -> str:
    text = str(value)
    # Keep actual amounts numeric. Treat malformed legacy values as text.
    return text if re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", text) else spreadsheet_safe_cell(value)


def _invoice_csv(rows: list[dict]) -> Response:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(("invoice_number", "customer", "issue_date", "due_date", "status", "currency", "gross", "outstanding"))
    for row in rows:
        state = row["payment_state"]
        buyer = row.get("buyer")
        buyer_name = buyer.get("name") if isinstance(buyer, dict) else None
        name = buyer_name or row["contact"].get("fields", {}).get("display_name", "")
        text = (row.get("invoice_number"), name, row.get("issue_date"), row.get("due_date"),
                state["status"], row.get("currency"))
        writer.writerow((
            *(spreadsheet_safe_cell(value) for value in text),
            _csv_amount(row.get("totals", {}).get("gross")), _csv_amount(state["outstanding"]),
        ))
    return Response("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="invoices.csv"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })


@login_required
def invoice_overview():
    filters = {name: request.args.get(name, "").strip() for name in (
        "q", "status", "project_id", "related_invoice_id", "date_from", "date_to",
    )}
    export = request.args.get("format") == "csv"
    english = g.language == "en"
    date_error = ""
    try:
        date_from = _date_bound(filters["date_from"])
        date_to = _date_bound(filters["date_to"])
        if date_from and date_to and date_from > date_to:
            date_error = "The start date must precede or equal the end date." if english else "Das Startdatum darf nicht nach dem Enddatum liegen."
    except ValueError:
        date_error = "Enter a valid invoice date." if english else "Bitte ein gültiges Rechnungsdatum eingeben."

    rows, project_options, related_options, relation_error = invoice_overview_data(
        _root(), _actor(), filters["q"], "", filters["project_id"], filters["related_invoice_id"],
        None if date_error else date_from, None if date_error else date_to,
    )
    if date_error:
        rows = []
    counts = Counter(row["payment_state"]["status"] for row in rows)
    stats = {"total": len(rows), **{status: counts[status] for status in ("open", "overdue", "paid", "written_off")}}
    selected_status = filters["status"]
    if selected_status:
        rows = [row for row in rows if row["payment_state"]["status"] == selected_status]
    if export and not date_error:
        if relation_error and filters["related_invoice_id"]:
            return ("Related-invoice filtering is temporarily unavailable." if english
                    else "Der Filter für verknüpfte Rechnungen ist momentan nicht verfügbar."), 503
        return _invoice_csv(rows)

    active = {name: value for name, value in filters.items() if value}
    return render_template(
        "documents/invoice_overview.html", rows=rows, stats=stats, filters=filters,
        active_filters=active, other_filters={key: value for key, value in active.items() if key != "status"},
        advanced_open=any(filters[key] for key in ("project_id", "related_invoice_id", "date_from", "date_to")),
        date_error=date_error, project_options=project_options, related_options=related_options,
        relation_error=relation_error, collection_suggestions=suggest_collection(rows),
    ), 400 if date_error else 200
