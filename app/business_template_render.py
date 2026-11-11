"""ReportLab renderer for visual business-document templates."""
from __future__ import annotations

import html
import io
import math
import re
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph, Table, TableStyle

from .business_template_editor import visual_template, visual_template_asset_path
from .business_template_macros import rich_text_runs

ALIGNMENT = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT, "justify": TA_JUSTIFY}
FONT_VARIANTS = {
    "Helvetica": {
        (False, False): "Helvetica",
        (True, False): "Helvetica-Bold",
        (False, True): "Helvetica-Oblique",
        (True, True): "Helvetica-BoldOblique",
    },
    "Times-Roman": {
        (False, False): "Times-Roman",
        (True, False): "Times-Bold",
        (False, True): "Times-Italic",
        (True, True): "Times-BoldItalic",
    },
    "Courier": {
        (False, False): "Courier",
        (True, False): "Courier-Bold",
        (False, True): "Courier-Oblique",
        (True, True): "Courier-BoldOblique",
    },
}


def _currency(row: dict[str, Any]) -> str:
    return str(row.get("currency") or "EUR")


def invoice_template_context(row: dict[str, Any], layout: dict[str, Any]) -> dict[str, str]:
    seller = row.get("seller") or {}
    buyer = row.get("buyer") or {}
    free_text = layout.get("free_text") or {}
    seller_address = " ".join(
        str(seller.get(key, "")).strip()
        for key in ("street", "postal", "city")
        if str(seller.get(key, "")).strip()
    )
    return {
        "firma.name": str(seller.get("name", "")),
        "firma.anschrift": seller_address,
        "firma.ansprechpartner": str(seller.get("contact", "")),
        "firma.email": str(seller.get("email", "")),
        "firma.iban": str(seller.get("iban", "")),
        "firma.blz": str(seller.get("blz", "")),
        "firma.bic": str(seller.get("bic", "")),
        "firma.bank": str(seller.get("bank", "")),
        "firma.steuer_nr": str(seller.get("tax_number", "")),
        "firma.ust_idnr": str(seller.get("vat_id", "")),
        "rechnung.nummer": str(row.get("invoice_number", "")),
        "rechnung.datum": str(row.get("issue_date", "")),
        "rechnung.zahlungsziel": str(row.get("due_date", "")),
        "kunde.name": str(buyer.get("name") or buyer.get("contact_name") or ""),
        "kunde.anschrift": str(buyer.get("label", "")),
        "datum.heute": date.today().isoformat(),
        "freitext1": str(free_text.get("freitext1", "")),
        "freitext2": str(free_text.get("freitext2", "")),
        "freitext3": str(free_text.get("freitext3", "")),
    }


def business_template_context(
    business: dict[str, Any],
    layout: dict[str, Any],
    *,
    recipient: str = "",
    subject: str = "",
) -> dict[str, str]:
    free_text = layout.get("free_text") or {}
    seller_address = " ".join(
        str(business.get(key, "")).strip()
        for key in ("seller_street", "seller_postal", "seller_city")
        if str(business.get(key, "")).strip()
    )
    return {
        "firma.name": str(business.get("seller_name", "")),
        "firma.anschrift": seller_address,
        "firma.ansprechpartner": "",
        "firma.email": str(business.get("seller_email", "")),
        "firma.iban": str(business.get("seller_iban", "")),
        "firma.blz": str(business.get("seller_blz", "")),
        "firma.bic": str(business.get("seller_bic", "")),
        "firma.bank": str(business.get("seller_bank", "")),
        "firma.steuer_nr": str(business.get("seller_tax_number", "")),
        "firma.ust_idnr": str(business.get("seller_vat_id", "")),
        "rechnung.nummer": "",
        "rechnung.datum": date.today().isoformat(),
        "rechnung.zahlungsziel": str(business.get("payment_terms", "")),
        "kunde.name": recipient.splitlines()[0] if recipient else "",
        "kunde.anschrift": recipient,
        "datum.heute": date.today().isoformat(),
        "freitext1": subject or str(free_text.get("freitext1", "")),
        "freitext2": str(free_text.get("freitext2", "")),
        "freitext3": str(free_text.get("freitext3", "")),
    }


def _font_name(style: dict[str, Any]) -> str:
    family = str(style.get("font_family", "Helvetica"))
    variants = FONT_VARIANTS.get(family, FONT_VARIANTS["Helvetica"])
    return variants[(bool(style.get("bold")), bool(style.get("italic")))]


def _rich_markup(text: str, values: dict[str, Any]) -> tuple[str, str | None]:
    runs = rich_text_runs(text, values)
    fragments: list[str] = []
    paragraph_align = next((run.get("align") for run in runs if run.get("align")), None)
    for run in runs:
        fragment = html.escape(str(run.get("text", ""))).replace("\n", "<br/>")
        if run.get("underline"):
            fragment = f"<u>{fragment}</u>"
        if run.get("italic"):
            fragment = f"<i>{fragment}</i>"
        if run.get("bold"):
            fragment = f"<b>{fragment}</b>"
        attributes = []
        if run.get("font_size"):
            attributes.append(f'size="{float(run["font_size"]):g}"')
        if run.get("color"):
            attributes.append(f'color="{run["color"]}"')
        if attributes:
            fragment = f"<font {' '.join(attributes)}>{fragment}</font>"
        fragments.append(fragment)
    return "".join(fragments), paragraph_align


def _rect(element: dict[str, Any]) -> tuple[float, float, float, float]:
    x = float(element["x_mm"]) * mm
    width = float(element["width_mm"]) * mm
    height = float(element["height_mm"]) * mm
    top = A4[1] - float(element["y_mm"]) * mm
    return x, top - height, width, height


def _region_enabled(layout: dict[str, Any], region: str) -> bool:
    if region == "content":
        return True
    return bool((layout.get("regions") or {}).get(region, {}).get("enabled", False))


def _draw_text(c: canvas.Canvas, element: dict[str, Any], values: dict[str, Any]) -> None:
    x, y, width, height = _rect(element)
    style = element.get("style") or {}
    markup, inline_align = _rich_markup(str(element.get("text", "")), values)
    paragraph_style = ParagraphStyle(
        f"visual-{element.get('id', 'text')}",
        fontName=_font_name(style),
        fontSize=float(style.get("font_size", 10)),
        leading=float(style.get("font_size", 10)) * 1.22,
        textColor=colors.HexColor(str(style.get("color", "#111827"))),
        alignment=ALIGNMENT.get(str(inline_align or style.get("align", "left")), TA_LEFT),
        spaceAfter=0,
        spaceBefore=0,
    )
    paragraph = Paragraph(markup or " ", paragraph_style)
    _wrapped_width, wrapped_height = paragraph.wrap(width, height)
    paragraph.drawOn(c, x, y + height - wrapped_height)


def _draw_shape(c: canvas.Canvas, element: dict[str, Any]) -> None:
    x, y, width, height = _rect(element)
    style = element.get("style") or {}
    c.saveState()
    c.setStrokeColor(colors.HexColor(str(style.get("color", "#111827"))))
    if element.get("type") == "line":
        c.setLineWidth(max(0.25, min(height, 2.5)))
        c.line(x, y + height / 2, x + width, y + height / 2)
    else:
        c.rect(x, y, width, height, stroke=1, fill=0)
    c.restoreState()

def _draw_image(c: canvas.Canvas, root, template_id: str, element: dict[str, Any]) -> None:
    x, y, width, height = _rect(element)
    path = visual_template_asset_path(root, template_id, str(element.get("asset", "")))
    if not path.is_file():
        raise ValueError("template image asset is missing")
    c.drawImage(
        ImageReader(str(path)), x, y, width=width, height=height,
        preserveAspectRatio=True, anchor="c", mask="auto",
    )



def _static_elements(layout: dict[str, Any], *, first_page: bool) -> Iterable[dict[str, Any]]:
    for element in layout.get("elements", []):
        if not element.get("visible", True) or not _region_enabled(layout, str(element.get("region", "content"))):
            continue
        if element.get("type") in {"invoice_lines", "invoice_totals", "body"}:
            continue
        region = str(element.get("region", "content"))
        if not first_page and region == "content":
            continue
        yield element


def _draw_static(c: canvas.Canvas, root, template_id: str, layout: dict[str, Any], values: dict[str, Any], *, first_page: bool) -> None:
    for element in _static_elements(layout, first_page=first_page):
        if element.get("type") in {"text", "token"}:
            _draw_text(c, element, values)
        elif element.get("type") in {"line", "rect"}:
            _draw_shape(c, element)
        elif element.get("type") == "image":
            _draw_image(c, root, template_id, element)


def _invoice_table(lines: list[dict[str, Any]], row: dict[str, Any], width: float) -> Table:
    currency = _currency(row)
    data: list[list[Any]] = [["Pos.", "Bezeichnung", "Menge", "Einzelpreis", "MwSt.", "Gesamt"]]
    for item in lines:
        description = html.escape(str(item.get("description", "")))
        if item.get("category"):
            description = f"<b>{html.escape(str(item['category']))}</b><br/>{description}"
        data.append([
            str(item.get("line_id", "")),
            Paragraph(description or " ", ParagraphStyle("visual-line", fontName="Helvetica", fontSize=7.5, leading=9)),
            str(item.get("quantity", "")),
            f"{item.get('net_unit_price', '')} {currency}",
            f"{item.get('vat_rate', '')} %",
            f"{item.get('net_total', '')} {currency}",
        ])
    proportions = (0.07, 0.42, 0.10, 0.15, 0.10, 0.16)
    table = Table(data, colWidths=[width * part for part in proportions], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.2),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.2),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#b9c2cf")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    return table


def _split_invoice_lines(
    lines: list[dict[str, Any]],
    row: dict[str, Any],
    width: float,
    height: float,
) -> list[list[dict[str, Any]]]:
    if not lines:
        return [[]]
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for item in lines:
        candidate = current + [item]
        table = _invoice_table(candidate, row, width)
        _w, table_height = table.wrap(width, height)
        if table_height <= height:
            current = candidate
            continue
        if not current:
            raise ValueError("invoice line does not fit inside the configured invoice table area")
        chunks.append(current)
        current = [item]
        single = _invoice_table(current, row, width)
        if single.wrap(width, height)[1] > height:
            raise ValueError("invoice line does not fit inside the configured invoice table area")
    if current:
        chunks.append(current)
    return chunks


def _draw_invoice_lines(
    c: canvas.Canvas,
    element: dict[str, Any],
    row: dict[str, Any],
    lines: list[dict[str, Any]],
) -> None:
    x, y, width, height = _rect(element)
    table = _invoice_table(lines, row, width)
    _w, table_height = table.wrap(width, height)
    table.drawOn(c, x, y + height - table_height)


def _draw_invoice_totals(c: canvas.Canvas, element: dict[str, Any], row: dict[str, Any]) -> None:
    x, y, width, height = _rect(element)
    totals = row.get("totals") or {}
    currency = _currency(row)
    rows = [
        ["Nettosumme", f"{totals.get('net', '0.00')} {currency}"],
        ["Umsatzsteuer", f"{totals.get('tax', '0.00')} {currency}"],
        ["Gesamtbetrag", f"{totals.get('gross', '0.00')} {currency}"],
    ]
    table = Table(rows, colWidths=[width * .62, width * .38])
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -2), "Helvetica", 8),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), .5, colors.HexColor("#64748b")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    _w, table_height = table.wrap(width, height)
    if table_height > height:
        raise ValueError("invoice totals do not fit inside the configured totals area")
    table.drawOn(c, x, y + height - table_height)


def _draw_page_number(c: canvas.Canvas, page: int, total: int) -> None:
    if total <= 1:
        return
    c.saveState()
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#64748b"))
    c.drawCentredString(A4[0] / 2, 10 * mm, f"{page} / {total}")
    c.restoreState()


def render_visual_invoice_pdf(root, template_id: str, row: dict[str, Any]) -> bytes:
    meta, layout = visual_template(root, template_id)
    if meta.get("document_type") != "invoice":
        raise ValueError("selected visual template is not an invoice template")
    lines_element = next((e for e in layout["elements"] if e.get("type") == "invoice_lines" and e.get("visible", True)), None)
    totals_element = next((e for e in layout["elements"] if e.get("type") == "invoice_totals" and e.get("visible", True)), None)
    if lines_element is None or totals_element is None:
        raise ValueError("invoice visual template requires invoice lines and totals elements")
    _x, _y, width, height = _rect(lines_element)
    chunks = _split_invoice_lines(list(row.get("lines") or []), row, width, height)
    context = invoice_template_context(row, layout)
    target = io.BytesIO()
    c = canvas.Canvas(target, pagesize=A4)
    total_pages = len(chunks)
    for page_index, chunk in enumerate(chunks):
        first_page = page_index == 0
        last_page = page_index == total_pages - 1
        _draw_static(c, root, template_id, layout, context, first_page=first_page)
        _draw_invoice_lines(c, lines_element, row, chunk)
        if last_page:
            _draw_invoice_totals(c, totals_element, row)
        _draw_page_number(c, page_index + 1, total_pages)
        c.showPage()
    c.save()
    return target.getvalue()


def _wrap_plain_text(text: str, *, max_chars: int) -> list[str]:
    result: list[str] = []
    for raw in str(text).replace("\r\n", "\n").split("\n"):
        words = raw.split()
        if not words:
            result.append("")
            continue
        line = words[0]
        for word in words[1:]:
            if len(line) + len(word) + 1 <= max_chars:
                line += " " + word
            else:
                result.append(line)
                line = word
        result.append(line)
    return result


def render_visual_letter_pdf(
    root,
    template_id: str,
    *,
    business: dict[str, Any],
    recipient: str,
    subject: str,
    body: str,
) -> bytes:
    meta, layout = visual_template(root, template_id)
    if meta.get("document_type") not in {"letter", "quote", "delivery_note"}:
        raise ValueError("selected visual template is not a body-document template")
    body_element = next((e for e in layout["elements"] if e.get("type") == "body" and e.get("visible", True)), None)
    if body_element is None:
        raise ValueError("letter visual template requires a body element")
    context = business_template_context(business, layout, recipient=recipient, subject=subject)
    x, y, width, height = _rect(body_element)
    style = body_element.get("style") or {}
    font_size = float(style.get("font_size", 10))
    leading = font_size * 1.35
    chars = max(24, int(width / max(font_size * .52, 1)))
    lines = _wrap_plain_text(body, max_chars=chars)
    per_page = max(1, int(height // leading))
    chunks = [lines[index:index + per_page] for index in range(0, len(lines), per_page)] or [[]]
    target = io.BytesIO()
    c = canvas.Canvas(target, pagesize=A4)
    total_pages = len(chunks)
    for page_index, chunk in enumerate(chunks):
        _draw_static(c, root, template_id, layout, context, first_page=page_index == 0)
        c.saveState()
        c.setFillColor(colors.HexColor(str(style.get("color", "#111827"))))
        c.setFont(_font_name(style), font_size)
        cursor = y + height - font_size
        for line in chunk:
            c.drawString(x, cursor, line)
            cursor -= leading
        c.restoreState()
        _draw_page_number(c, page_index + 1, total_pages)
        c.showPage()
    c.save()
    return target.getvalue()


def sample_invoice() -> dict[str, Any]:
    return {
        "invoice_number": "RE-2026-0001",
        "issue_date": date.today().isoformat(),
        "service_date": date.today().isoformat(),
        "due_date": date.today().isoformat(),
        "currency": "EUR",
        "seller": {
            "name": "Musterfirma GmbH", "street": "Musterstraße 1", "postal": "12345",
            "city": "Musterstadt", "email": "info@example.invalid", "iban": "DE001234",
            "blz": "00000000", "bic": "MUSTERBIC", "bank": "Musterbank", "vat_id": "DE123456789",
            "tax_number": "12/345/67890",
        },
        "buyer": {"name": "Musterkunde GmbH", "label": "Musterkunde GmbH\nKundenstraße 5\n54321 Kundenstadt"},
        "lines": [
            {"line_id": "1", "description": "Beratung und Umsetzung", "quantity": "2", "net_unit_price": "125.00", "vat_rate": "19.00", "net_total": "250.00"},
            {"line_id": "2", "description": "Dokumentation", "quantity": "1", "net_unit_price": "80.00", "vat_rate": "19.00", "net_total": "80.00"},
        ],
        "totals": {"net": "330.00", "tax": "62.70", "gross": "392.70"},
    }
