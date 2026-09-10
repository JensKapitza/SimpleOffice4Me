"""Reusable corporate documents, invoices, CRM links and ZUGFeRD-aware storage.

Corporate design templates are three-page backgrounds:
  page 1: optional cover/title page
  page 2: first content page
  page 3: continuation pages

Invoices are calculated server-side with Decimal values. Catalog objects are snapshotted
into each invoice, so later product/price changes never modify historical invoices.
ZUGFeRD/CII XML is embedded in every generated invoice. A PDF is only marked as
validated when the configured PDF/A-3 and XML validation steps actually succeed.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
import shlex
import subprocess
import tempfile
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, BinaryIO

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, Frame, ListFlowable, ListItem, PageTemplate, Paragraph, Spacer, Table, TableStyle

from .auth import login_required
from .calendar_store import CalendarStore
from .contact_extensions import ContactCRMStore
from .customer_credit import CustomerCreditLedger
from .contact_store import ContactStore
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .object_store import ObjectStore
from .project_store import ProjectStore
from .settings_store import translate

TEMPLATE_DIR = "business-templates"
LINK_FILE = "contact-document-links.json"
SETTINGS_FILE = "business-document-settings.json"
INVOICE_DIR = "invoices"
INVOICE_SEQUENCE = "invoice-sequence.json"
CREDIT_NOTE_SEQUENCE = "credit-note-sequence.json"
ZUGFERD_FILENAMES = {"factur-x.xml", "zugferd-invoice.xml", "zugferd.xml"}
MONEY = Decimal("0.01")
QTY = Decimal("0.001")
SUPPORTED_TEMPLATE_SUFFIXES = {".pdf", ".odt", ".ott", ".doc", ".docx", ".rtf", ".odp", ".ppt", ".pptx"}
DIN_LEFT_MARGIN = 25 * mm
DIN_RIGHT_MARGIN = 20 * mm
DIN_TOP_RESERVED = 55 * mm
# Keep dynamic content out of the lowest 4 cm on every template page.  This
# area belongs to the corporate background; only the renderer's page number
# uses a small protected corridor around the horizontal centre at y=10 mm.
DIN_BOTTOM_RESERVED = 40 * mm
PAGE_NUMBER_Y = 10 * mm
PAGE_NUMBER_CLEAR_BOTTOM = 6 * mm
PAGE_NUMBER_CLEAR_TOP = 14 * mm
logger = logging.getLogger("app.business_documents")
WRITE_OFF_REASONS = {
    "customer_deceased",
    "insolvency",
    "unknown_address",
    "collection_uneconomical",
    "goodwill",
    "other",
}


def _root() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]).expanduser().resolve()


def _actor() -> str:
    return str(g.user["username"])


def _is_admin() -> bool:
    try: return bool(g.user["is_admin"])
    except (KeyError, TypeError, IndexError): return False


def _safe_filename(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return clean[:100] or "document"


def _read_json(path: Path, default: Any) -> Any:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return default


def _template_directory(root: Path) -> Path:
    path = root / CONTROL_DIR / TEMPLATE_DIR; path.mkdir(parents=True, exist_ok=True); return path


def _template_index(root: Path) -> Path:
    return _template_directory(root) / "templates.json"


def templates(root: Path) -> list[dict[str, Any]]:
    rows = _read_json(_template_index(root), {"templates": []}).get("templates", [])
    return sorted(rows, key=lambda row: (not bool(row.get("active")), str(row.get("name", "")).casefold()))


def template(root: Path, template_id: str) -> dict[str, Any]:
    row = next((item for item in templates(root) if item.get("template_id") == template_id), None)
    if row is None: raise ValueError("unknown business document template")
    return row


def _office_to_pdf(raw: bytes, filename: str) -> bytes:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".pdf": return raw
    if suffix not in SUPPORTED_TEMPLATE_SUFFIXES: raise ValueError("unsupported template format")
    libreoffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not libreoffice: raise ValueError("Office template conversion requires LibreOffice/soffice")
    with tempfile.TemporaryDirectory(prefix="simpleoffice-template-") as temp:
        work = Path(temp); source = work / (Path(filename).name or f"template{suffix}"); source.write_bytes(raw)
        result = subprocess.run([libreoffice, "--headless", "--convert-to", "pdf", "--outdir", str(work), str(source)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
        target = work / f"{source.stem}.pdf"
        if result.returncode != 0 or not target.is_file(): raise ValueError("LibreOffice could not convert the corporate template to PDF")
        return target.read_bytes()


def save_template(root: Path, upload, name: str, actor: str) -> dict[str, Any]:
    if not name.strip(): raise ValueError("template name is required")
    raw = upload.read(25 * 1024 * 1024 + 1)
    if not raw or len(raw) > 25 * 1024 * 1024: raise ValueError("template must be between 1 byte and 25 MiB")
    pdf = _office_to_pdf(raw, upload.filename or "template.pdf")
    try: reader = PdfReader(io.BytesIO(pdf))
    except Exception as exc: raise ValueError("template is not a readable PDF") from exc
    if len(reader.pages) != 3: raise ValueError("corporate template must render to exactly three pages")
    directory = _template_directory(root); template_id = str(uuid.uuid4()); pdf_name = f"{template_id}.pdf"; (directory / pdf_name).write_bytes(pdf)
    with exclusive_file_lock(directory / ".templates-write.lock"):
        payload = _read_json(_template_index(root), {"templates": []})
        row = {"template_id": template_id, "name": name.strip(), "file": pdf_name, "pages": 3, "source_name": upload.filename or "", "active": not bool(payload.get("templates")), "created_at": utc_now(), "created_by": actor}
        payload.setdefault("templates", []).append(row); atomic_json_write(_template_index(root), payload)
    DocumentStore(root).history.record("business_template_created", actor, "business-template", template_id, row)
    return row


def set_active_template(root: Path, template_id: str, actor: str) -> None:
    directory = _template_directory(root)
    with exclusive_file_lock(directory / ".templates-write.lock"):
        payload = _read_json(_template_index(root), {"templates": []}); found = False
        for row in payload.get("templates", []): row["active"] = row.get("template_id") == template_id; found = found or row["active"]
        if not found: raise ValueError("unknown business document template")
        atomic_json_write(_template_index(root), payload)
    DocumentStore(root).history.record("business_template_activated", actor, "business-template", template_id, {})


def active_template(root: Path, template_id: str = "") -> dict[str, Any]:
    if template_id: return template(root, template_id)
    row = next((item for item in templates(root) if item.get("active")), None)
    if row is None: raise ValueError("no active business document template configured")
    return row


def business_settings(root: Path) -> dict[str, Any]:
    defaults = {"seller_name": "", "seller_street": "", "seller_postal": "", "seller_city": "", "seller_state": "", "seller_country": "DE", "seller_email": "", "seller_vat_id": "", "seller_tax_number": "", "seller_iban": "", "seller_bic": "", "seller_bank": "", "payment_terms": "Zahlbar ohne Abzug", "default_payment_days": "14", "currency": "EUR", "zugferd_profile": "EN16931", "zugferd_version": "2.5.2", "require_zugferd_validation": True}
    stored = _read_json(root / CONTROL_DIR / SETTINGS_FILE, {})
    if isinstance(stored, dict): defaults.update(stored)
    return defaults


def save_business_settings(root: Path, values: dict[str, Any], actor: str) -> dict[str, Any]:
    settings = business_settings(root)
    for key in ("seller_name", "seller_street", "seller_postal", "seller_city", "seller_state", "seller_country", "seller_email", "seller_vat_id", "seller_tax_number", "seller_iban", "seller_bic", "seller_bank", "payment_terms", "currency", "zugferd_profile"):
        settings[key] = str(values.get(key, settings.get(key, ""))).strip()
    try: days = int(str(values.get("default_payment_days", settings.get("default_payment_days", "14"))).strip())
    except ValueError as exc: raise ValueError("default payment days must be an integer") from exc
    if not 0 <= days <= 3650: raise ValueError("default payment days must be between 0 and 3650")
    settings["default_payment_days"] = str(days); settings["seller_country"] = (settings["seller_country"] or "DE").upper()[:2]; settings["currency"] = (settings["currency"] or "EUR").upper()[:3]
    settings["zugferd_version"] = "2.5.2"; settings["require_zugferd_validation"] = True
    path = root / CONTROL_DIR / SETTINGS_FILE; path.parent.mkdir(parents=True, exist_ok=True); atomic_json_write(path, settings)
    DocumentStore(root).history.record("business_settings_updated", actor, "business-settings", "default", {key: value for key, value in settings.items() if "iban" not in key.casefold()})
    return settings


def _recipient_names(contact: dict[str, Any]) -> list[str]:
    fields = contact.get("fields", {})
    company = str(fields.get("company", "")).strip()
    structured_person = " ".join(filter(None, (str(fields.get("first_name", "")).strip(), str(fields.get("last_name", "")).strip())))
    person = structured_person or str(fields.get("display_name", "")).strip()
    names: list[str] = []
    for value in (company, person):
        if value and value.casefold() not in {item.casefold() for item in names}:
            names.append(value)
    return names


def _recipient_label(contact: dict[str, Any], *address_lines: str) -> str:
    lines = [str(line).strip() for line in address_lines if str(line).strip()]
    names = _recipient_names(contact)
    name_keys = {name.casefold() for name in names}
    return "\n".join(names + [line for line in lines if line.casefold() not in name_keys])


def address_labels(contact: dict[str, Any], crm: dict[str, Any], selected: str = "") -> tuple[str, list[dict[str, str]]]:
    candidates: list[dict[str, str]] = []
    for index, item in enumerate(crm.get("addresses", [])):
        if not isinstance(item, dict): continue
        street, postal, city = (str(item.get(key, "")).strip() for key in ("street", "postal", "city")); state = str(item.get("state", "")).strip(); country = str(item.get("country", "")).strip().upper()
        if not any((street, postal, city)): continue
        address_type = str(item.get("type", "other")).strip() or "other"
        label = _recipient_label(contact, ContactStore.format_postal_address({"street": street, "postal": postal, "city": city, "state": state, "country": "" if country == "DE" else country}))
        candidates.append({"id": f"crm-{index}", "type": address_type, "label": label, "street": street, "postal": postal, "city": city, "state": state, "country": country or "DE"})
    for index, item in enumerate(contact.get("addresses", [])):
        value = str(item.get("value", "")).strip(); components = item.get("components", {})
        if value: candidates.append({"id": f"contact-{index}", "type": str(item.get("label", "Adresse")), "label": _recipient_label(contact, *value.splitlines()), "street": components.get("street", value), "postal": components.get("postal", ""), "city": components.get("city", ""), "state": components.get("state", ""), "country": components.get("country", "DE")})
    choice = next((item for item in candidates if item["id"] == selected), None)
    choice = choice or next((item for item in candidates if item["type"].casefold() in {"billing", "rechnung", "rechnungsadresse"}), None)
    choice = choice or (candidates[0] if candidates else None)
    return (choice["label"] if choice else ""), candidates


def _markdown_flowables(markdown: str) -> list[Any]:
    styles = getSampleStyleSheet(); body = ParagraphStyle("LetterBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=10.5, leading=15, spaceAfter=7); heading = ParagraphStyle("LetterHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=17, spaceBefore=7, spaceAfter=7)
    flow: list[Any] = []; bullets: list[ListItem] = []
    def inline(text: str) -> str:
        value = html.escape(text); value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value); return re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", value)
    def flush() -> None:
        nonlocal bullets
        if bullets: flow.append(ListFlowable(bullets, bulletType="bullet", leftIndent=15, bulletFontName="Helvetica", bulletFontSize=8)); flow.append(Spacer(1, 4)); bullets = []
    for raw in markdown.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.startswith("#"): flush(); flow.append(Paragraph(inline(line.lstrip("#").strip()), heading))
        elif re.match(r"^\s*[-*]\s+", line): bullets.append(ListItem(Paragraph(inline(re.sub(r"^\s*[-*]\s+", "", line)), body)))
        elif not line.strip(): flush(); flow.append(Spacer(1, 5))
        else: flush(); flow.append(Paragraph(inline(line), body))
    flush(); return flow


class _ContentDocTemplate(BaseDocTemplate):
    def __init__(self, target, *, recipient: str = "", subject: str = "", top_margin: float = DIN_TOP_RESERVED):
        super().__init__(target, pagesize=A4, leftMargin=DIN_LEFT_MARGIN, rightMargin=DIN_RIGHT_MARGIN, topMargin=max(top_margin, DIN_TOP_RESERVED), bottomMargin=DIN_BOTTOM_RESERVED)
        self.recipient, self.subject = recipient, subject
        self.addPageTemplates(PageTemplate(id="content", frames=[Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="content")], onPage=self._header))
    def _header(self, canv, doc):
        if doc.page != 1 or not self.recipient: return
        canv.saveState(); y = A4[1] - 24 * mm; canv.setFont("Helvetica", 9)
        for line in self.recipient.splitlines(): canv.drawString(25 * mm, y, line[:120]); y -= 4.3 * mm
        if self.subject: canv.setFont("Helvetica-Bold", 11); canv.drawString(25 * mm, A4[1] - 49 * mm, self.subject[:140])
        canv.restoreState()


def _content_pdf(recipient: str, subject: str, markdown: str) -> bytes:
    target = io.BytesIO(); _ContentDocTemplate(target, recipient=recipient, subject=subject).build(_markdown_flowables(markdown)); return target.getvalue()


def din5008_template_guide_pdf() -> bytes:
    """Create a three-page DIN-5008 design guide for corporate backgrounds."""
    target = io.BytesIO(); c = canvas.Canvas(target, pagesize=A4)
    page_titles = ("Seite 1 – optionale Titelseite", "Seite 2 – erste Inhaltsseite", "Seite 3 – Folgeseiten")
    for page_number, title in enumerate(page_titles, 1):
        c.setFont("Helvetica-Bold", 15); c.drawString(15 * mm, A4[1] - 14 * mm, "SimpleOffice DIN-5008 Vorlagenmuster")
        c.setFont("Helvetica", 10); c.drawString(15 * mm, A4[1] - 20 * mm, title)
        c.setStrokeColor(colors.HexColor("#1f6feb")); c.setFillColor(colors.Color(.12, .42, .92, alpha=.07))
        c.rect(DIN_LEFT_MARGIN, DIN_BOTTOM_RESERVED, A4[0] - DIN_LEFT_MARGIN - DIN_RIGHT_MARGIN, A4[1] - DIN_TOP_RESERVED - DIN_BOTTOM_RESERVED, fill=1)
        c.setFillColor(colors.HexColor("#1f6feb")); c.setFont("Helvetica-Bold", 9)
        c.drawString(DIN_LEFT_MARGIN + 2 * mm, A4[1] - DIN_TOP_RESERVED - 5 * mm, "DYNAMISCHER INHALT – hier keine statischen Texte/Grafiken platzieren")
        c.setStrokeColor(colors.HexColor("#238636")); c.setFillColor(colors.Color(.13, .53, .21, alpha=.08))
        c.rect(15 * mm, A4[1] - 43 * mm, A4[0] - 30 * mm, 18 * mm, fill=1)
        c.setFillColor(colors.HexColor("#238636")); c.drawString(17 * mm, A4[1] - 34 * mm, "Logo-/Kopfbereich (empfohlen: oberhalb 45 mm)")
        c.setStrokeColor(colors.HexColor("#9a6700")); c.setFillColor(colors.Color(.85, .63, .08, alpha=.1))
        c.rect(15 * mm, 0, A4[0] - 30 * mm, DIN_BOTTOM_RESERVED, fill=1)
        c.setFillColor(colors.HexColor("#9a6700")); c.drawString(17 * mm, 34 * mm, "4 cm FREIER FUSSBEREICH – Logo, Firmenangaben, IBAN und Infotexte")
        c.setStrokeColor(colors.HexColor("#cf222e")); c.setFillColor(colors.white)
        c.rect(A4[0] / 2 - 32 * mm, PAGE_NUMBER_CLEAR_BOTTOM, 64 * mm, PAGE_NUMBER_CLEAR_TOP - PAGE_NUMBER_CLEAR_BOTTOM, fill=1)
        c.setFillColor(colors.HexColor("#cf222e")); c.drawCentredString(A4[0] / 2, PAGE_NUMBER_Y, f"SEITENZAHL FREIHALTEN · {page_number} / 3")
        if page_number == 2:
            c.setDash(3, 2); c.setStrokeColor(colors.HexColor("#cf222e")); c.rect(20 * mm, A4[1] - 90 * mm, 85 * mm, 45 * mm, fill=0); c.setDash()
            c.setFillColor(colors.HexColor("#cf222e")); c.drawString(22 * mm, A4[1] - 94 * mm, "DIN-5008-Anschriftzone (wird vom Renderer dynamisch belegt)")
        c.setFillColor(colors.black); c.setFont("Helvetica", 7)
        c.drawRightString(A4[0] - 17 * mm, 18 * mm, "Hilfslinien und Beschriftungen vor produktiver Verwendung entfernen")
        c.showPage()
    c.save(); return target.getvalue()


def _cover_overlay(title: str, recipient: str) -> bytes:
    target = io.BytesIO(); c = canvas.Canvas(target, pagesize=A4); c.setFont("Helvetica-Bold", 24); c.drawCentredString(A4[0] / 2, A4[1] * .58, title[:100]); c.setFont("Helvetica", 12); y = A4[1] * .48
    for line in recipient.splitlines(): c.drawCentredString(A4[0] / 2, y, line[:120]); y -= 6 * mm
    c.save(); return target.getvalue()


def _number_overlay(number: int, total: int) -> bytes:
    target = io.BytesIO(); c = canvas.Canvas(target, pagesize=A4); c.setFont("Helvetica", 8.5); c.drawCentredString(A4[0] / 2, PAGE_NUMBER_Y, f"{number} / {total}"); c.save(); return target.getvalue()


def _background_page(root: Path, template_row: dict[str, Any], index: int):
    return PdfReader(_template_directory(root) / template_row["file"]).pages[index]


def _merge_content_with_template(root: Path, template_row: dict[str, Any], content_pdf: bytes, *, cover: bool = False, cover_title: str = "", cover_recipient: str = "") -> bytes:
    content = PdfReader(io.BytesIO(content_pdf)); writer = PdfWriter()
    if cover:
        page = _background_page(root, template_row, 0); page.merge_page(PdfReader(io.BytesIO(_cover_overlay(cover_title or "Dokument", cover_recipient))).pages[0]); writer.add_page(page)
    for index, overlay in enumerate(content.pages):
        page = _background_page(root, template_row, 1 if index == 0 else 2); page.merge_page(overlay); writer.add_page(page)
    total = len(writer.pages)
    if total > 1:
        for index, page in enumerate(writer.pages, 1): page.merge_page(PdfReader(io.BytesIO(_number_overlay(index, total))).pages[0])
    result = io.BytesIO(); writer.write(result); return result.getvalue()


def render_business_pdf(root: Path, template_row: dict[str, Any], *, recipient: str, subject: str, markdown: str, cover: bool = False) -> bytes:
    return _merge_content_with_template(root, template_row, _content_pdf(recipient, subject, markdown), cover=cover, cover_title=subject or "Dokument", cover_recipient=recipient)


def _money(value: Any, field: str = "amount") -> Decimal:
    try: number = Decimal(str(value or "0").strip().replace(",", "."))
    except InvalidOperation as exc: raise ValueError(f"invalid {field}") from exc
    return number.quantize(MONEY, rounding=ROUND_HALF_UP)


def _quantity(value: Any) -> Decimal:
    try: number = Decimal(str(value or "0").strip().replace(",", "."))
    except InvalidOperation as exc: raise ValueError("invalid quantity") from exc
    if number <= 0: raise ValueError("quantity must be greater than zero")
    return number.quantize(QTY, rounding=ROUND_HALF_UP).normalize()


def _invoice_number(root: Path) -> str:
    now = datetime.now(timezone.utc); path = root / CONTROL_DIR / INVOICE_SEQUENCE; lock = path.with_suffix(".lock"); path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(lock):
        state = _read_json(path, {"years": {}}); years = state.setdefault("years", {}); year = str(now.year); number = int(years.get(year, 0)) + 1; years[year] = number; atomic_json_write(path, state)
    return f"{year}-{number:04d}"


def _draft_invoice_number(root: Path, issue_date: date) -> str:
    """Return a non-binding preview of the next annual number without consuming it."""
    path = root / CONTROL_DIR / INVOICE_SEQUENCE
    state = _read_json(path, {"years": {}})
    next_number = int(state.get("years", {}).get(str(issue_date.year), 0)) + 1
    return f"DRAFT-{issue_date.year}-{next_number:04d}"


def _credit_note_number(root: Path) -> str:
    now = datetime.now(timezone.utc); path = root / CONTROL_DIR / CREDIT_NOTE_SEQUENCE; lock = path.with_suffix(".lock"); path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(lock):
        state = _read_json(path, {"years": {}}); years = state.setdefault("years", {}); year = str(now.year); number = int(years.get(year, 0)) + 1; years[year] = number; atomic_json_write(path, state)
    return f"GS-{year}-{number:04d}"


def _epc_qr_payload(row: dict[str, Any], amount: Decimal) -> str:
    """Return an EPC069-12 SEPA Credit Transfer payload for the invoice."""
    seller = row.get("seller", {})
    name = str(seller.get("name", "")).strip()[:70]
    iban = re.sub(r"\s+", "", str(seller.get("iban", ""))).upper()
    bic = re.sub(r"\s+", "", str(seller.get("bic", ""))).upper()[:11]
    if amount <= 0 or not name or not re.fullmatch(r"[A-Z]{2}[0-9A-Z]{13,32}", iban):
        return ""
    purpose = f"Rechnung {row.get('invoice_number', '')}"[:140]
    return "\n".join(("BCD", "002", "1", "SCT", bic, name, iban,
                      f"EUR{amount.quantize(MONEY):.2f}", "", "", purpose, ""))


def _epc_qr_drawing(payload: str, size: float = 32 * mm) -> Drawing:
    code = QrCodeWidget(payload)
    x1, y1, x2, y2 = code.getBounds()
    width, height = x2 - x1, y2 - y1
    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(code)
    return drawing


def _invoice_store_path(root: Path, invoice_id: str) -> Path:
    path = root / CONTROL_DIR / INVOICE_DIR; path.mkdir(parents=True, exist_ok=True); return path / f"{invoice_id}.json"


def invoice(root: Path, invoice_id: str) -> dict[str, Any]:
    row = _read_json(_invoice_store_path(root, invoice_id), {})
    if not isinstance(row, dict) or row.get("invoice_id") != invoice_id: raise ValueError("invoice not found")
    return row


def invoice_state(row: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Return payment state without changing the immutable invoice snapshot."""
    if row.get("status") == "draft":
        gross = _money(row.get("totals", {}).get("gross", "0"))
        return {"status": "draft", "paid": "0.00", "credited": "0.00", "written_off": "0.00", "effective_total": f"{gross:.2f}", "collectible_outstanding": f"{gross:.2f}", "outstanding": f"{gross:.2f}", "collection_stopped": False}
    original_gross = _money(row.get("totals", {}).get("gross", "0"))
    credited = sum((_money(item.get("gross", "0")) for item in row.get("credit_notes", []) if isinstance(item, dict)), Decimal("0"))
    gross = max(Decimal("0"), original_gross - credited)
    paid = sum((_money(item.get("amount", "0")) for item in row.get("payments", []) if isinstance(item, dict)), Decimal("0"))
    paid = min(paid, gross)
    write_offs = [item for item in row.get("write_offs", []) if isinstance(item, dict) and not item.get("reversed_at")]
    written_off = min(sum((_money(item.get("amount", "0")) for item in write_offs), Decimal("0")), max(Decimal("0"), gross - paid))
    collectible = max(Decimal("0"), gross - paid - written_off)
    collection_stopped = any(bool(item.get("stop_collection")) for item in write_offs)
    outstanding = Decimal("0") if collection_stopped else collectible
    status = "written_off" if written_off > 0 and (collectible == 0 or collection_stopped) else "credited" if credited > 0 and gross == 0 else "paid" if outstanding == 0 else "partial" if paid > 0 or written_off > 0 else "open"
    try:
        if outstanding > 0 and date.fromisoformat(str(row.get("due_date", ""))) < (today or date.today()): status = "overdue"
    except ValueError:
        pass
    return {"status": status, "paid": f"{paid.quantize(MONEY):.2f}", "credited": f"{credited.quantize(MONEY):.2f}", "written_off": f"{written_off.quantize(MONEY):.2f}", "effective_total": f"{gross.quantize(MONEY):.2f}", "collectible_outstanding": f"{collectible.quantize(MONEY):.2f}", "outstanding": f"{outstanding.quantize(MONEY):.2f}", "collection_stopped": collection_stopped}


def write_off_invoice(root: Path, invoice_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
    path = _invoice_store_path(root, invoice_id)
    with exclusive_file_lock(path.with_suffix(".lock")):
        row = invoice(root, invoice_id)
        state = invoice_state(row)
        if row.get("status") in {"draft", "finalizing"}:
            raise ValueError("a draft invoice cannot be written off")
        if state["status"] in {"paid", "credited", "written_off"} or Decimal(state["collectible_outstanding"]) <= 0:
            raise ValueError("invoice has no collectible outstanding amount")
        reason = str(values.get("reason", "")).strip()
        if reason not in WRITE_OFF_REASONS:
            raise ValueError("write-off reason is invalid")
        note = str(values.get("note", "")).strip()[:2000]
        if reason == "other" and not note:
            raise ValueError("a note is required for another write-off reason")
        amount = _money(values.get("amount", ""), "write-off amount")
        collectible = Decimal(state["collectible_outstanding"])
        if amount <= 0 or amount > collectible:
            raise ValueError("write-off amount must be positive and not exceed the collectible outstanding amount")
        written_off_at = str(values.get("written_off_at", "")).strip() or date.today().isoformat()
        try:
            write_off_date = date.fromisoformat(written_off_at)
        except ValueError as exc:
            raise ValueError("write-off date must be a valid ISO date") from exc
        try:
            issue_date = date.fromisoformat(str(row.get("issue_date", "")))
        except ValueError:
            issue_date = None
        if issue_date and write_off_date < issue_date:
            raise ValueError("write-off date cannot precede the invoice date")
        stop_collection = str(values.get("stop_collection", "")).casefold() in {"1", "true", "yes", "on"}
        if stop_collection and amount != collectible:
            raise ValueError("stopping collection requires writing off the full collectible outstanding amount")
        entry = {
            "write_off_id": uuid.uuid4().hex,
            "reason": reason,
            "note": note,
            "written_off_at": written_off_at,
            "recorded_at": utc_now(),
            "recorded_by": actor,
            "original_outstanding": state["collectible_outstanding"],
            "amount": f"{amount:.2f}",
            "stop_collection": stop_collection,
        }
        row.setdefault("write_offs", []).append(entry)
        new_state = invoice_state(row)
        row["status"] = new_state["status"]
        row["collection_stopped"] = new_state["collection_stopped"]
        row["updated_at"] = utc_now()
        row["updated_by"] = actor
        row.setdefault("history", []).append({"type": "invoice_written_off", "at": row["updated_at"], "actor": actor, "write_off_id": entry["write_off_id"], "reason": reason, "amount": entry["amount"], "original_outstanding": entry["original_outstanding"], "stop_collection": stop_collection})
        atomic_json_write(path, row)
    DocumentStore(root).history.record("invoice_written_off", actor, "invoice", invoice_id, {"invoice_number": row.get("invoice_number", ""), "contact_id": row.get("contact_id", ""), **entry})
    return {**row, "payment_state": new_state}


def invoices(root: Path) -> list[dict[str, Any]]:
    directory = root / CONTROL_DIR / INVOICE_DIR
    rows: list[dict[str, Any]] = []
    for path in directory.glob("*.json") if directory.is_dir() else []:
        row = _read_json(path, {})
        if isinstance(row, dict) and row.get("invoice_id"):
            rows.append({**row, "payment_state": invoice_state(row)})
    return sorted(rows, key=lambda item: (str(item.get("issue_date", "")), str(item.get("invoice_number", ""))), reverse=True)


def customer_account_overview(root: Path, actor: str, currency: str = "") -> dict[str, Any]:
    """Return visible customer balances with one contact and one ledger scan."""
    selected_currency = (currency or business_settings(root).get("currency") or "EUR").upper()[:3]
    contacts = ContactStore(root).contacts(actor)
    contact_map = {item["contact_id"]: item for item in contacts}
    contact_ids = set(contact_map)
    credit_accounts = CustomerCreditLedger(root).accounts(contact_ids, selected_currency)
    claims = defaultdict(lambda: Decimal("0"))
    for row in invoices(root):
        contact_id = str(row.get("contact_id", ""))
        if contact_id not in contact_ids or str(row.get("currency", "EUR")).upper() != selected_currency:
            continue
        claims[contact_id] += Decimal(row["payment_state"]["outstanding"])
    rows = []
    for contact_id, contact in contact_map.items():
        credit = Decimal(credit_accounts[contact_id]["balance"])
        outstanding = claims[contact_id]
        if not credit and not outstanding:
            continue
        fields = contact.get("fields", {})
        rows.append({
            "contact_id": contact_id,
            "name": str(fields.get("display_name") or fields.get("company") or contact_id),
            "credit": f"{credit.quantize(MONEY):.2f}",
            "outstanding": f"{outstanding.quantize(MONEY):.2f}",
            "net": f"{(credit - outstanding).quantize(MONEY):.2f}",
            "currency": selected_currency,
        })
    rows.sort(key=lambda item: (-(Decimal(item["outstanding"]) + Decimal(item["credit"])), item["name"].casefold()))
    return {
        "currency": selected_currency,
        "credit_total": f"{sum((Decimal(item['credit']) for item in rows), Decimal('0')).quantize(MONEY):.2f}",
        "outstanding_total": f"{sum((Decimal(item['outstanding']) for item in rows), Decimal('0')).quantize(MONEY):.2f}",
        "rows": rows,
    }


def record_invoice_payment(root: Path, invoice_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
    path = _invoice_store_path(root, invoice_id)
    with exclusive_file_lock(path.with_suffix(".lock")):
        row = invoice(root, invoice_id); state = invoice_state(row); outstanding = _money(state["outstanding"])
        if row.get("status") in {"draft", "finalizing"} or state["status"] == "written_off": raise ValueError("payments cannot be recorded for a draft, while finalizing or after collection was stopped")
        if outstanding <= 0: raise ValueError("invoice is already paid")
        amount = _money(values.get("amount", ""), "payment amount")
        if amount <= 0 or amount > outstanding: raise ValueError("payment amount must be positive and not exceed the outstanding amount")
        paid_at = str(values.get("paid_at", "")).strip() or date.today().isoformat()
        try: date.fromisoformat(paid_at)
        except ValueError as exc: raise ValueError("payment date must be a valid ISO date") from exc
        payment = {"payment_id": uuid.uuid4().hex, "amount": f"{amount:.2f}", "paid_at": paid_at, "reference": str(values.get("reference", "")).strip()[:200], "source": str(values.get("source", "bank")).strip()[:40] or "bank", "ledger_entry_id": str(values.get("ledger_entry_id", "")).strip(), "recorded_at": utc_now(), "recorded_by": actor}
        row.setdefault("payments", []).append(payment); new_state = invoice_state(row); row["status"] = new_state["status"]; row["updated_at"] = utc_now(); row["updated_by"] = actor
        row.setdefault("history", []).append({"type": "payment_recorded", "at": row["updated_at"], "actor": actor, "payment_id": payment["payment_id"], "amount": payment["amount"], "status": new_state["status"]})
        atomic_json_write(path, row)
    DocumentStore(root).history.record("invoice_payment_recorded", actor, "invoice", invoice_id, {"payment_id": payment["payment_id"], "amount": payment["amount"], "paid_at": paid_at, "status": new_state["status"]})
    return {**row, "payment_state": new_state}


def apply_available_customer_credit(root: Path, invoice_id: str, actor: str) -> dict[str, Any]:
    """Apply available same-currency credit as payment without changing VAT totals."""
    row = invoice(root, invoice_id)
    if row.get("status") in {"draft", "finalizing", "written_off"}: raise ValueError("customer credit cannot be applied before an invoice is finalized or after collection was stopped")
    state = invoice_state(row)
    outstanding = _money(state["outstanding"])
    account = CustomerCreditLedger(root).account(row["contact_id"], row.get("currency", "EUR"))
    available = Decimal(account["balance"])
    if outstanding <= 0 or available <= 0:
        return {**row, "payment_state": state, "credit_applied": "0.00"}
    amount = min(outstanding, available).quantize(MONEY)
    ledger = CustomerCreditLedger(root)
    entry = ledger.apply(row["contact_id"], invoice_id, amount, actor=actor, currency=row.get("currency", "EUR"))
    try:
        updated = record_invoice_payment(root, invoice_id, {
            "amount": f"{amount:.2f}", "paid_at": date.today().isoformat(),
            "reference": f"Kundenguthaben {entry['entry_id'][:8]}",
            "source": "customer_credit", "ledger_entry_id": entry["entry_id"],
        }, actor)
    except Exception:
        ledger.add(row["contact_id"], amount, kind="manual", tax_treatment="manual_review",
                   actor=actor, note="Automatische Rückbuchung nach fehlgeschlagener Rechnungsverrechnung",
                   reference=entry["entry_id"], currency=row.get("currency", "EUR"),
                   related_invoice_id=invoice_id)
        raise
    updated["credit_applied"] = f"{amount:.2f}"
    return updated


def _build_invoice_lines(root: Path, form) -> list[dict[str, Any]]:
    store = ObjectStore(root); object_ids = form.getlist("line_object_id"); descriptions = form.getlist("line_description"); quantities = form.getlist("line_quantity"); nets = form.getlist("line_net_price"); vats = form.getlist("line_vat_rate"); categories=form.getlist("line_category"); project_ids=form.getlist("line_project_id"); source_types=form.getlist("line_source_type"); source_ids=form.getlist("line_source_id")
    count = max(len(object_ids), len(descriptions), len(quantities), len(nets), len(vats),len(categories),len(project_ids),len(source_types),len(source_ids)); lines: list[dict[str, Any]] = []
    for index in range(count):
        object_id = object_ids[index].strip() if index < len(object_ids) else ""; description = descriptions[index].strip() if index < len(descriptions) else ""; qty_text = quantities[index] if index < len(quantities) else "1"; net_text = nets[index] if index < len(nets) else "0"; vat_text = vats[index] if index < len(vats) else "0"; category=categories[index].strip() if index<len(categories) else ""; project_id=project_ids[index].strip() if index<len(project_ids) else ""; source_type=source_types[index].strip() if index<len(source_types) else ""; source_id=source_ids[index].strip() if index<len(source_ids) else ""
        if not object_id and not description: continue
        snapshot: dict[str, Any] = {"category":category}
        if project_id:
            if not all((source_type,source_id)) or source_type not in {"time_group","time_entry"}: raise ValueError(f"invoice line {index + 1}: invalid project billing source")
            snapshot.update({"project_id":project_id,"project_source_type":source_type,"project_source_id":source_id})
        elif source_type or source_id:
            if source_type != "calendar_event" or not source_id: raise ValueError(f"invoice line {index + 1}: invalid appointment billing source")
            snapshot.update({"source_type":"calendar_event","source_id":source_id})
        if object_id:
            try:
                obj = store.object(object_id); effective = store.invoice_effective(obj)
            except ValueError as exc: raise ValueError(f"invoice line {index + 1}: unknown catalog object") from exc
            if not effective.get("use_in_invoice"): raise ValueError(f"invoice line {index + 1}: object is not enabled for invoices")
            snapshot.update({"object_id": obj["object_id"], "object_display_id": obj["display_id"], "object_name": obj["name"], "category": category or effective.get("category", ""), "price_group": effective.get("price_group", "")})
            description = description or effective.get("description") or obj["name"]; net_text = net_text or effective.get("net_price", "0"); vat_text = vat_text or effective.get("vat_rate", "0")
        quantity = _quantity(qty_text); net_price = _money(net_text, "net unit price")
        try: vat_rate = Decimal(str(vat_text or "0").replace(",", ".")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation as exc: raise ValueError("invalid VAT rate") from exc
        if vat_rate < 0: raise ValueError("VAT rate must not be negative")
        net_total = (net_price * quantity).quantize(MONEY, rounding=ROUND_HALF_UP); tax_total = (net_total * vat_rate / Decimal("100")).quantize(MONEY, rounding=ROUND_HALF_UP); gross_total = net_total + tax_total
        lines.append({"line_id": len(lines) + 1, **snapshot, "description": description or "Position", "quantity": format(quantity, "f"), "unit": "C62", "net_unit_price": f"{net_price:.2f}", "vat_rate": format(vat_rate, "f"), "net_total": f"{net_total:.2f}", "tax_total": f"{tax_total:.2f}", "gross_total": f"{gross_total:.2f}"})
    if not lines: raise ValueError("at least one invoice line is required")
    return lines


def _project_source_refs(row: dict[str, Any]) -> set[tuple[str,str,str]]:
    return {(str(line.get("project_id","")),str(line.get("project_source_type","")),str(line.get("project_source_id",""))) for line in row.get("lines",[]) if line.get("project_id") and line.get("project_source_type") and line.get("project_source_id")}


def _billed_project_sources(root: Path, exclude_invoice_id: str = "") -> set[tuple[str,str,str]]:
    return {ref for item in invoices(root) if item.get("invoice_id") != exclude_invoice_id and item.get("status") != "draft" and item.get("document_id") for ref in _project_source_refs(item)}


def _appointment_source_refs(row: dict[str, Any]) -> set[str]:
    return {str(line.get("source_id", "")) for line in row.get("lines", []) if line.get("source_type") == "calendar_event" and line.get("source_id")}


def _billed_appointment_sources(root: Path, exclude_invoice_id: str = "") -> set[str]:
    return {event_id for item in invoices(root) if item.get("invoice_id") != exclude_invoice_id and item.get("status") != "draft" and item.get("document_id") for event_id in _appointment_source_refs(item)}


def _validate_project_sources(root: Path, row: dict[str, Any], actor: str) -> None:
    refs=[(str(line.get("project_id","")),str(line.get("project_source_type","")),str(line.get("project_source_id",""))) for line in row.get("lines",[]) if line.get("project_id")]
    if len(refs)!=len(set(refs)): raise ValueError("a project billing item can only appear once on an invoice")
    if refs:
        billed=_billed_project_sources(root,row.get("invoice_id",""))
        if set(refs)&billed: raise ValueError("a selected project billing item has already been invoiced")
        store=ProjectStore(root); available:set[tuple[str,str,str]]=set()
        for project_id in {ref[0] for ref in refs}:
            projection=store.billing_projection(project_id,actor)
            available.update((project_id,str(line["source_type"]),str(line["source_id"])) for line in projection["lines"])
        if not set(refs)<=available: raise ValueError("a selected project billing item no longer exists")
    appointment_refs = [str(line.get("source_id", "")) for line in row.get("lines", []) if line.get("source_type") == "calendar_event"]
    if len(appointment_refs) != len(set(appointment_refs)):
        raise ValueError("an appointment can only appear once on an invoice")
    if appointment_refs:
        if set(appointment_refs) & _billed_appointment_sources(root, row.get("invoice_id", "")):
            raise ValueError("a selected appointment has already been invoiced")
        events = {item["event_id"]: item for item in CalendarStore(root).events(actor)}
        for event_id in appointment_refs:
            event = events.get(event_id)
            if not event or event.get("contact_id") != row.get("contact_id") or not event.get("billing", {}).get("billable"):
                raise ValueError("a selected billable appointment no longer exists")


def _invoice_totals(lines: list[dict[str, Any]]) -> dict[str, Any]:
    net = sum((Decimal(line["net_total"]) for line in lines), Decimal("0")); tax = sum((Decimal(line["tax_total"]) for line in lines), Decimal("0")); gross = net + tax; groups: dict[str, dict[str, str]] = {}
    grouped: dict[Decimal, tuple[Decimal, Decimal]] = defaultdict(lambda: (Decimal("0"), Decimal("0")))
    for line in lines:
        rate = Decimal(line["vat_rate"]); basis, amount = grouped[rate]; grouped[rate] = (basis + Decimal(line["net_total"]), amount + Decimal(line["tax_total"]))
    for rate, (basis, amount) in grouped.items(): groups[format(rate, "f")] = {"basis": f"{basis.quantize(MONEY):.2f}", "tax": f"{amount.quantize(MONEY):.2f}"}
    return {"net": f"{net.quantize(MONEY):.2f}", "tax": f"{tax.quantize(MONEY):.2f}", "gross": f"{gross.quantize(MONEY):.2f}", "due": f"{gross.quantize(MONEY):.2f}", "vat_groups": groups}


def _invoice_content_pdf(row: dict[str, Any]) -> bytes:
    target = io.BytesIO(); doc = _ContentDocTemplate(target); styles = getSampleStyleSheet(); normal = ParagraphStyle("InvoiceNormal", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12); small = ParagraphStyle("InvoiceSmall", parent=normal, fontSize=8, leading=10); title = ParagraphStyle("InvoiceTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=19)
    flow: list[Any] = [Paragraph("Rechnung", title), Spacer(1, 3 * mm)]
    buyer = row["buyer"]; seller = row["seller"]
    header = [[Paragraph("<b>Rechnung an</b><br/>" + "<br/>".join(html.escape(x) for x in buyer["label"].splitlines()), normal), Paragraph(f"<b>Rechnungsnummer:</b> {html.escape(row['invoice_number'])}<br/><b>Rechnungsdatum:</b> {html.escape(row['issue_date'])}<br/><b>Leistungsdatum:</b> {html.escape(row['service_date'])}<br/><b>Fällig:</b> {html.escape(row['due_date'])}", normal)]]
    table = Table(header, colWidths=[95 * mm, 75 * mm]); table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"), ("BOTTOMPADDING", (0,0), (-1,-1), 6)])); flow += [table, Spacer(1, 5 * mm)]
    rows = [["Pos.", "Beschreibung", "Menge", "Netto", "MwSt.", "Gesamt"]]
    for line in row["lines"]:
        description=(f"<b>{html.escape(line['category'])}</b><br/>" if line.get("category") else "")+html.escape(line["description"])
        rows.append([str(line["line_id"]), Paragraph(description, small), line["quantity"], f"{line['net_unit_price']} €", f"{line['vat_rate']} %", f"{line['net_total']} €"])
    positions = Table(rows, repeatRows=1, colWidths=[11*mm, 83*mm, 18*mm, 24*mm, 19*mm, 25*mm]); positions.setStyle(TableStyle([("FONT", (0,0), (-1,0), "Helvetica-Bold", 8), ("FONT", (0,1), (-1,-1), "Helvetica", 8), ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#eeeeee")), ("GRID", (0,0), (-1,-1), .25, colors.HexColor("#bbbbbb")), ("VALIGN", (0,0), (-1,-1), "TOP"), ("ALIGN", (2,1), (-1,-1), "RIGHT"), ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4)])); flow += [positions, Spacer(1, 5 * mm)]
    totals = row["totals"]; settlement = row.get("settlement", {}); credit = Decimal(str(settlement.get("customer_credit", "0"))); bank_due = Decimal(str(settlement.get("bank_due", totals["gross"])))
    totals_rows = [["Nettosumme", f"{totals['net']} €"], ["Umsatzsteuer", f"{totals['tax']} €"], ["Gesamtbetrag", f"{totals['gross']} €"]]
    if credit > 0: totals_rows += [["Verrechnung Kundenguthaben", f"- {credit:.2f} €"], ["Noch zu überweisen", f"{bank_due:.2f} €"]]
    tt = Table(totals_rows, colWidths=[55*mm, 30*mm], hAlign="RIGHT"); tt.setStyle(TableStyle([("FONT", (0,0), (-1,-2), "Helvetica", 9), ("FONT", (0,-1), (-1,-1), "Helvetica-Bold", 10), ("ALIGN", (1,0), (1,-1), "RIGHT"), ("LINEABOVE", (0,2), (-1,2), .7, colors.black), ("TOPPADDING", (0,0), (-1,-1), 4)])); flow += [tt, Spacer(1, 5 * mm)]
    flow.append(Paragraph(f"<b>Zahlungsbedingungen:</b> {html.escape(row.get('payment_terms',''))}", normal))
    if seller.get("iban"):
        flow.append(Paragraph(f"Bank: {html.escape(seller.get('bank',''))} · IBAN {html.escape(seller['iban'])}" + (f" · BIC {html.escape(seller.get('bic',''))}" if seller.get("bic") else ""), small))
        payload = _epc_qr_payload(row, bank_due)
        if payload:
            qr_table = Table([[_epc_qr_drawing(payload), Paragraph(f"<b>Per Banking-App bezahlen</b><br/>Betrag: {bank_due:.2f} {html.escape(row['currency'])}<br/>Verwendungszweck: Rechnung {html.escape(row['invoice_number'])}", small)]], colWidths=[38*mm, 90*mm], hAlign="LEFT")
            qr_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("LEFTPADDING", (0,0), (-1,-1), 0), ("BOX", (0,0), (-1,-1), .25, colors.HexColor("#bbbbbb"))]))
            flow += [Spacer(1, 3*mm), qr_table]
    if seller.get("vat_id") or seller.get("tax_number"): flow.append(Paragraph(" · ".join(filter(None, [f"USt-IdNr. {html.escape(seller.get('vat_id',''))}" if seller.get('vat_id') else "", f"Steuernr. {html.escape(seller.get('tax_number',''))}" if seller.get('tax_number') else ""])), small))
    doc.build(flow); return target.getvalue()


def _credit_note_amounts(row: dict[str, Any], gross_amount: Decimal) -> dict[str, Any]:
    original_gross = Decimal(row["totals"]["gross"])
    if gross_amount <= 0 or gross_amount > original_gross:
        raise ValueError("credit note amount must be positive and not exceed invoice total")
    groups = list(row["totals"].get("vat_groups", {}).items())
    if not groups:
        return {"net": f"{gross_amount:.2f}", "tax": "0.00", "gross": f"{gross_amount:.2f}", "vat_groups": {}}
    remaining = gross_amount; result: dict[str, dict[str, str]] = {}; net_sum = Decimal("0"); tax_sum = Decimal("0")
    for index, (rate_text, values) in enumerate(groups):
        rate = Decimal(rate_text); group_gross = Decimal(values["basis"]) + Decimal(values["tax"])
        allocated_gross = remaining if index == len(groups) - 1 else (gross_amount * group_gross / original_gross).quantize(MONEY, rounding=ROUND_HALF_UP)
        remaining -= allocated_gross
        basis = (allocated_gross / (Decimal("1") + rate / Decimal("100"))).quantize(MONEY, rounding=ROUND_HALF_UP)
        tax = allocated_gross - basis; net_sum += basis; tax_sum += tax
        result[rate_text] = {"basis": f"{basis:.2f}", "tax": f"{tax:.2f}", "gross": f"{allocated_gross:.2f}"}
    return {"net": f"{net_sum:.2f}", "tax": f"{tax_sum:.2f}", "gross": f"{gross_amount:.2f}", "vat_groups": result}


def _credit_note_pdf(row: dict[str, Any], note: dict[str, Any]) -> bytes:
    target = io.BytesIO(); doc = _ContentDocTemplate(target); styles = getSampleStyleSheet(); normal = ParagraphStyle("CreditNormal", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12); title = ParagraphStyle("CreditTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=16)
    amounts = note["amounts"]; flow: list[Any] = [Paragraph("Gutschrift / Rechnungskorrektur", title), Spacer(1, 4*mm), Paragraph(f"Gutschriftnummer: <b>{html.escape(note['credit_note_number'])}</b><br/>Datum: {html.escape(note['issue_date'])}<br/>Bezug: Rechnung {html.escape(row['invoice_number'])} vom {html.escape(row['issue_date'])}", normal), Spacer(1, 4*mm), Paragraph("Empfänger:<br/>" + "<br/>".join(html.escape(item) for item in row["buyer"]["label"].splitlines()), normal), Spacer(1, 5*mm), Paragraph(f"Grund: {html.escape(note['reason'])}", normal), Spacer(1, 5*mm)]
    rows = [["Steuersatz", "Netto", "Umsatzsteuer", "Brutto"]]
    for rate, values in amounts["vat_groups"].items(): rows.append([f"{rate} %", f"{values['basis']} {row['currency']}", f"{values['tax']} {row['currency']}", f"{values['gross']} {row['currency']}"])
    rows.append(["Gesamt", f"{amounts['net']} {row['currency']}", f"{amounts['tax']} {row['currency']}", f"{amounts['gross']} {row['currency']}"])
    table = Table(rows, colWidths=[35*mm, 40*mm, 45*mm, 40*mm]); table.setStyle(TableStyle([("FONT", (0,0), (-1,0), "Helvetica-Bold", 9), ("FONT", (0,-1), (-1,-1), "Helvetica-Bold", 9), ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#eeeeee")), ("GRID", (0,0), (-1,-1), .25, colors.HexColor("#bbbbbb")), ("ALIGN", (1,1), (-1,-1), "RIGHT")]))
    flow += [table, Spacer(1, 5*mm), Paragraph("Diese Rechnungskorrektur mindert die Forderung aus der genannten Ursprungsrechnung. Die ursprüngliche Rechnung bleibt unverändert und nachvollziehbar.", normal)]
    doc.build(flow); return target.getvalue()


def create_credit_note(root: Path, invoice_id: str, amount: Any, reason: str, actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = invoice(root, invoice_id); value = _money(amount, "credit note amount"); reason = reason.strip()
    if row.get("status") in {"draft", "finalizing"}: raise ValueError("a credit note cannot be created before an invoice is finalized")
    if not reason: raise ValueError("credit note reason is required")
    already = sum((_money(item.get("gross", "0")) for item in row.get("credit_notes", [])), Decimal("0"))
    if value > Decimal(row["totals"]["gross"]) - already: raise ValueError("credit note amount exceeds remaining invoice total")
    before = invoice_state(row); note = {"credit_note_id": uuid.uuid4().hex, "credit_note_number": _credit_note_number(root), "invoice_id": invoice_id, "invoice_number": row["invoice_number"], "contact_id": row["contact_id"], "issue_date": date.today().isoformat(), "reason": reason, "currency": row["currency"], "amounts": _credit_note_amounts(row, value), "gross": f"{value:.2f}", "created_at": utc_now(), "created_by": actor}
    tpl = active_template(root, row.get("template_id", "")); pdf = _merge_content_with_template(root, tpl, _credit_note_pdf(row, note)); document = _store_generated_pdf(root, row["contact_id"], f"Gutschrift-{note['credit_note_number']}", pdf, actor, "credit_note", tpl["template_id"], metadata={"credit_note_id": note["credit_note_id"], "credit_note_number": note["credit_note_number"], "original_invoice_id": invoice_id, "original_invoice_number": row["invoice_number"], "credit_note_gross": note["gross"], "credit_note_tax": note["amounts"]["tax"]})
    note["document_id"] = document["document_id"]
    path = _invoice_store_path(root, invoice_id)
    with exclusive_file_lock(path.with_suffix(".lock")):
        current = invoice(root, invoice_id); current.setdefault("credit_notes", []).append(note); current.setdefault("history", []).append({"type": "credit_note_created", "at": utc_now(), "actor": actor, "credit_note_id": note["credit_note_id"], "gross": note["gross"]}); current["updated_at"] = utc_now(); current["updated_by"] = actor; atomic_json_write(path, current)
    surplus = max(Decimal("0"), value - Decimal(before["outstanding"]))
    if surplus > 0:
        CustomerCreditLedger(root).add(row["contact_id"], surplus, kind="credit_note", tax_treatment="outside_scope", actor=actor, note=f"Guthaben aus {note['credit_note_number']}", reference=row["invoice_number"], currency=row["currency"], related_invoice_id=invoice_id)
    DocumentStore(root).history.record("credit_note_created", actor, "invoice", invoice_id, {"credit_note_id": note["credit_note_id"], "credit_note_number": note["credit_note_number"], "gross": note["gross"], "tax": note["amounts"]["tax"], "document_id": document["document_id"]})
    return note, document


def _cii_xml(row: dict[str, Any]) -> bytes:
    ns = {"rsm":"urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100", "ram":"urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100", "udt":"urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"}
    for prefix, uri in ns.items(): ET.register_namespace(prefix, uri)
    def q(prefix: str, name: str) -> str: return f"{{{ns[prefix]}}}{name}"
    root = ET.Element(q("rsm","CrossIndustryInvoice")); context = ET.SubElement(root,q("rsm","ExchangedDocumentContext")); parameter=ET.SubElement(context,q("ram","GuidelineSpecifiedDocumentContextParameter")); ET.SubElement(parameter,q("ram","ID")).text="urn:cen.eu:en16931:2017"
    doc=ET.SubElement(root,q("rsm","ExchangedDocument")); ET.SubElement(doc,q("ram","ID")).text=row["invoice_number"]; ET.SubElement(doc,q("ram","TypeCode")).text="380"; issue=ET.SubElement(doc,q("ram","IssueDateTime")); date_string=ET.SubElement(issue,q("udt","DateTimeString"),{"format":"102"}); date_string.text=row["issue_date"].replace("-","")
    trans=ET.SubElement(root,q("rsm","SupplyChainTradeTransaction"))
    for line in row["lines"]:
        li=ET.SubElement(trans,q("ram","IncludedSupplyChainTradeLineItem")); assoc=ET.SubElement(li,q("ram","AssociatedDocumentLineDocument")); ET.SubElement(assoc,q("ram","LineID")).text=str(line["line_id"]); product=ET.SubElement(li,q("ram","SpecifiedTradeProduct")); ET.SubElement(product,q("ram","Name")).text=line.get("object_name") or line["description"]; ET.SubElement(product,q("ram","Description")).text=line["description"]
        agreement=ET.SubElement(li,q("ram","SpecifiedLineTradeAgreement")); price=ET.SubElement(agreement,q("ram","NetPriceProductTradePrice")); ET.SubElement(price,q("ram","ChargeAmount")).text=line["net_unit_price"]; basis=ET.SubElement(price,q("ram","BasisQuantity"),{"unitCode":"C62"}); basis.text="1"
        delivery=ET.SubElement(li,q("ram","SpecifiedLineTradeDelivery")); qty=ET.SubElement(delivery,q("ram","BilledQuantity"),{"unitCode":"C62"}); qty.text=line["quantity"]
        settlement=ET.SubElement(li,q("ram","SpecifiedLineTradeSettlement")); tax=ET.SubElement(settlement,q("ram","ApplicableTradeTax")); ET.SubElement(tax,q("ram","TypeCode")).text="VAT"; ET.SubElement(tax,q("ram","CategoryCode")).text="S" if Decimal(line["vat_rate"]) > 0 else "Z"; ET.SubElement(tax,q("ram","RateApplicablePercent")).text=line["vat_rate"]; summ=ET.SubElement(settlement,q("ram","SpecifiedTradeSettlementLineMonetarySummation")); ET.SubElement(summ,q("ram","LineTotalAmount")).text=line["net_total"]
    agreement=ET.SubElement(trans,q("ram","ApplicableHeaderTradeAgreement")); seller=ET.SubElement(agreement,q("ram","SellerTradeParty")); ET.SubElement(seller,q("ram","Name")).text=row["seller"]["name"]; saddr=ET.SubElement(seller,q("ram","PostalTradeAddress")); ET.SubElement(saddr,q("ram","PostcodeCode")).text=row["seller"]["postal"]; ET.SubElement(saddr,q("ram","LineOne")).text=row["seller"]["street"]; ET.SubElement(saddr,q("ram","CityName")).text=row["seller"]["city"]; ET.SubElement(saddr,q("ram","CountryID")).text=row["seller"]["country"]
    if row["seller"].get("vat_id"): taxreg=ET.SubElement(seller,q("ram","SpecifiedTaxRegistration")); ident=ET.SubElement(taxreg,q("ram","ID"),{"schemeID":"VA"}); ident.text=row["seller"]["vat_id"]
    buyer=ET.SubElement(agreement,q("ram","BuyerTradeParty")); ET.SubElement(buyer,q("ram","Name")).text=row["buyer"]["name"]; baddr=ET.SubElement(buyer,q("ram","PostalTradeAddress")); ET.SubElement(baddr,q("ram","PostcodeCode")).text=row["buyer"]["postal"]; ET.SubElement(baddr,q("ram","LineOne")).text=row["buyer"]["street"]; ET.SubElement(baddr,q("ram","CityName")).text=row["buyer"]["city"]; ET.SubElement(baddr,q("ram","CountryID")).text=row["buyer"]["country"]
    delivery=ET.SubElement(trans,q("ram","ApplicableHeaderTradeDelivery")); event=ET.SubElement(delivery,q("ram","ActualDeliverySupplyChainEvent")); when=ET.SubElement(event,q("ram","OccurrenceDateTime")); ds=ET.SubElement(when,q("udt","DateTimeString"),{"format":"102"}); ds.text=row["service_date"].replace("-","")
    settlement=ET.SubElement(trans,q("ram","ApplicableHeaderTradeSettlement")); ET.SubElement(settlement,q("ram","InvoiceCurrencyCode")).text=row["currency"]
    for rate, amounts in row["totals"]["vat_groups"].items():
        tax=ET.SubElement(settlement,q("ram","ApplicableTradeTax")); ET.SubElement(tax,q("ram","CalculatedAmount")).text=amounts["tax"]; ET.SubElement(tax,q("ram","TypeCode")).text="VAT"; ET.SubElement(tax,q("ram","BasisAmount")).text=amounts["basis"]; ET.SubElement(tax,q("ram","CategoryCode")).text="S" if Decimal(rate)>0 else "Z"; ET.SubElement(tax,q("ram","RateApplicablePercent")).text=rate
    if row["seller"].get("iban"):
        means=ET.SubElement(settlement,q("ram","SpecifiedTradeSettlementPaymentMeans")); ET.SubElement(means,q("ram","TypeCode")).text="58"; account=ET.SubElement(means,q("ram","PayeePartyCreditorFinancialAccount")); ET.SubElement(account,q("ram","IBANID")).text=row["seller"]["iban"]
        if row["seller"].get("bic"): inst=ET.SubElement(means,q("ram","PayeeSpecifiedCreditorFinancialInstitution")); ET.SubElement(inst,q("ram","BICID")).text=row["seller"]["bic"]
    terms=ET.SubElement(settlement,q("ram","SpecifiedTradePaymentTerms")); ET.SubElement(terms,q("ram","Description")).text=row.get("payment_terms",""); due=ET.SubElement(terms,q("ram","DueDateDateTime")); due_ds=ET.SubElement(due,q("udt","DateTimeString"),{"format":"102"}); due_ds.text=row["due_date"].replace("-","")
    sums=ET.SubElement(settlement,q("ram","SpecifiedTradeSettlementHeaderMonetarySummation")); ET.SubElement(sums,q("ram","LineTotalAmount")).text=row["totals"]["net"]; ET.SubElement(sums,q("ram","TaxBasisTotalAmount")).text=row["totals"]["net"]; tax_total=ET.SubElement(sums,q("ram","TaxTotalAmount"),{"currencyID":row["currency"]}); tax_total.text=row["totals"]["tax"]; ET.SubElement(sums,q("ram","GrandTotalAmount")).text=row["totals"]["gross"]; ET.SubElement(sums,q("ram","DuePayableAmount")).text=row["totals"]["due"]
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _pdfa3_convert_detailed(pdf: bytes) -> tuple[bytes, dict[str, Any]]:
    gs = shutil.which("gs")
    if not gs: return pdf, {"status": "ghostscript_unavailable", "exit_code": None, "stdout": "", "stderr": ""}
    profiles = [Path("/usr/share/color/icc/ghostscript/srgb.icc"), Path("/usr/share/ghostscript/iccprofiles/srgb.icc")]
    icc = next((path for path in profiles if path.is_file()), None)
    if icc is None:
        for base in Path("/usr/share/ghostscript").glob("*/iccprofiles/srgb.icc"):
            if base.is_file(): icc = base; break
    if icc is None: return pdf, {"status": "icc_profile_unavailable", "exit_code": None, "stdout": "", "stderr": ""}
    with tempfile.TemporaryDirectory(prefix="simpleoffice-pdfa-") as temp:
        work=Path(temp); source=work/"input.pdf"; target=work/"output.pdf"; definition=work/"PDFA_def.ps"; source.write_bytes(pdf)
        definition.write_text(f"[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark\n[{{icc_PDFA}} << /N 3 >> /PUT pdfmark\n[{{icc_PDFA}} ({str(icc)}) (r) file /PUT pdfmark\n[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark\n[{{OutputIntent_PDFA}} << /Type /OutputIntent /S /GTS_PDFA1 /DestOutputProfile {{icc_PDFA}} /OutputConditionIdentifier (sRGB) >> /PUT pdfmark\n[{{Catalog}} << /OutputIntents [{{OutputIntent_PDFA}}] >> /PUT pdfmark\n", encoding="utf-8")
        command=[gs,"-dPDFA=3","-dBATCH","-dNOPAUSE","-dNOOUTERSAVE",f"--permit-file-read={icc}","-sDEVICE=pdfwrite","-sColorConversionStrategy=RGB","-dPDFACompatibilityPolicy=1",f"-sOutputFile={target}",str(definition),str(source)]
        try:
            result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,check=False,text=True)
        except subprocess.TimeoutExpired as exc:
            stderr=str(exc.stderr or "")
            logger.error("Ghostscript PDF/A conversion timed out: %s", stderr)
            return pdf, {"status": "ghostscript_pdfa_timeout", "exit_code": None, "stdout": str(exc.stdout or ""), "stderr": stderr}
        details={"status": "pdfa3_created", "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        if result.returncode != 0 or not target.is_file():
            details["status"]="ghostscript_pdfa_failed"
            logger.error("Ghostscript PDF/A conversion failed exit_code=%s stdout=%s stderr=%s", result.returncode, result.stdout, result.stderr)
            return pdf, details
        return target.read_bytes(), details


def _pdfa3_convert(pdf: bytes) -> tuple[bytes, str]:
    converted, details = _pdfa3_convert_detailed(pdf)
    return converted, str(details["status"])


def embed_invoice_xml(pdf: bytes, xml: bytes, filename: str = "factur-x.xml") -> bytes:
    if filename.casefold() not in ZUGFERD_FILENAMES: raise ValueError("unsupported ZUGFeRD XML filename")
    try: DefusedElementTree.fromstring(xml)
    except (ET.ParseError, DefusedXmlException) as exc: raise ValueError("invoice XML is not well formed") from exc
    reader=PdfReader(io.BytesIO(pdf)); writer=PdfWriter(); writer.clone_document_from_reader(reader); writer.add_attachment(filename, xml)
    try:
        embedded=writer._root_object[NameObject("/Names")][NameObject("/EmbeddedFiles")][NameObject("/Names")]
        filespec=embedded[-1].get_object(); filespec[NameObject("/AFRelationship")]=NameObject("/Data"); writer._root_object[NameObject("/AF")]=ArrayObject([embedded[-1]])
    except Exception: pass
    writer.add_metadata({"/Title":"Invoice", "/Subject":"ZUGFeRD/Factur-X hybrid invoice", "/ZUGFeRDVersion":"2.5.2", "/ZUGFeRDConformanceLevel":"EN16931"})
    target=io.BytesIO(); writer.write(target); return target.getvalue()


def _validate_hybrid(pdf: bytes, xml: bytes) -> dict[str, Any]:
    result={"pdfa":False,"xml":False,"validated":False,"details":[],"pdfa_exit_code":None,"xml_exit_code":None,"pdfa_output":"","xml_output":"","validator":""}
    try: DefusedElementTree.fromstring(xml); result["xml"]=True
    except (ET.ParseError, DefusedXmlException): result["details"].append("xml_not_well_formed")
    verapdf=shutil.which("verapdf")
    if verapdf:
        with tempfile.TemporaryDirectory(prefix="simpleoffice-verapdf-") as temp:
            path=Path(temp)/"invoice.pdf"; path.write_bytes(pdf)
            try: check=subprocess.run([verapdf,"--format","text",str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,check=False,text=True)
            except subprocess.TimeoutExpired as exc:
                result["details"].append("pdfa_validation_timeout"); result["pdfa_output"]=str(exc.stdout or "")+str(exc.stderr or ""); logger.error("veraPDF validation timed out: %s", result["pdfa_output"])
            else:
                output=check.stdout+check.stderr; text=output.casefold(); result["pdfa_exit_code"]=check.returncode; result["pdfa_output"]=output; result["pdfa"]=check.returncode==0 and ("compliant" in text or "passed" in text) and "not compliant" not in text
                if not result["pdfa"]: result["details"].append("pdfa_validation_failed"); logger.error("veraPDF validation failed exit_code=%s output=%s", check.returncode, output)
    else: result["details"].append("verapdf_unavailable")
    configured=os.environ.get("SIMPLEOFFICE_ZUGFERD_VALIDATOR","").strip()
    mustang_jar=Path(os.environ.get("SIMPLEOFFICE_MUSTANG_JAR", "") or Path(__file__).resolve().parents[1]/".runtime-tools"/"Mustang-CLI-2.25.0.jar")
    hybrid_validator = False
    validator_parts: list[str] = []
    if configured:
        validator_parts=shlex.split(configured); result["validator"]="configured_override"
    elif mustang_jar.is_file() and shutil.which("java"):
        validator_parts=[shutil.which("java") or "java", "-Xmx1G", "-jar", str(mustang_jar), "--action", "validate", "--source", "{pdf}", "--disable-file-logging", "--no-notices"]
        hybrid_validator=True; result["validator"]=f"mustang-{mustang_jar.stem.rsplit('-', 1)[-1]}"
    if validator_parts:
        with tempfile.TemporaryDirectory(prefix="simpleoffice-zugferd-") as temp:
            xml_path=Path(temp)/"factur-x.xml"; xml_path.write_bytes(xml); pdf_path=Path(temp)/"invoice.pdf"; pdf_path.write_bytes(pdf)
            cmd=[part.replace("{xml}",str(xml_path)).replace("{pdf}",str(pdf_path)) for part in validator_parts]
            try: check=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,check=False,text=True)
            except subprocess.TimeoutExpired as exc:
                result["xml"]=False; result["details"].append("zugferd_schema_validation_timeout"); result["xml_output"]=str(exc.stdout or "")+str(exc.stderr or ""); logger.error("ZUGFeRD validation timed out: %s", result["xml_output"])
            else:
                result["xml_exit_code"]=check.returncode; result["xml_output"]=check.stdout+check.stderr; result["xml"]=result["xml"] and check.returncode==0
                if hybrid_validator and check.returncode==0:
                    result["pdfa"]=True; result["pdfa_exit_code"]=0; result["pdfa_output"]=result["xml_output"]
                    result["details"]=[detail for detail in result["details"] if detail != "verapdf_unavailable"]
                if check.returncode!=0: result["details"].append("zugferd_schema_validation_failed"); logger.error("ZUGFeRD validation failed exit_code=%s output=%s", check.returncode, result["xml_output"])
    else: result["details"].append("en16931_default_validator_unavailable")
    result["validated"]=bool(result["pdfa"] and result["xml"] and validator_parts)
    return result


def _zugferd_status(pdfa_status: str, validation: dict[str, Any]) -> str:
    if pdfa_status != "pdfa3_created":
        return "pdfa_failed"
    return "validated" if validation.get("validated") else "validation_failed"

# Export private helpers as well: business_documents is the compatibility
# facade and existing direct imports must keep working after the split.
__all__ = [name for name in globals() if not name.startswith("__")]
