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
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

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

bp = Blueprint("business_documents", __name__, url_prefix="/documents/business")
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
logger = logging.getLogger(__name__)
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
    try: ET.fromstring(xml)
    except ET.ParseError as exc: raise ValueError("invoice XML is not well formed") from exc
    reader=PdfReader(io.BytesIO(pdf)); writer=PdfWriter(); writer.clone_document_from_reader(reader); writer.add_attachment(filename, xml)
    try:
        embedded=writer._root_object[NameObject("/Names")][NameObject("/EmbeddedFiles")][NameObject("/Names")]
        filespec=embedded[-1].get_object(); filespec[NameObject("/AFRelationship")]=NameObject("/Data"); writer._root_object[NameObject("/AF")]=ArrayObject([embedded[-1]])
    except Exception: pass
    writer.add_metadata({"/Title":"Invoice", "/Subject":"ZUGFeRD/Factur-X hybrid invoice", "/ZUGFeRDVersion":"2.5.2", "/ZUGFeRDConformanceLevel":"EN16931"})
    target=io.BytesIO(); writer.write(target); return target.getvalue()


def _validate_hybrid(pdf: bytes, xml: bytes) -> dict[str, Any]:
    result={"pdfa":False,"xml":False,"validated":False,"details":[],"pdfa_exit_code":None,"xml_exit_code":None,"pdfa_output":"","xml_output":"","validator":""}
    try: ET.fromstring(xml); result["xml"]=True
    except ET.ParseError: result["details"].append("xml_not_well_formed")
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


def _link_path(root: Path) -> Path:
    path=root/CONTROL_DIR/LINK_FILE; path.parent.mkdir(parents=True,exist_ok=True); return path


def contact_links(root: Path, contact_id: str) -> list[dict[str, Any]]:
    rows=_read_json(_link_path(root),{"links":[]}).get("links",[]); return sorted((row for row in rows if row.get("contact_id")==contact_id),key=lambda row:row.get("created_at",""),reverse=True)


def _customer_document_rows(root: Path, contact_id: str, invoice_rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Resolve only authoritative customer/document relations, without name guessing."""
    store = DocumentStore(root)
    linked: dict[str, dict[str, Any]] = {}
    for link in contact_links(root, contact_id):
        document_id = str(link.get("document_id", "")).strip()
        if not document_id:
            continue
        entry = linked.setdefault(document_id, {"document_id": document_id, "links": [], "invoice_ids": []})
        entry["links"].append(link)
    for row in invoice_rows if invoice_rows is not None else invoices(root):
        if row.get("contact_id") != contact_id:
            continue
        document_id = str(row.get("document_id", "")).strip()
        if document_id:
            entry = linked.setdefault(document_id, {"document_id": document_id, "links": [], "invoice_ids": []})
            entry["invoice_ids"].append(str(row.get("invoice_id", "")))
    result: list[dict[str, Any]] = []
    for document_id, relation in linked.items():
        try:
            document = store.get_document(document_id)
        except ValueError:
            result.append({**relation, "available": False, "error": "document_metadata_missing"})
            continue
        candidate = root / str(document.get("last_path", ""))
        path = candidate.resolve()
        try:
            path.relative_to(root)
            safe = candidate.is_file() and not candidate.is_symlink()
        except ValueError:
            safe = False
        result.append({**relation, "document": document, "path": path,
                       "filename": Path(str(document.get("last_path", ""))).name,
                       "available": safe,
                       **({} if safe else {"error": "document_file_missing_or_unsafe"})})
    return sorted(result, key=lambda item: str(item.get("document", {}).get("last_seen_at", "")), reverse=True)


def _document_provenance(document: dict[str, Any]) -> dict[str, Any]:
    attributes = document.get("attributes", {}) if isinstance(document.get("attributes"), dict) else {}
    origin_keys = {
        "attachment_origin", "copied_from", "email_origin", "import_origin",
        "mail_origin", "source", "webdav_origin",
    }
    origins = {
        key: value for key, value in attributes.items()
        if key in origin_keys or key.endswith("_origin")
    }
    return {
        "first_seen_at": document.get("first_seen_at", ""),
        "last_seen_at": document.get("last_seen_at", ""),
        "current_storage_path": document.get("last_path", ""),
        "location_history": document.get("location_history", []),
        "content_history": document.get("content_history", []),
        "origins": origins,
        "malware_scan": attributes.get("malware_scan", {}),
    }


def _archive_member_name(document: dict[str, Any], used: set[str]) -> str:
    original = Path(str(document.get("last_path", "document"))).name
    stem = _safe_filename(Path(original).stem) or "document"
    suffix = Path(original).suffix.lower()[:20]
    candidate = f"documents/{stem}{suffix}"
    if candidate in used:
        candidate = f"documents/{stem}-{str(document.get('document_id', ''))[:8]}{suffix}"
    counter = 2
    unique = candidate
    while unique in used:
        unique = f"documents/{stem}-{counter}{suffix}"
        counter += 1
    used.add(unique)
    return unique


def customer_document_archive(root: Path, contact: dict[str, Any], actor: str) -> tuple[tempfile.SpooledTemporaryFile, dict[str, Any]]:
    """Build an auditable customer archive with files, provenance and history."""
    contact_id = str(contact["contact_id"])
    store = DocumentStore(root)
    invoice_rows = [row for row in invoices(root) if row.get("contact_id") == contact_id]
    document_rows = _customer_document_rows(root, contact_id, invoice_rows)
    if not document_rows and not invoice_rows:
        raise ValueError("customer archive is empty")

    target = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b")
    export_id, exported_at = str(uuid.uuid4()), utc_now()
    manifest_documents: list[dict[str, Any]] = []
    used_names: set[str] = set()
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for row in document_rows:
                document = row.get("document", {})
                record: dict[str, Any] = {
                    "document_id": row["document_id"],
                    "available": bool(row.get("available")),
                    "relations": row.get("links", []),
                    "invoice_ids": sorted(set(row.get("invoice_ids", []))),
                }
                if not row.get("available"):
                    record["error"] = row.get("error", "document_unavailable")
                    manifest_documents.append(record)
                    continue
                member_name = _archive_member_name(document, used_names)
                digest = hashlib.sha256()
                size = 0
                with row["path"].open("rb") as source, archive.open(member_name, "w", force_zip64=True) as destination:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                actual_sha256 = digest.hexdigest()
                record.update({
                    "archive_path": member_name,
                    "filename": Path(str(document.get("last_path", ""))).name,
                    "size": size,
                    "sha256": actual_sha256,
                    "stored_sha256": document.get("sha256", ""),
                    "hash_matches_metadata": actual_sha256 == document.get("sha256"),
                    "state": document.get("state", ""),
                    "tags": document.get("tags", []),
                    "provenance": _document_provenance(document),
                })
                archive.writestr(
                    f"audit/documents/{row['document_id']}.json",
                    json.dumps(store.logbook(row["document_id"]), ensure_ascii=False, indent=2) + "\n",
                )
                manifest_documents.append(record)

            export_record = {
                "export_id": export_id,
                "action": "customer_document_archive_exported",
                "exported_at": exported_at,
                "exported_by": actor,
                "contact_id": contact_id,
                "customer_name": contact.get("fields", {}).get("display_name", ""),
                "document_count": sum(1 for row in manifest_documents if row.get("available")),
                "unavailable_document_count": sum(1 for row in manifest_documents if not row.get("available")),
                "invoice_count": len(invoice_rows),
            }
            revision = store.history.record(
                "customer_document_archive_exported", actor, "customer-exports", export_id,
                {**export_record, "document_ids": [row["document_id"] for row in manifest_documents],
                 "invoice_ids": [str(row.get("invoice_id", "")) for row in invoice_rows]},
            )
            export_record["audit_revision"] = revision
            manifest = {
                "format": "SimpleOffice4Me customer document archive",
                "format_version": 1,
                "customer": {"contact_id": contact_id, "display_name": export_record["customer_name"]},
                "export": export_record,
                "scope": "Explicit customer-document links and invoices linked by contact_id; no name-based inference.",
                "documents": manifest_documents,
                "invoices": invoice_rows,
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("audit/export.json", json.dumps(export_record, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("invoices/invoices.json", json.dumps(invoice_rows, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("README.txt", (
                "KUNDENAKTE / CUSTOMER DOCUMENT ARCHIVE\n\n"
                "manifest.json enthält die Dokumentliste, SHA-256-Prüfsummen, Verknüpfungen und Herkunftsnachweise.\n"
                "audit/ enthält den protokollierten Export und die Historie jedes enthaltenen Dokuments.\n"
                "invoices/invoices.json enthält die gespeicherten Rechnungsdatensätze.\n"
                "Dokumente werden nur über explizite Kontaktverknüpfungen oder eine eindeutige contact_id der Rechnung zugeordnet.\n\n"
                "manifest.json contains the document list, SHA-256 checksums, relations and provenance.\n"
                "audit/ contains the recorded export and each included document's audit history.\n"
                "invoices/invoices.json contains the stored invoice records.\n"
                "Documents are included only through explicit contact links or an invoice's authoritative contact_id.\n"
            ))
        target.seek(0)
        return target, export_record
    except Exception:
        target.close()
        raise


def attach_contact_document(root: Path, contact_id: str, document_id: str, actor: str, *, relation: str="correspondence", metadata: dict[str,Any]|None=None) -> dict[str,Any]:
    path=_link_path(root)
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload=_read_json(path,{"links":[]}); existing=next((row for row in payload.get("links",[]) if row.get("contact_id")==contact_id and row.get("document_id")==document_id),None)
        if existing:return existing
        row={"link_id":str(uuid.uuid4()),"contact_id":contact_id,"document_id":document_id,"relation":relation,"metadata":metadata or {},"created_at":utc_now(),"created_by":actor}; payload.setdefault("links",[]).append(row); atomic_json_write(path,payload)
    DocumentStore(root).history.record("contact_document_attached",actor,"contacts",contact_id,row); return row


def _xml_values(root: ET.Element) -> dict[str,list[str]]:
    values:dict[str,list[str]]={}
    for element in root.iter():
        local=element.tag.rsplit("}",1)[-1]; value=(element.text or "").strip()
        if value:values.setdefault(local,[]).append(value)
    return values


def inspect_zugferd_pdf(path: Path) -> dict[str,Any]:
    result={"detected":False,"xml_filename":"","profile":"","invoice_id":"","issue_date":"","currency":"","seller":"","buyer":"","grand_total":"","tax_total":"","due_payable":"","raw_xml":"","validation":"not_validated"}
    try:reader=PdfReader(path)
    except Exception:return result
    try:attachments=dict(reader.attachments)
    except Exception:attachments={}
    for filename,payloads in attachments.items():
        if str(filename).casefold() not in ZUGFERD_FILENAMES:continue
        payload=payloads[0] if isinstance(payloads,list) and payloads else payloads
        if not isinstance(payload,(bytes,bytearray)):continue
        try:text=bytes(payload).decode("utf-8"); xml_root=ET.fromstring(text)
        except (UnicodeDecodeError,ET.ParseError):continue
        values=_xml_values(xml_root); result.update({"detected":True,"xml_filename":str(filename),"raw_xml":text,"invoice_id":(values.get("ID") or [""])[0],"issue_date":(values.get("DateTimeString") or [""])[0],"currency":(values.get("InvoiceCurrencyCode") or [""])[0],"grand_total":(values.get("GrandTotalAmount") or [""])[0],"tax_total":(values.get("TaxTotalAmount") or [""])[0],"due_payable":(values.get("DuePayableAmount") or [""])[0]}); names=values.get("Name",[])
        if names:result["seller"]=names[0]
        if len(names)>1:result["buyer"]=names[1]
        result["profile"]="EN16931"; break
    return result


def _store_generated_pdf(root: Path, contact_id: str, subject: str, pdf: bytes, actor: str, kind: str, template_id: str, *, metadata: dict[str,Any]|None=None) -> dict[str,Any]:
    now=datetime.now(timezone.utc); directory=root/"generated"/kind/now.strftime("%Y")/contact_id; directory.mkdir(parents=True,exist_ok=True); path=directory/f"{now.strftime('%Y%m%d-%H%M%S')}-{_safe_filename(subject)}-{uuid.uuid4().hex[:8]}.pdf"; path.write_bytes(pdf)
    store=DocumentStore(root); store.scan(); document=store.get_document(path); store.set_attribute(document["document_id"],"contact_id",contact_id,actor); store.set_attribute(document["document_id"],"business_document_kind",kind,actor); store.set_attribute(document["document_id"],"business_template_id",template_id,actor); store.set_tags(document["document_id"],[kind,"crm"],author=actor)
    for key,value in (metadata or {}).items():
        if value is not None and not isinstance(value,(dict,list)):store.set_attribute(document["document_id"],str(key),str(value),actor)
    attach_contact_document(root,contact_id,document["document_id"],actor,relation=kind,metadata={"subject":subject,"template_id":template_id,**(metadata or {})}); return store.get_document(document["document_id"])


def _invoice_row_from_form(root: Path, contact_id: str, form, actor: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    contacts=ContactStore(root); contact=contacts.get(contact_id,actor)
    if not contacts.can_manage(contact_id,actor):raise PermissionError
    crm=ContactCRMStore(root).record(contact_id); selected=form.get("address",""); label,candidates=address_labels(contact,crm,selected); candidate=next((item for item in candidates if item["label"]==label),None)
    if not candidate:raise ValueError("billing address is required")
    settings=business_settings(root)
    for required in ("seller_name","seller_street","seller_postal","seller_city"):
        if not settings.get(required):raise ValueError("invoice issuer data is incomplete; configure business document settings first")
    lines=_build_invoice_lines(root,form); totals=_invoice_totals(lines); issue_text=form.get("issue_date","") or date.today().isoformat(); service_text=form.get("service_date","") or issue_text
    try:issue=date.fromisoformat(issue_text); service=date.fromisoformat(service_text)
    except ValueError as exc:raise ValueError("invoice/service date must be valid ISO dates") from exc
    try:days=int(form.get("payment_days",crm.get("payment_days") or settings.get("default_payment_days","14")) or 0)
    except ValueError as exc:raise ValueError("payment days must be an integer") from exc
    due=issue+timedelta(days=max(0,days)); invoice_id=str(existing.get("invoice_id")) if existing else str(uuid.uuid4()); fields=contact.get("fields",{}); contact_names=_recipient_names(contact)
    recipient_type=str(form.get("recipient_type") or ("company" if fields.get("company") else "private")).strip().casefold()
    if recipient_type not in {"private","company"}: raise ValueError("recipient type must be private or company")
    company=str(form.get("recipient_company","")).strip(); person=str(form.get("recipient_contact","")).strip()
    if recipient_type == "private" and not person: raise ValueError("a private invoice requires a recipient name")
    if recipient_type == "company" and not company: raise ValueError("a company invoice requires a company name")
    known_names={str(value).strip().casefold() for value in (*contact_names,fields.get("display_name",""),fields.get("company","")) if str(value).strip()}
    address_lines=[line.strip() for line in label.splitlines() if line.strip() and line.strip().casefold() not in known_names]
    recipient_lines=[person] if recipient_type == "private" else [company,*([person] if person else [])]
    buyer_label="\n".join(recipient_lines+address_lines); buyer_name=person if recipient_type == "private" else company
    now=utc_now(); row={"invoice_id":invoice_id,"invoice_number":existing.get("invoice_number") if existing else _draft_invoice_number(root,issue),"contact_id":contact_id,"issue_date":issue.isoformat(),"service_date":service.isoformat(),"due_date":due.isoformat(),"currency":str(form.get("currency") or crm.get("currency") or settings.get("currency") or "EUR").upper()[:3],"payment_terms":str(form.get("payment_terms") or crm.get("payment_terms") or settings.get("payment_terms") or "").strip(),"seller":{"name":settings["seller_name"],"street":settings["seller_street"],"postal":settings["seller_postal"],"city":settings["seller_city"],"country":settings.get("seller_country") or "DE","email":settings.get("seller_email","") ,"vat_id":settings.get("seller_vat_id","") ,"tax_number":settings.get("seller_tax_number","") ,"iban":settings.get("seller_iban","") ,"bic":settings.get("seller_bic","") ,"bank":settings.get("seller_bank","")},"buyer":{"name":buyer_name,"label":buyer_label,"recipient_type":recipient_type,"company":company if recipient_type == "company" else "","contact_name":person,"address_id":candidate["id"],"street":candidate.get("street",label),"postal":candidate.get("postal","") ,"city":candidate.get("city","") ,"country":candidate.get("country") or "DE","vat_id":crm.get("vat_id","")},"lines":lines,"totals":totals,"status":"draft","payments":[],"history":list(existing.get("history",[])) if existing else [],"template_id":str(form.get("template_id","")).strip(),"zugferd":{"version":"2.5.2","profile":"EN16931","status":"not_created"},"created_at":existing.get("created_at",now) if existing else now,"created_by":existing.get("created_by",actor) if existing else actor,"updated_at":now,"updated_by":actor}
    available_credit = max(Decimal("0"), Decimal(CustomerCreditLedger(root).account(contact_id, row["currency"])["balance"]))
    planned_credit = min(Decimal(totals["gross"]), available_credit).quantize(MONEY)
    row["settlement"] = {"customer_credit": f"{planned_credit:.2f}", "bank_due": f"{(Decimal(totals['gross']) - planned_credit).quantize(MONEY):.2f}"}
    tpl=active_template(root,row["template_id"]); row["template_id"]=tpl["template_id"]
    row["history"].append({"type":"draft_updated" if existing else "draft_created","at":now,"actor":actor})
    return row


def save_invoice_draft(root: Path, contact_id: str, form, actor: str, invoice_id: str = "") -> dict[str, Any]:
    path = _invoice_store_path(root, invoice_id) if invoice_id else None
    lock_path = path.with_suffix(".lock") if path else root / CONTROL_DIR / INVOICE_DIR / ".create.lock"
    with exclusive_file_lock(lock_path):
        existing = invoice(root, invoice_id) if invoice_id else None
        if existing and (existing.get("status") != "draft" or existing.get("contact_id") != contact_id): raise ValueError("only a matching invoice draft can be edited")
        row = _invoice_row_from_form(root, contact_id, form, actor, existing)
        atomic_json_write(_invoice_store_path(root,row["invoice_id"]),row)
    DocumentStore(root).history.record("invoice_draft_saved",actor,"invoice",row["invoice_id"],{"contact_id":contact_id,"totals":row["totals"]})
    return row


def _draft_watermark(pdf: bytes) -> bytes:
    reader=PdfReader(io.BytesIO(pdf)); writer=PdfWriter()
    for source in reader.pages:
        overlay=io.BytesIO(); c=canvas.Canvas(overlay,pagesize=A4); c.saveState(); c.setFillColor(colors.Color(.75,.1,.1,alpha=.18)); c.setFont("Helvetica-Bold",42); c.translate(A4[0]/2,A4[1]/2); c.rotate(35); c.drawCentredString(0,0,"ENTWURF / DRAFT"); c.setFont("Helvetica-Bold",12); c.drawCentredString(0,-18*mm,"KEINE RECHNUNG / NOT AN INVOICE"); c.restoreState(); c.save(); overlay.seek(0); writer.add_page(source); writer.pages[-1].merge_page(PdfReader(overlay).pages[0])
    target=io.BytesIO(); writer.write(target); return target.getvalue()


def draft_invoice_pdf(root: Path, row: dict[str, Any]) -> bytes:
    if row.get("status") != "draft": raise ValueError("invoice is not a draft")
    tpl=active_template(root,row.get("template_id","")); visual=_merge_content_with_template(root,tpl,_invoice_content_pdf(row)); return _draft_watermark(visual)


def finalize_invoice(root: Path, invoice_id: str, actor: str) -> tuple[dict[str,Any],dict[str,Any]]:
    path = _invoice_store_path(root, invoice_id)
    timings: dict[str, float] = {}
    started_total = time.perf_counter()

    def timed(step: str, operation):
        started = time.perf_counter()
        try:
            return operation()
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            timings[step] = elapsed_ms
            log = logger.warning if elapsed_ms > 500 else logger.debug
            log("invoice_finalization invoice_id=%s step=%s duration_ms=%.1f", invoice_id, step, elapsed_ms)

    with exclusive_file_lock(path.with_suffix(".lock"), blocking=False) as acquired, exclusive_file_lock(root / CONTROL_DIR / ".project-billing.lock"):
        if not acquired:
            raise ValueError("invoice finalization is already in progress")
        row = timed("invoice_load", lambda: invoice(root, invoice_id))
        if row.get("status") != "draft":
            raise ValueError("invoice is already finalized")
        timed("contact_load", lambda: ContactStore(root).get(row["contact_id"], actor) if row.get("contact_id") else None)
        timed("project_positions_load", lambda: _validate_project_sources(root, row, actor))
        settings = timed("business_settings_load", lambda: business_settings(root))
        number = row.get("invoice_number", "")
        number = timed("invoice_number_assign", lambda: _invoice_number(root)) if not number or number.startswith("DRAFT-") else number
        row["invoice_number"] = number
        row["history"].append({"type": "number_assigned", "at": utc_now(), "actor": actor, "invoice_number": number})
        try:
            tpl = timed("template_load", lambda: active_template(root, row["template_id"]))
            content_pdf = timed("content_pdf_render", lambda: _invoice_content_pdf(row))
            visual = timed("template_pdf_render", lambda: _merge_content_with_template(root, tpl, content_pdf))
            pdfa, pdfa_details = timed("ghostscript_pdfa_convert", lambda: _pdfa3_convert_detailed(visual))
            row["zugferd"].update({"pdfa_pipeline": pdfa_details["status"], "pdfa_details": pdfa_details})
            try:
                xml = timed("zugferd_xml_generate", lambda: _cii_xml(row))
            except Exception:
                row["zugferd"]["status"] = "xml_generation_failed"
                raise
            try:
                hybrid = timed("zugferd_xml_embed", lambda: embed_invoice_xml(pdfa, xml, "factur-x.xml"))
            except Exception:
                row["zugferd"]["status"] = "embedding_failed"
                raise
            validation = timed("zugferd_validate", lambda: _validate_hybrid(hybrid, xml))
            technical_status = _zugferd_status(str(pdfa_details["status"]), validation)
            row["zugferd"].update({"validation": validation, "status": technical_status})
            if technical_status != "validated":
                raise ValueError("ZUGFeRD validation is required but PDF/A-3/XML validation did not pass: " + ", ".join(validation.get("details", [])))
            document = timed(
                "final_file_save_and_contact_link",
                lambda: _store_generated_pdf(
                    root, row["contact_id"], f"Rechnung-{number}", hybrid, actor, "invoice", tpl["template_id"],
                    metadata={"invoice_id": invoice_id, "invoice_number": number, "invoice_total": row["totals"]["gross"], "invoice_currency": row["currency"], "zugferd_status": technical_status, "zugferd_version": "2.5.2"},
                ),
            )
        except Exception as exc:
            row["status"] = "draft"
            row["finalization_error"] = str(exc)
            row["finalization_timings_ms"] = timings
            row["history"].append({"type": "finalization_failed", "at": utc_now(), "actor": actor, "error": str(exc)[:500]})
            atomic_json_write(path, row)
            raise
        row.pop("finalization_error", None)
        row["document_id"] = document["document_id"]
        row["status"] = "open"
        row["history"].append({"type": "issued", "at": utc_now(), "actor": actor})
        timed("invoice_and_project_links_save", lambda: atomic_json_write(path, row))
        timed("audit_history_save", lambda: DocumentStore(root).history.record("invoice_created", actor, "invoice", invoice_id, {"invoice_number": number, "contact_id": row["contact_id"], "document_id": document["document_id"], "totals": row["totals"], "zugferd": row["zugferd"]}))
        row["finalization_timings_ms"] = timings
        atomic_json_write(path, row)
    if Decimal(row.get("settlement", {}).get("customer_credit", "0")) > 0:
        row = timed("customer_credit_apply", lambda: apply_available_customer_credit(root, invoice_id, actor))
    total_ms = round((time.perf_counter() - started_total) * 1000, 1)
    row["finalization_timings_ms"] = {**timings, "total": total_ms}
    logger.info("invoice_finalization invoice_id=%s total_ms=%.1f status=%s timings=%s", invoice_id, total_ms, row["zugferd"]["status"], timings)
    return row, document


def _create_invoice(root: Path, contact_id: str, form, actor: str) -> tuple[dict[str,Any],dict[str,Any]]:
    draft=save_invoice_draft(root,contact_id,form,actor)
    return finalize_invoice(root,draft["invoice_id"],actor)


@bp.get("/templates")
@login_required
def template_manager():return render_template("documents/business_templates.html",templates=templates(_root()),is_admin=_is_admin(),business=business_settings(_root()),libreoffice=bool(shutil.which("libreoffice") or shutil.which("soffice")),ghostscript=bool(shutil.which("gs")),verapdf=bool(shutil.which("verapdf")))

@bp.get("/templates/din5008-guide.pdf")
@login_required
def din5008_template_guide():
    return send_file(io.BytesIO(din5008_template_guide_pdf()), mimetype="application/pdf", as_attachment=True, download_name="SimpleOffice-DIN5008-Vorlagenmuster.pdf")

@bp.post("/templates")
@login_required
def upload_template():
    if not _is_admin():abort(403)
    upload=request.files.get("template")
    if upload is None:abort(400)
    try:save_template(_root(),upload,request.form.get("name",""),_actor());flash("Corporate-Design-Vorlage gespeichert.")
    except ValueError as exc:flash(str(exc))
    return redirect(url_for(".template_manager"))

@bp.post("/templates/settings")
@login_required
def update_business_settings():
    if not _is_admin():abort(403)
    try:save_business_settings(_root(),request.form.to_dict(),_actor());flash("Rechnungssteller- und Dokumenteinstellungen gespeichert.")
    except ValueError as exc:flash(str(exc))
    return redirect(url_for(".template_manager"))

@bp.post("/templates/<template_id>/activate")
@login_required
def activate_template(template_id:str):
    if not _is_admin():abort(403)
    try:set_active_template(_root(),template_id,_actor());flash("Vorlage aktiviert.")
    except ValueError as exc:flash(str(exc))
    return redirect(url_for(".template_manager"))

@bp.get("/objects/invoice-catalog.json")
@login_required
def invoice_object_catalog():
    query=request.args.get("q","").strip(); store=ObjectStore(_root())
    if request.args.get("categories")=="1":
        items=[]
        for item in store.invoice_categories():
            if query and query.casefold() not in f"{item.get('display_id','')} {item.get('name','')} {item.get('invoice',{}).get('category','')}".casefold():continue
            effective=store.invoice_effective(item);items.append({"object_id":item["object_id"],"id":item["display_id"],"name":item["name"],"vat_rate":effective.get("default_vat_rate") or effective.get("vat_rate","") ,"net_price":effective.get("default_net_price") or effective.get("net_price","") ,"gross_price":effective.get("default_gross_price") or effective.get("gross_price","") ,"price_group":effective.get("default_price_group") or effective.get("price_group","")})
            if len(items)>=20:break
        return jsonify({"items":items})
    return jsonify({"items":store.invoice_candidates(query,20)})


@bp.get("/projects/invoice-candidates.json")
@login_required
def project_invoice_candidates():
    root,actor=_root(),_actor(); store=ProjectStore(root); billed=_billed_project_sources(root); items=[]
    for project in store.projects():
        if project.get("status") == "cancelled":continue
        projection=store.billing_projection(project["project_id"],actor)
        for line in projection["lines"]:
            ref=(project["project_id"],str(line["source_type"]),str(line["source_id"]))
            if ref in billed:continue
            minutes=int(line.get("minutes",0)); quantity=(Decimal(minutes)/Decimal(60)).quantize(QTY).normalize()
            items.append({"project_id":project["project_id"],"project_title":project["title"],"source_type":line["source_type"],"source_id":line["source_id"],"description":line["description"],"minutes":minutes,"quantity":format(quantity,"f"),"category":"","net_price":"","vat_rate":"19"})
    return jsonify({"items":items})


@bp.get("/contacts/<contact_id>/appointment-invoice-candidates.json")
@login_required
def appointment_invoice_candidates(contact_id: str):
    root, actor = _root(), _actor()
    contacts = ContactStore(root)
    if not contacts.can_manage(contact_id, actor):
        abort(403)
    billed = _billed_appointment_sources(root)
    items = []
    for event in CalendarStore(root).events(actor):
        billing = event.get("billing", {}) if isinstance(event.get("billing"), dict) else {}
        if event.get("contact_id") != contact_id or event.get("event_id") in billed or not billing.get("billable"):
            continue
        if event.get("status", "active") in {"cancelled", "deleted", "moved"}:
            continue
        appointment_type = str(event.get("appointment_type") or "").strip()
        description = str(billing.get("description") or "").strip()
        if not description:
            prefix = f"{appointment_type}: " if appointment_type else "Termin: "
            description = f"{prefix}{event.get('title', 'Leistung')} ({str(event.get('start', ''))[:10]})"
        items.append({
            "source_type": "calendar_event",
            "source_id": event["event_id"],
            "event_id": event["event_id"],
            "event_start": event.get("start", ""),
            "appointment_type": appointment_type,
            "attendance": event.get("attendance", ""),
            "description": description,
            "quantity": billing.get("quantity", "1"),
            "net_price": billing.get("net_price", "0.00"),
            "vat_rate": billing.get("vat_rate", "19"),
            "currency": billing.get("currency", "EUR"),
            "category": "Termin",
        })
    return jsonify({"items": sorted(items, key=lambda item: item["event_start"], reverse=True)})

@bp.route("/contacts/<contact_id>/letter",methods=("GET","POST"))
@login_required
def contact_letter(contact_id:str):
    root,actor=_root(),_actor();contacts=ContactStore(root);contact=contacts.get(contact_id,actor)
    if not contacts.can_manage(contact_id,actor):abort(403)
    crm=ContactCRMStore(root).record(contact_id);selected=request.form.get("address","") if request.method=="POST" else request.args.get("address","");address,addresses=address_labels(contact,crm,selected)
    if request.method=="POST" and request.form.get("body","").strip():
        try:
            if not address:raise ValueError("recipient address is required")
            tpl=active_template(root,request.form.get("template_id",""));subject=request.form.get("subject","").strip();pdf=render_business_pdf(root,tpl,recipient=address,subject=subject,markdown=request.form.get("body","").strip(),cover=request.form.get("cover")=="1");document=_store_generated_pdf(root,contact_id,subject or "Brief",pdf,actor,"letter",tpl["template_id"]);flash("Brief erzeugt und mit dem CRM-Kontakt verknüpft.");return redirect(url_for("documents.detail",document_id=document["document_id"]))
        except ValueError as exc:flash(str(exc))
    return render_template("documents/contact_letter.html",contact=contact,crm=crm,address=address,addresses=addresses,templates=templates(root),links=contact_links(root,contact_id))

@bp.route("/contacts/<contact_id>/invoice",methods=("GET","POST"))
@login_required
def contact_invoice(contact_id:str):
    root,actor=_root(),_actor();contacts=ContactStore(root)
    try:contact=contacts.get(contact_id,actor)
    except ValueError:abort(404)
    if not contacts.can_manage(contact_id,actor):abort(403)
    draft_id=request.form.get("invoice_id","").strip() if request.method=="POST" else request.args.get("invoice_id","").strip(); draft=None
    if draft_id:
        try:draft=invoice(root,draft_id)
        except ValueError:abort(404)
        if draft.get("contact_id")!=contact_id or draft.get("status")!="draft":abort(409)
    crm=ContactCRMStore(root).record(contact_id);selected=request.form.get("address","") if request.method=="POST" else (draft.get("buyer",{}).get("address_id","") if draft else request.args.get("address",""));address,addresses=address_labels(contact,crm,selected);settings=business_settings(root)
    if request.method=="POST":
        try:
            row=save_invoice_draft(root,contact_id,request.form,actor,draft_id)
            if request.form.get("action")=="finalize":
                row,document=finalize_invoice(root,row["invoice_id"],actor)
                if row["zugferd"]["status"] == "validated":
                    message = f"Invoice {row['invoice_number']} finalized, linked and technically validated." if g.language == "en" else f"Rechnung {row['invoice_number']} finalisiert, verknüpft und technisch validiert."
                else:
                    message = f"Invoice {row['invoice_number']} was created, but technical validation failed: {row['zugferd']['status']}." if g.language == "en" else f"Rechnung {row['invoice_number']} wurde erzeugt, aber die technische Validierung ist fehlgeschlagen: {row['zugferd']['status']}."
                flash(message);return redirect(url_for(".invoice_detail",invoice_id=row["invoice_id"]))
            flash("Rechnungsentwurf schnell gespeichert. Es wurde noch keine endgültige Rechnungsnummer vergeben.");return redirect(url_for(".invoice_detail",invoice_id=row["invoice_id"]))
        except PermissionError:abort(403)
        except ValueError as exc:flash(str(exc))
    fields=contact.get("fields",{}); default_names=_recipient_names(contact); default_company=str(fields.get("company","")).strip(); default_person=next((name for name in default_names if name.casefold()!=default_company.casefold()),"")
    recipient={"type":draft.get("buyer",{}).get("recipient_type") if draft else ("company" if default_company else "private"),"company":draft.get("buyer",{}).get("company","") if draft else default_company,"contact":draft.get("buyer",{}).get("contact_name","") if draft else (default_person or str(fields.get("display_name","")).strip())}
    if request.method=="POST":recipient={"type":request.form.get("recipient_type","private"),"company":request.form.get("recipient_company",""),"contact":request.form.get("recipient_contact","")}
    payment_days=str((date.fromisoformat(draft["due_date"])-date.fromisoformat(draft["issue_date"])).days) if draft else str(crm.get("payment_days") or settings.get("default_payment_days") or "14");return render_template("documents/contact_invoice.html",contact=contact,crm=crm,address=address,addresses=addresses,templates=templates(root),business=settings,payment_days=payment_days,issue_date=draft.get("issue_date",date.today().isoformat()) if draft else date.today().isoformat(),service_date=draft.get("service_date",date.today().isoformat()) if draft else date.today().isoformat(),links=contact_links(root,contact_id),draft=draft,recipient=recipient)


@bp.get("/invoices/<invoice_id>/draft.pdf")
@login_required
def invoice_draft_preview(invoice_id: str):
    root,actor=_root(),_actor()
    try:row=invoice(root,invoice_id)
    except ValueError:abort(404)
    if not ContactStore(root).can_manage(row["contact_id"],actor):abort(403)
    try:pdf=draft_invoice_pdf(root,row)
    except ValueError:abort(409)
    return send_file(io.BytesIO(pdf),as_attachment=False,download_name=f"{_safe_filename(row['invoice_number'])}.pdf",mimetype="application/pdf")


@bp.get("/contacts/<contact_id>/billing")
@login_required
def customer_billing(contact_id: str):
    root, actor = _root(), _actor(); contacts = ContactStore(root)
    try: contact = contacts.get(contact_id, actor)
    except ValueError: abort(404)
    if not contacts.can_manage(contact_id, actor): abort(403)
    rows = [row for row in invoices(root) if row.get("contact_id") == contact_id]
    candidates = [item for item in contacts.contacts(actor) if item.get("contact_id") != contact_id and contacts.can_manage(item["contact_id"], actor)]
    customer_documents = _customer_document_rows(root, contact_id, rows)
    return render_template("documents/customer_billing.html", contact=contact, rows=rows,
                           customer_documents=customer_documents,
                           credit=CustomerCreditLedger(root).account(contact_id),
                           referrals=CustomerCreditLedger(root).referrals(contact_id),
                           candidates=candidates, today=date.today().isoformat())


@bp.post("/contacts/<contact_id>/credits")
@login_required
def add_customer_credit(contact_id: str):
    root, actor = _root(), _actor()
    if not ContactStore(root).can_manage(contact_id, actor): abort(403)
    try:
        CustomerCreditLedger(root).add(contact_id, request.form.get("amount", ""),
            kind=request.form.get("kind", "topup"), tax_treatment=request.form.get("tax_treatment", ""),
            actor=actor, note=request.form.get("note", ""), reference=request.form.get("reference", ""),
            currency=request.form.get("currency", "EUR"), related_contact_id=request.form.get("related_contact_id", ""))
        flash("Kundenguthaben wurde revisionssicher gebucht.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".customer_billing", contact_id=contact_id))


@bp.post("/contacts/<contact_id>/referrals")
@login_required
def add_customer_referral(contact_id: str):
    root, actor = _root(), _actor(); referred_id = request.form.get("referred_id", "").strip(); contacts = ContactStore(root)
    if not contacts.can_manage(contact_id, actor) or not contacts.can_manage(referred_id, actor): abort(403)
    try:
        ledger = CustomerCreditLedger(root); ledger.add_referral(contact_id, referred_id, actor, request.form.get("note", ""))
        reward = request.form.get("reward_amount", "").strip()
        if reward:
            ledger.add(contact_id, reward, kind="referral", tax_treatment=request.form.get("tax_treatment", "manual_review"), actor=actor, note=f"Prämie für geworbenen Kunden {referred_id}", related_contact_id=referred_id)
        flash("Kundenwerbung wurde gespeichert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".customer_billing", contact_id=contact_id))


@bp.post("/contacts/<contact_id>/credits/refund")
@login_required
def refund_customer_credit(contact_id: str):
    root, actor = _root(), _actor()
    if not ContactStore(root).can_manage(contact_id, actor): abort(403)
    try:
        CustomerCreditLedger(root).refund(contact_id, request.form.get("amount", ""), actor=actor,
            reference=request.form.get("reference", ""), note=request.form.get("note", ""), currency=request.form.get("currency", "EUR"))
        flash("Guthabenauszahlung wurde protokolliert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".customer_billing", contact_id=contact_id))


def _invoice_pdf_path(root: Path, row: dict[str, Any]) -> Path:
    document = DocumentStore(root).get_document(row.get("document_id", ""))
    path = (root / str(document.get("last_path", ""))).resolve()
    try: path.relative_to(root)
    except ValueError as exc: raise ValueError("invoice PDF is outside document storage") from exc
    if not path.is_file(): raise ValueError("invoice PDF not found")
    return path


@bp.get("/invoices/<invoice_id>/download")
@login_required
def invoice_download(invoice_id: str):
    root, actor = _root(), _actor()
    try: row = invoice(root, invoice_id)
    except ValueError: abort(404)
    if not ContactStore(root).can_manage(row["contact_id"], actor): abort(403)
    if row.get("status") == "draft":
        return send_file(io.BytesIO(draft_invoice_pdf(root,row)),as_attachment=True,download_name=f"{_safe_filename(row['invoice_number'])}.pdf",mimetype="application/pdf")
    try: path = _invoice_pdf_path(root, row)
    except ValueError: abort(404)
    return send_file(path, as_attachment=True, download_name=f"Rechnung-{_safe_filename(row['invoice_number'])}.pdf", mimetype="application/pdf")


@bp.get("/contacts/<contact_id>/invoices.zip")
@login_required
def customer_invoice_archive(contact_id: str):
    root, actor = _root(), _actor()
    if not ContactStore(root).can_manage(contact_id, actor): abort(403)
    target = io.BytesIO(); count = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for row in invoices(root):
            if row.get("contact_id") != contact_id: continue
            try: path = _invoice_pdf_path(root, row)
            except ValueError: continue
            archive.writestr(f"Rechnung-{_safe_filename(row['invoice_number'])}.pdf", path.read_bytes()); count += 1
    if count == 0: abort(404)
    target.seek(0)
    return send_file(target, as_attachment=True, download_name=f"Rechnungen-{_safe_filename(contact_id)}.zip", mimetype="application/zip")


@bp.get("/contacts/<contact_id>/customer-documents.zip")
@login_required
def customer_document_archive_download(contact_id: str):
    root, actor = _root(), _actor()
    contacts = ContactStore(root)
    try:
        contact = contacts.get(contact_id, actor)
    except ValueError:
        abort(404)
    if not contacts.can_manage(contact_id, actor):
        abort(403)
    try:
        target, _summary = customer_document_archive(root, contact, actor)
    except ValueError:
        abort(404)
    response = send_file(
        target, as_attachment=True,
        download_name=f"Kundenakte-{_safe_filename(contact.get('fields', {}).get('display_name', contact_id))}.zip",
        mimetype="application/zip", conditional=False,
    )
    response.call_on_close(target.close)
    return response

@bp.get("/invoices")
@login_required
def invoice_overview():
    root,actor=_root(),_actor();contacts=ContactStore(root);query=request.args.get("q","").strip().casefold();selected_status=request.args.get("status","").strip()
    rows=[]
    for row in invoices(root):
        try:contact=contacts.get(row["contact_id"],actor)
        except ValueError:continue
        if not contacts.can_manage(row["contact_id"],actor):continue
        state=row["payment_state"]["status"]
        if selected_status and state!=selected_status:continue
        searchable=f"{row.get('invoice_number','')} {row.get('buyer',{}).get('name','')} {contact.get('fields',{}).get('display_name','')}"
        if query and query not in searchable.casefold():continue
        rows.append({**row,"contact":contact})
    stats={"total":len(rows),"open":sum(row["payment_state"]["status"] in {"open","partial"} for row in rows),"overdue":sum(row["payment_state"]["status"]=="overdue" for row in rows),"paid":sum(row["payment_state"]["status"] in {"paid","credited"} for row in rows),"written_off":sum(row["payment_state"]["status"]=="written_off" for row in rows)}
    return render_template("documents/invoice_overview.html",rows=rows,stats=stats,query=request.args.get("q","").strip(),selected_status=selected_status)

@bp.get("/invoices/<invoice_id>")
@login_required
def invoice_detail(invoice_id:str):
    try:row=invoice(_root(),invoice_id)
    except ValueError:abort(404)
    contacts=ContactStore(_root());contact=contacts.get(row["contact_id"],_actor())
    if not contacts.can_manage(row["contact_id"],_actor()):abort(403)
    return render_template("documents/invoice_detail.html",invoice={**row,"payment_state":invoice_state(row)},contact=contact,today=date.today().isoformat(),credit=CustomerCreditLedger(_root()).account(row["contact_id"],row.get("currency","EUR")),write_off_reasons=sorted(WRITE_OFF_REASONS))

@bp.post("/invoices/<invoice_id>/payments")
@login_required
def invoice_payment(invoice_id:str):
    root,actor=_root(),_actor()
    try:row=invoice(root,invoice_id)
    except ValueError:abort(404)
    if not ContactStore(root).can_manage(row["contact_id"],actor):abort(403)
    try:record_invoice_payment(root,invoice_id,request.form,actor);flash(translate(g.language,"invoice.payment.saved"))
    except ValueError as exc:
        keys={"invoice is already paid":"invoice.payment.error.paid","payment amount must be positive and not exceed the outstanding amount":"invoice.payment.error.amount","payment date must be a valid ISO date":"invoice.payment.error.date"}
        flash(translate(g.language,keys.get(str(exc),"invoice.payment.error.default")))
    return redirect(url_for(".invoice_detail",invoice_id=invoice_id))


@bp.post("/invoices/<invoice_id>/write-off")
@login_required
def invoice_write_off(invoice_id: str):
    root, actor = _root(), _actor()
    try:
        row = invoice(root, invoice_id)
    except ValueError:
        abort(404)
    if not ContactStore(root).can_manage(row["contact_id"], actor):
        abort(403)
    error_keys = {
        "a draft invoice cannot be written off": "writeoff.error.draft",
        "invoice has no collectible outstanding amount": "writeoff.error.no_outstanding",
        "write-off reason is invalid": "writeoff.error.reason",
        "a note is required for another write-off reason": "writeoff.error.note",
        "write-off amount must be positive and not exceed the collectible outstanding amount": "writeoff.error.amount",
        "write-off date must be a valid ISO date": "writeoff.error.date",
        "write-off date cannot precede the invoice date": "writeoff.error.date_before_invoice",
        "stopping collection requires writing off the full collectible outstanding amount": "writeoff.error.stop_requires_full",
    }
    try:
        write_off_invoice(root, invoice_id, request.form, actor)
        flash(translate(g.language, "writeoff.saved"))
    except ValueError as exc:
        flash(translate(g.language, error_keys.get(str(exc), "writeoff.error")))
    return redirect(url_for(".invoice_detail", invoice_id=invoice_id))


@bp.post("/invoices/<invoice_id>/apply-credit")
@login_required
def invoice_apply_credit(invoice_id: str):
    root, actor = _root(), _actor()
    try: row = invoice(root, invoice_id)
    except ValueError: abort(404)
    if not ContactStore(root).can_manage(row["contact_id"], actor): abort(403)
    try:
        updated = apply_available_customer_credit(root, invoice_id, actor)
        amount = updated.get("credit_applied", "0.00")
        flash("Kundenguthaben wurde auf die Rechnung angewendet." if amount != "0.00" else "Kein verrechenbares Kundenguthaben vorhanden.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".invoice_detail", invoice_id=invoice_id))


@bp.post("/invoices/<invoice_id>/credit-notes")
@login_required
def invoice_credit_note(invoice_id: str):
    root, actor = _root(), _actor()
    try: row = invoice(root, invoice_id)
    except ValueError: abort(404)
    if not ContactStore(root).can_manage(row["contact_id"], actor): abort(403)
    try:
        note, _document = create_credit_note(root, invoice_id, request.form.get("amount", ""), request.form.get("reason", ""), actor)
        flash(f"Gutschrift {note['credit_note_number']} wurde erstellt.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".invoice_detail", invoice_id=invoice_id))

@bp.post("/contacts/<contact_id>/attach")
@login_required
def attach_existing(contact_id:str):
    root,actor=_root(),_actor();contacts=ContactStore(root)
    if not contacts.can_manage(contact_id,actor):abort(403)
    document_id=request.form.get("document_id","").strip();store=DocumentStore(root)
    try:document=store.get_document(document_id)
    except ValueError:abort(404)
    metadata:dict[str,Any]={};path=root/str(document.get("last_path",""))
    if path.suffix.casefold()==".pdf" and path.is_file():
        details=inspect_zugferd_pdf(path)
        if details.get("detected"):
            metadata["zugferd"]={key:value for key,value in details.items() if key!="raw_xml"};store.set_attribute(document_id,"zugferd_detected","yes",actor)
            for key in ("invoice_id","profile","currency","grand_total","due_payable"):
                if details.get(key):store.set_attribute(document_id,f"zugferd_{key}",str(details[key]),actor)
    attach_contact_document(root,contact_id,document_id,actor,relation=request.form.get("relation","correspondence"),metadata=metadata);flash("Dokument mit Kontakt verknüpft.");return redirect(url_for(".contact_letter",contact_id=contact_id))

@bp.get("/zugferd/<document_id>")
@login_required
def zugferd_details(document_id:str):
    root=_root();store=DocumentStore(root)
    try:document=store.get_document(document_id)
    except ValueError:abort(404)
    return render_template("documents/zugferd_details.html",document=document,details=inspect_zugferd_pdf(root/str(document.get("last_path",""))))
