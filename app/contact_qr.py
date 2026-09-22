"""Selective vCard QR export for contacts."""
from __future__ import annotations

from typing import Iterable

from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import createBarcodeDrawing

from .contact_store import ContactStore


MAX_QR_VCARD_BYTES = 2800
QR_SIZE = 320
DEFAULT_QR_FIELDS = ("name", "email", "phone", "company", "website")
QR_FIELD_MAP: dict[str, set[str]] = {
    "name": {"display_name", "name"},
    "nickname": {"nickname"},
    "email": {"email"},
    "phone": {"phone"},
    "birthday": {"birthday"},
    "company": {"company", "department"},
    "title": {"title", "role"},
    "website": {"website"},
    "note": {"note"},
    "addresses": {"addresses"},
    "categories": {"categories", "groups"},
}


def normalize_qr_fields(values: Iterable[str]) -> tuple[str, ...]:
    """Return supported QR field tokens in stable order; name is always present."""
    requested = {str(value).strip() for value in values if str(value).strip()}
    if not requested:
        requested = set(DEFAULT_QR_FIELDS)
    requested.add("name")
    return tuple(key for key in QR_FIELD_MAP if key in requested)


def selected_vcard_fields(values: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for token in normalize_qr_fields(values):
        result.update(QR_FIELD_MAP[token])
    return result


def contact_qr_vcard(
    store: ContactStore,
    contact_id: str,
    actor: str,
    field_tokens: Iterable[str],
) -> str:
    """Build a compact mobile vCard without leaking internal UID/unknown fields."""
    card = store.vcard(
        contact_id,
        actor,
        selected_fields=selected_vcard_fields(field_tokens),
    )
    lines = []
    for line in card.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            continue
        if line.upper().startswith("UID:"):
            continue
        if line.upper() == "VERSION:4.0":
            line = "VERSION:3.0"
        lines.append(line)
    payload = "\r\n".join(lines) + "\r\n"
    if len(payload.encode("utf-8")) > MAX_QR_VCARD_BYTES:
        raise ValueError(
            "Die ausgewählten Kontaktdaten sind für einen QR-Code zu groß. "
            "Bitte große Felder wie Notiz, Adresse oder Tags abwählen."
        )
    return payload


def contact_qr_svg(
    store: ContactStore,
    contact_id: str,
    actor: str,
    field_tokens: Iterable[str],
) -> str:
    """Render a vCard QR code as an SVG using the existing ReportLab dependency."""
    payload = contact_qr_vcard(store, contact_id, actor, field_tokens)
    try:
        drawing = createBarcodeDrawing(
            "QR",
            value=payload,
            barLevel="L",
            width=QR_SIZE,
            height=QR_SIZE,
        )
    except Exception as exc:
        raise ValueError("Der QR-Code konnte aus diesen Kontaktdaten nicht erzeugt werden.") from exc
    svg = renderSVG.drawToString(drawing)
    if isinstance(svg, bytes):
        return svg.decode("utf-8")
    return str(svg)