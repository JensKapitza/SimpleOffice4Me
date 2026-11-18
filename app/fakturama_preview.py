"""Read-only preview of the documented Fakturama 2.2 invoice CSV projection."""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


PROFILE = ("simpleoffice-fakturama-invoices", "1", "2.2.0")
MAX_FILE_BYTES = 256 * 1024
MAX_ROWS = 200
MAX_COLUMNS = 40
MAX_CELL_CHARS = 240
REQUIRED_FIELDS = (
    "document_id", "document_number", "document_date", "currency", "total_value",
    "receiver_id", "buyer_name", "buyer_street", "buyer_postal_code", "buyer_city", "buyer_country",
)
OPTIONAL_FIELDS = (
    "origin_contact_id", "origin_address_id", "source_document_id", "invoice_reference_id", "paid_value",
)
COLUMNS = REQUIRED_FIELDS + OPTIONAL_FIELDS
KNOWN_FIELDS = frozenset(COLUMNS)
_DECIMAL = re.compile(r"-?[0-9]{1,12}(?:[.,][0-9]{1,4})?")
_HEADER = re.compile(r"[a-z][a-z0-9_]{0,63}")


class PreviewError(ValueError):
    """A file-level error with a stable message code."""


@dataclass(frozen=True)
class InvoicePreviewRow:
    line: int
    values: dict[str, str]
    amounts: dict[str, Decimal]
    unmapped: tuple[str, ...]
    invalid: tuple[str, ...]
    duplicate: bool = False

    @property
    def status(self) -> str:
        if self.invalid or self.duplicate:
            return "rejected"
        if self.unmapped:
            return "unmapped"
        return "recognized"


@dataclass(frozen=True)
class InvoicePreview:
    rows: tuple[InvoicePreviewRow, ...]
    counts: dict[str, int]


def example_csv() -> str:
    """Generate an empty UTF-8 profile that users can fill with their projection."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(PROFILE)
    writer.writerow(COLUMNS)
    return output.getvalue()


def _parse_row(line: int, headers: list[str], cells: list[str]) -> InvoicePreviewRow:
    if len(cells) != len(headers):
        return InvoicePreviewRow(line, {}, {}, (), ("column_count",))
    values: dict[str, str] = {}
    invalid: list[str] = []
    unmapped: list[str] = []
    for name, cell in zip(headers, cells):
        value = cell.strip()
        if len(cell) > MAX_CELL_CHARS or (value and not value.isprintable()):
            invalid.append(name)
        elif name in KNOWN_FIELDS:
            values[name] = value
        elif value:
            unmapped.append(name)
    unmapped.extend(name for name in REQUIRED_FIELDS if not values.get(name) and name not in invalid)

    issued = values.get("document_date", "")
    if issued:
        try:
            valid_date = date.fromisoformat(issued).isoformat() == issued
        except ValueError:
            valid_date = False
        if not valid_date:
            invalid.append("document_date")
    for name, length in (("currency", 3), ("buyer_country", 2)):
        if values.get(name) and not re.fullmatch(rf"[A-Z]{{{length}}}", values[name]):
            invalid.append(name)

    amounts: dict[str, Decimal] = {}
    for name in ("total_value", "paid_value"):
        value = values.get(name, "")
        if not value:
            continue
        if not _DECIMAL.fullmatch(value):
            invalid.append(name)
            continue
        amounts[name] = Decimal(value.replace(",", "."))
    return InvoicePreviewRow(line, values, amounts, tuple(unmapped), tuple(invalid))


def preview_invoice_csv(payload: bytes) -> InvoicePreview:
    if len(payload) > MAX_FILE_BYTES:
        raise PreviewError("file_size")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PreviewError("encoding") from exc
    if "\x00" in text:
        raise PreviewError("syntax")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=";", strict=True)
    parsed: list[InvoicePreviewRow] = []
    try:
        if tuple(next(reader, ())) != PROFILE:
            raise PreviewError("profile")
        headers = next(reader, [])
        if not headers or len(headers) > MAX_COLUMNS or len(set(headers)) != len(headers) or not all(_HEADER.fullmatch(name) for name in headers):
            raise PreviewError("header")
        if not set(headers) & KNOWN_FIELDS:
            raise PreviewError("header")
        for cells in reader:
            if not cells:
                continue
            if len(parsed) == MAX_ROWS:
                raise PreviewError("row_limit")
            parsed.append(_parse_row(reader.line_num, headers, cells))
    except csv.Error as exc:
        raise PreviewError("syntax") from exc
    if not parsed:
        raise PreviewError("empty")

    ids = Counter(row.values.get("document_id") for row in parsed if row.values.get("document_id"))
    rows = tuple(
        InvoicePreviewRow(row.line, row.values, row.amounts, row.unmapped, row.invalid, ids[row.values.get("document_id")] > 1)
        for row in parsed
    )
    return InvoicePreview(rows, dict(Counter(row.status for row in rows)))
