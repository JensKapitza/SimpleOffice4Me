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

import html
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, Frame, ListFlowable, ListItem, PageTemplate, Paragraph, Spacer, Table, TableStyle

from .auth import login_required
from .contact_extensions import ContactCRMStore
from .contact_store import ContactStore
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .object_store import ObjectStore

bp = Blueprint("business_documents", __name__, url_prefix="/documents/business")
TEMPLATE_DIR = "business-templates"
LINK_FILE = "contact-document-links.json"
SETTINGS_FILE = "business-document-settings.json"
INVOICE_DIR = "invoices"
INVOICE_SEQUENCE = "invoice-sequence.json"
ZUGFERD_FILENAMES = {"factur-x.xml", "zugferd-invoice.xml", "zugferd.xml"}
MONEY = Decimal("0.01")
QTY = Decimal("0.001")
SUPPORTED_TEMPLATE_SUFFIXES = {".pdf", ".odt", ".ott", ".doc", ".docx", ".rtf", ".odp", ".ppt", ".pptx"}


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
    defaults = {"seller_name": "", "seller_street": "", "seller_postal": "", "seller_city": "", "seller_country": "DE", "seller_email": "", "seller_vat_id": "", "seller_tax_number": "", "seller_iban": "", "seller_bic": "", "seller_bank": "", "payment_terms": "Zahlbar ohne Abzug", "default_payment_days": "14", "currency": "EUR", "zugferd_profile": "EN16931", "zugferd_version": "2.5.2", "require_zugferd_validation": False}
    stored = _read_json(root / CONTROL_DIR / SETTINGS_FILE, {})
    if isinstance(stored, dict): defaults.update(stored)
    return defaults


def save_business_settings(root: Path, values: dict[str, Any], actor: str) -> dict[str, Any]:
    settings = business_settings(root)
    for key in ("seller_name", "seller_street", "seller_postal", "seller_city", "seller_country", "seller_email", "seller_vat_id", "seller_tax_number", "seller_iban", "seller_bic", "seller_bank", "payment_terms", "currency", "zugferd_profile"):
        settings[key] = str(values.get(key, settings.get(key, ""))).strip()
    try: days = int(str(values.get("default_payment_days", settings.get("default_payment_days", "14"))).strip())
    except ValueError as exc: raise ValueError("default payment days must be an integer") from exc
    if not 0 <= days <= 3650: raise ValueError("default payment days must be between 0 and 3650")
    settings["default_payment_days"] = str(days); settings["seller_country"] = (settings["seller_country"] or "DE").upper()[:2]; settings["currency"] = (settings["currency"] or "EUR").upper()[:3]
    settings["zugferd_version"] = "2.5.2"; settings["require_zugferd_validation"] = str(values.get("require_zugferd_validation", "")).casefold() in {"1", "true", "yes", "on"}
    path = root / CONTROL_DIR / SETTINGS_FILE; path.parent.mkdir(parents=True, exist_ok=True); atomic_json_write(path, settings)
    DocumentStore(root).history.record("business_settings_updated", actor, "business-settings", "default", {key: value for key, value in settings.items() if "iban" not in key.casefold()})
    return settings


def address_labels(contact: dict[str, Any], crm: dict[str, Any], selected: str = "") -> tuple[str, list[dict[str, str]]]:
    candidates: list[dict[str, str]] = []
    for index, item in enumerate(crm.get("addresses", [])):
        if not isinstance(item, dict): continue
        street, postal, city = (str(item.get(key, "")).strip() for key in ("street", "postal", "city")); country = str(item.get("country", "")).strip().upper()
        if not any((street, postal, city)): continue
        address_type = str(item.get("type", "other")).strip() or "other"
        label = "\n".join(filter(None, [str(contact.get("fields", {}).get("company", "")).strip(), str(contact.get("fields", {}).get("display_name", "")).strip(), street, " ".join(filter(None, (postal, city))), country if country and country != "DE" else ""]))
        candidates.append({"id": f"crm-{index}", "type": address_type, "label": label, "street": street, "postal": postal, "city": city, "country": country or "DE"})
    for index, item in enumerate(contact.get("addresses", [])):
        value = str(item.get("value", "")).strip()
        if value: candidates.append({"id": f"contact-{index}", "type": str(item.get("label", "Adresse")), "label": value, "street": value, "postal": "", "city": "", "country": "DE"})
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
    def __init__(self, target, *, recipient: str = "", subject: str = "", top_margin: float = 55 * mm):
        super().__init__(target, pagesize=A4, leftMargin=25 * mm, rightMargin=20 * mm, topMargin=top_margin, bottomMargin=24 * mm)
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


def _cover_overlay(title: str, recipient: str) -> bytes:
    target = io.BytesIO(); c = canvas.Canvas(target, pagesize=A4); c.setFont("Helvetica-Bold", 24); c.drawCentredString(A4[0] / 2, A4[1] * .58, title[:100]); c.setFont("Helvetica", 12); y = A4[1] * .48
    for line in recipient.splitlines(): c.drawCentredString(A4[0] / 2, y, line[:120]); y -= 6 * mm
    c.save(); return target.getvalue()


def _number_overlay(number: int, total: int) -> bytes:
    target = io.BytesIO(); c = canvas.Canvas(target, pagesize=A4); c.setFont("Helvetica", 8.5); c.drawCentredString(A4[0] / 2, 10 * mm, f"{number} / {total}"); c.save(); return target.getvalue()


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


def _invoice_store_path(root: Path, invoice_id: str) -> Path:
    path = root / CONTROL_DIR / INVOICE_DIR; path.mkdir(parents=True, exist_ok=True); return path / f"{invoice_id}.json"


def invoice(root: Path, invoice_id: str) -> dict[str, Any]:
    row = _read_json(_invoice_store_path(root, invoice_id), {})
    if not isinstance(row, dict) or row.get("invoice_id") != invoice_id: raise ValueError("invoice not found")
    return row


def _build_invoice_lines(root: Path, form) -> list[dict[str, Any]]:
    store = ObjectStore(root); object_ids = form.getlist("line_object_id"); descriptions = form.getlist("line_description"); quantities = form.getlist("line_quantity"); nets = form.getlist("line_net_price"); vats = form.getlist("line_vat_rate")
    count = max(len(object_ids), len(descriptions), len(quantities), len(nets), len(vats)); lines: list[dict[str, Any]] = []
    for index in range(count):
        object_id = object_ids[index].strip() if index < len(object_ids) else ""; description = descriptions[index].strip() if index < len(descriptions) else ""; qty_text = quantities[index] if index < len(quantities) else "1"; net_text = nets[index] if index < len(nets) else "0"; vat_text = vats[index] if index < len(vats) else "0"
        if not object_id and not description: continue
        snapshot: dict[str, Any] = {}
        if object_id:
            try:
                obj = store.object(object_id); effective = store.invoice_effective(obj)
            except ValueError as exc: raise ValueError(f"invoice line {index + 1}: unknown catalog object") from exc
            if not effective.get("use_in_invoice"): raise ValueError(f"invoice line {index + 1}: object is not enabled for invoices")
            snapshot = {"object_id": obj["object_id"], "object_display_id": obj["display_id"], "object_name": obj["name"], "category": effective.get("category", ""), "price_group": effective.get("price_group", "")}
            description = description or effective.get("description") or obj["name"]; net_text = net_text or effective.get("net_price", "0"); vat_text = vat_text or effective.get("vat_rate", "0")
        quantity = _quantity(qty_text); net_price = _money(net_text, "net unit price")
        try: vat_rate = Decimal(str(vat_text or "0").replace(",", ".")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation as exc: raise ValueError("invalid VAT rate") from exc
        if vat_rate < 0: raise ValueError("VAT rate must not be negative")
        net_total = (net_price * quantity).quantize(MONEY, rounding=ROUND_HALF_UP); tax_total = (net_total * vat_rate / Decimal("100")).quantize(MONEY, rounding=ROUND_HALF_UP); gross_total = net_total + tax_total
        lines.append({"line_id": len(lines) + 1, **snapshot, "description": description or "Position", "quantity": format(quantity, "f"), "unit": "C62", "net_unit_price": f"{net_price:.2f}", "vat_rate": format(vat_rate, "f"), "net_total": f"{net_total:.2f}", "tax_total": f"{tax_total:.2f}", "gross_total": f"{gross_total:.2f}"})
    if not lines: raise ValueError("at least one invoice line is required")
    return lines


def _invoice_totals(lines: list[dict[str, Any]]) -> dict[str, Any]:
    net = sum((Decimal(line["net_total"]) for line in lines), Decimal("0")); tax = sum((Decimal(line["tax_total"]) for line in lines), Decimal("0")); gross = net + tax; groups: dict[str, dict[str, str]] = {}
    grouped: dict[Decimal, tuple[Decimal, Decimal]] = defaultdict(lambda: (Decimal("0"), Decimal("0")))
    for line in lines:
        rate = Decimal(line["vat_rate"]); basis, amount = grouped[rate]; grouped[rate] = (basis + Decimal(line["net_total"]), amount + Decimal(line["tax_total"]))
    for rate, (basis, amount) in grouped.items(): groups[format(rate, "f")] = {"basis": f"{basis.quantize(MONEY):.2f}", "tax": f"{amount.quantize(MONEY):.2f}"}
    return {"net": f"{net.quantize(MONEY):.2f}", "tax": f"{tax.quantize(MONEY):.2f}", "gross": f"{gross.quantize(MONEY):.2f}", "due": f"{gross.quantize(MONEY):.2f}", "vat_groups": groups}


def _invoice_content_pdf(row: dict[str, Any]) -> bytes:
    target = io.BytesIO(); doc = _ContentDocTemplate(target, top_margin=26 * mm); styles = getSampleStyleSheet(); normal = ParagraphStyle("InvoiceNormal", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12); small = ParagraphStyle("InvoiceSmall", parent=normal, fontSize=8, leading=10); title = ParagraphStyle("InvoiceTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=19)
    flow: list[Any] = [Paragraph("Rechnung", title), Spacer(1, 3 * mm)]
    buyer = row["buyer"]; seller = row["seller"]
    header = [[Paragraph("<b>Rechnung an</b><br/>" + "<br/>".join(html.escape(x) for x in buyer["label"].splitlines()), normal), Paragraph(f"<b>Rechnungsnummer:</b> {html.escape(row['invoice_number'])}<br/><b>Rechnungsdatum:</b> {html.escape(row['issue_date'])}<br/><b>Leistungsdatum:</b> {html.escape(row['service_date'])}<br/><b>Fällig:</b> {html.escape(row['due_date'])}", normal)]]
    table = Table(header, colWidths=[95 * mm, 75 * mm]); table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"), ("BOTTOMPADDING", (0,0), (-1,-1), 6)])); flow += [table, Spacer(1, 5 * mm)]
    rows = [["Pos.", "Beschreibung", "Menge", "Netto", "MwSt.", "Gesamt"]]
    for line in row["lines"]: rows.append([str(line["line_id"]), Paragraph(html.escape(line["description"]), small), line["quantity"], f"{line['net_unit_price']} €", f"{line['vat_rate']} %", f"{line['net_total']} €"])
    positions = Table(rows, repeatRows=1, colWidths=[11*mm, 83*mm, 18*mm, 24*mm, 19*mm, 25*mm]); positions.setStyle(TableStyle([("FONT", (0,0), (-1,0), "Helvetica-Bold", 8), ("FONT", (0,1), (-1,-1), "Helvetica", 8), ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#eeeeee")), ("GRID", (0,0), (-1,-1), .25, colors.HexColor("#bbbbbb")), ("VALIGN", (0,0), (-1,-1), "TOP"), ("ALIGN", (2,1), (-1,-1), "RIGHT"), ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4)])); flow += [positions, Spacer(1, 5 * mm)]
    totals = row["totals"]; totals_table = [["Nettosumme", f"{totals['net']} €"], ["Umsatzsteuer", f"{totals['tax']} €"], ["Gesamtbetrag", f"{totals['gross']} €"]]; tt = Table(totals_table, colWidths=[45*mm, 30*mm], hAlign="RIGHT"); tt.setStyle(TableStyle([("FONT", (0,0), (-1,-2), "Helvetica", 9), ("FONT", (0,-1), (-1,-1), "Helvetica-Bold", 10), ("ALIGN", (1,0), (1,-1), "RIGHT"), ("LINEABOVE", (0,-1), (-1,-1), .7, colors.black), ("TOPPADDING", (0,0), (-1,-1), 4)])); flow += [tt, Spacer(1, 5 * mm)]
    flow.append(Paragraph(f"<b>Zahlungsbedingungen:</b> {html.escape(row.get('payment_terms',''))}", normal))
    if seller.get("iban"): flow.append(Paragraph(f"Bank: {html.escape(seller.get('bank',''))} · IBAN {html.escape(seller['iban'])}" + (f" · BIC {html.escape(seller.get('bic',''))}" if seller.get("bic") else ""), small))
    if seller.get("vat_id") or seller.get("tax_number"): flow.append(Paragraph(" · ".join(filter(None, [f"USt-IdNr. {html.escape(seller.get('vat_id',''))}" if seller.get('vat_id') else "", f"Steuernr. {html.escape(seller.get('tax_number',''))}" if seller.get('tax_number') else ""])), small))
    doc.build(flow); return target.getvalue()


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


def _pdfa3_convert(pdf: bytes) -> tuple[bytes, str]:
    gs = shutil.which("gs")
    if not gs: return pdf, "ghostscript_unavailable"
    profiles = [Path("/usr/share/color/icc/ghostscript/srgb.icc"), Path("/usr/share/ghostscript/iccprofiles/srgb.icc")]
    icc = next((path for path in profiles if path.is_file()), None)
    if icc is None:
        for base in Path("/usr/share/ghostscript").glob("*/iccprofiles/srgb.icc"):
            if base.is_file(): icc = base; break
    if icc is None: return pdf, "icc_profile_unavailable"
    with tempfile.TemporaryDirectory(prefix="simpleoffice-pdfa-") as temp:
        work=Path(temp); source=work/"input.pdf"; target=work/"output.pdf"; definition=work/"PDFA_def.ps"; source.write_bytes(pdf)
        definition.write_text(f"[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark\n[{{icc_PDFA}} << /N 3 >> /PUT pdfmark\n[{{icc_PDFA}} ({str(icc)}) (r) file /PUT pdfmark\n[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark\n[{{OutputIntent_PDFA}} << /Type /OutputIntent /S /GTS_PDFA1 /DestOutputProfile {{icc_PDFA}} /OutputConditionIdentifier (sRGB) >> /PUT pdfmark\n[{{Catalog}} << /OutputIntents [{{OutputIntent_PDFA}}] >> /PUT pdfmark\n", encoding="utf-8")
        result=subprocess.run([gs,"-dPDFA=3","-dBATCH","-dNOPAUSE","-dNOOUTERSAVE","-sDEVICE=pdfwrite","-sColorConversionStrategy=RGB","-dPDFACompatibilityPolicy=1",f"-sOutputFile={target}",str(definition),str(source)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,check=False)
        if result.returncode != 0 or not target.is_file(): return pdf, "ghostscript_pdfa_failed"
        return target.read_bytes(), "pdfa3_created"


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
    result={"pdfa":False,"xml":False,"validated":False,"details":[]}
    try: ET.fromstring(xml); result["xml"]=True
    except ET.ParseError: result["details"].append("xml_not_well_formed")
    verapdf=shutil.which("verapdf")
    if verapdf:
        with tempfile.TemporaryDirectory(prefix="simpleoffice-verapdf-") as temp:
            path=Path(temp)/"invoice.pdf"; path.write_bytes(pdf); check=subprocess.run([verapdf,"--format","text",str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,check=False,text=True)
            text=(check.stdout+check.stderr).casefold(); result["pdfa"]=check.returncode==0 and ("compliant" in text or "passed" in text) and "not compliant" not in text
            if not result["pdfa"]: result["details"].append("pdfa_validation_failed")
    else: result["details"].append("verapdf_unavailable")
    validator=os.environ.get("SIMPLEOFFICE_ZUGFERD_VALIDATOR","").strip()
    if validator:
        with tempfile.TemporaryDirectory(prefix="simpleoffice-zugferd-") as temp:
            xml_path=Path(temp)/"factur-x.xml"; xml_path.write_bytes(xml); cmd=[part.replace("{xml}",str(xml_path)) for part in validator.split()]; check=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,check=False); result["xml"]=result["xml"] and check.returncode==0
            if check.returncode!=0: result["details"].append("zugferd_schema_validation_failed")
    else: result["details"].append("zugferd_2_5_2_validator_unconfigured")
    result["validated"]=bool(result["pdfa"] and result["xml"] and validator)
    return result


def _link_path(root: Path) -> Path:
    path=root/CONTROL_DIR/LINK_FILE; path.parent.mkdir(parents=True,exist_ok=True); return path


def contact_links(root: Path, contact_id: str) -> list[dict[str, Any]]:
    rows=_read_json(_link_path(root),{"links":[]}).get("links",[]); return sorted((row for row in rows if row.get("contact_id")==contact_id),key=lambda row:row.get("created_at",""),reverse=True)


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


def _create_invoice(root: Path, contact_id: str, form, actor: str) -> tuple[dict[str,Any],dict[str,Any]]:
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
    due=issue+timedelta(days=max(0,days)); number=_invoice_number(root); invoice_id=str(uuid.uuid4()); fields=contact.get("fields",{}); buyer_name=str(fields.get("company") or fields.get("display_name") or "").strip()
    row={"invoice_id":invoice_id,"invoice_number":number,"contact_id":contact_id,"issue_date":issue.isoformat(),"service_date":service.isoformat(),"due_date":due.isoformat(),"currency":str(form.get("currency") or crm.get("currency") or settings.get("currency") or "EUR").upper()[:3],"payment_terms":str(form.get("payment_terms") or crm.get("payment_terms") or settings.get("payment_terms") or "").strip(),"seller":{"name":settings["seller_name"],"street":settings["seller_street"],"postal":settings["seller_postal"],"city":settings["seller_city"],"country":settings.get("seller_country") or "DE","email":settings.get("seller_email","") ,"vat_id":settings.get("seller_vat_id","") ,"tax_number":settings.get("seller_tax_number","") ,"iban":settings.get("seller_iban","") ,"bic":settings.get("seller_bic","") ,"bank":settings.get("seller_bank","")},"buyer":{"name":buyer_name,"label":label,"street":candidate.get("street",label),"postal":candidate.get("postal","") ,"city":candidate.get("city","") ,"country":candidate.get("country") or "DE","vat_id":crm.get("vat_id","")},"lines":lines,"totals":totals,"template_id":str(form.get("template_id","")).strip(),"zugferd":{"version":"2.5.2","profile":"EN16931","status":"pending"},"created_at":utc_now(),"created_by":actor}
    tpl=active_template(root,row["template_id"]); row["template_id"]=tpl["template_id"]; visual=_merge_content_with_template(root,tpl,_invoice_content_pdf(row)); pdfa,pdfa_status=_pdfa3_convert(visual); xml=_cii_xml(row); hybrid=embed_invoice_xml(pdfa,xml,"factur-x.xml"); validation=_validate_hybrid(hybrid,xml); row["zugferd"].update({"pdfa_pipeline":pdfa_status,"validation":validation,"status":"validated" if validation["validated"] else "embedded_unvalidated"})
    if settings.get("require_zugferd_validation") and not validation["validated"]:raise ValueError("ZUGFeRD validation is required but PDF/A-3/XML validation did not pass: "+", ".join(validation.get("details",[])))
    atomic_json_write(_invoice_store_path(root,invoice_id),row); document=_store_generated_pdf(root,contact_id,f"Rechnung-{number}",hybrid,actor,"invoice",tpl["template_id"],metadata={"invoice_id":invoice_id,"invoice_number":number,"invoice_total":totals["gross"],"invoice_currency":row["currency"],"zugferd_status":row["zugferd"]["status"],"zugferd_version":"2.5.2"}); row["document_id"]=document["document_id"]; atomic_json_write(_invoice_store_path(root,invoice_id),row); DocumentStore(root).history.record("invoice_created",actor,"invoice",invoice_id,{"invoice_number":number,"contact_id":contact_id,"document_id":document["document_id"],"totals":totals,"zugferd":row["zugferd"]}); return row,document


@bp.get("/templates")
@login_required
def template_manager():return render_template("documents/business_templates.html",templates=templates(_root()),is_admin=_is_admin(),business=business_settings(_root()),libreoffice=bool(shutil.which("libreoffice") or shutil.which("soffice")),ghostscript=bool(shutil.which("gs")),verapdf=bool(shutil.which("verapdf")))

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
    crm=ContactCRMStore(root).record(contact_id);selected=request.form.get("address","") if request.method=="POST" else request.args.get("address","");address,addresses=address_labels(contact,crm,selected);settings=business_settings(root)
    if request.method=="POST":
        try:row,document=_create_invoice(root,contact_id,request.form,actor);flash(f"Rechnung {row['invoice_number']} erstellt, gespeichert und mit dem Kontakt verknüpft. ZUGFeRD: {row['zugferd']['status']}.");return redirect(url_for("documents.detail",document_id=document["document_id"]))
        except PermissionError:abort(403)
        except ValueError as exc:flash(str(exc))
    payment_days=str(crm.get("payment_days") or settings.get("default_payment_days") or "14");return render_template("documents/contact_invoice.html",contact=contact,crm=crm,address=address,addresses=addresses,templates=templates(root),business=settings,payment_days=payment_days,issue_date=date.today().isoformat(),service_date=date.today().isoformat(),links=contact_links(root,contact_id))

@bp.get("/invoices/<invoice_id>")
@login_required
def invoice_detail(invoice_id:str):
    try:row=invoice(_root(),invoice_id)
    except ValueError:abort(404)
    contact=ContactStore(_root()).get(row["contact_id"],_actor());return render_template("documents/invoice_detail.html",invoice=row,contact=contact)

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
