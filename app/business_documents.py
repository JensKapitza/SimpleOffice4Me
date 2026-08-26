"""Reusable corporate document rendering, CRM links and ZUGFeRD inspection.

Corporate design templates are three-page PDFs:
  page 1: optional cover/title page
  page 2: first content page
  page 3: continuation pages

The renderer is deliberately generic so invoices, delivery notes and lists can use the
same layout pipeline later. ZUGFeRD/Factur-X PDFs are inspected and their embedded CII
XML is made readable internally. Merely embedding XML never marks a PDF as compliant:
proper ZUGFeRD output additionally requires PDF/A-3 and schema/business-rule validation.
"""
from __future__ import annotations

import html
import io
import json
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, Frame, ListFlowable, ListItem, PageTemplate, Paragraph, Spacer

from .auth import login_required
from .contact_extensions import ContactCRMStore
from .contact_store import ContactStore
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock

bp = Blueprint("business_documents", __name__, url_prefix="/documents/business")
TEMPLATE_DIR = "business-templates"
LINK_FILE = "contact-document-links.json"
ZUGFERD_FILENAMES = {"factur-x.xml", "zugferd-invoice.xml", "zugferd.xml"}


def _root() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]).expanduser().resolve()


def _actor() -> str:
    return str(g.user["username"])


def _is_admin() -> bool:
    try:
        return bool(g.user["is_admin"])
    except (KeyError, TypeError, IndexError):
        return False


def _safe_filename(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return clean[:100] or "document"


def _template_directory(root: Path) -> Path:
    path = root / CONTROL_DIR / TEMPLATE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _template_index(root: Path) -> Path:
    return _template_directory(root) / "templates.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def templates(root: Path) -> list[dict[str, Any]]:
    rows = _read_json(_template_index(root), {"templates": []}).get("templates", [])
    return sorted(rows, key=lambda row: (not bool(row.get("active")), str(row.get("name", "")).casefold()))


def template(root: Path, template_id: str) -> dict[str, Any]:
    row = next((item for item in templates(root) if item.get("template_id") == template_id), None)
    if row is None:
        raise ValueError("unknown business document template")
    return row


def save_template(root: Path, upload, name: str, actor: str) -> dict[str, Any]:
    if not name.strip():
        raise ValueError("template name is required")
    raw = upload.read(25 * 1024 * 1024 + 1)
    if not raw or len(raw) > 25 * 1024 * 1024:
        raise ValueError("template PDF must be between 1 byte and 25 MiB")
    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError("template is not a readable PDF") from exc
    if len(reader.pages) != 3:
        raise ValueError("corporate template PDF must contain exactly three pages")
    directory = _template_directory(root)
    template_id = str(uuid.uuid4())
    pdf_name = f"{template_id}.pdf"
    (directory / pdf_name).write_bytes(raw)
    with exclusive_file_lock(directory / ".templates-write.lock"):
        payload = _read_json(_template_index(root), {"templates": []})
        row = {"template_id": template_id, "name": name.strip(), "file": pdf_name, "pages": 3, "active": not bool(payload.get("templates")), "created_at": utc_now(), "created_by": actor}
        payload.setdefault("templates", []).append(row)
        atomic_json_write(_template_index(root), payload)
    DocumentStore(root).history.record("business_template_created", actor, "business-template", template_id, row)
    return row


def set_active_template(root: Path, template_id: str, actor: str) -> None:
    directory = _template_directory(root)
    with exclusive_file_lock(directory / ".templates-write.lock"):
        payload = _read_json(_template_index(root), {"templates": []})
        found = False
        for row in payload.get("templates", []):
            row["active"] = row.get("template_id") == template_id
            found = found or row["active"]
        if not found:
            raise ValueError("unknown business document template")
        atomic_json_write(_template_index(root), payload)
    DocumentStore(root).history.record("business_template_activated", actor, "business-template", template_id, {})


def active_template(root: Path, template_id: str = "") -> dict[str, Any]:
    if template_id:
        return template(root, template_id)
    row = next((item for item in templates(root) if item.get("active")), None)
    if row is None:
        raise ValueError("no active business document template configured")
    return row


def address_labels(contact: dict[str, Any], crm: dict[str, Any], selected: str = "") -> tuple[str, list[dict[str, str]]]:
    candidates: list[dict[str, str]] = []
    for index, item in enumerate(crm.get("addresses", [])):
        if not isinstance(item, dict):
            continue
        street, postal, city = (str(item.get(key, "")).strip() for key in ("street", "postal", "city"))
        country = str(item.get("country", "")).strip().upper()
        if not any((street, postal, city)):
            continue
        address_type = str(item.get("type", "other")).strip() or "other"
        label = "\n".join(filter(None, [str(contact.get("fields", {}).get("company", "")).strip(), str(contact.get("fields", {}).get("display_name", "")).strip(), street, " ".join(filter(None, (postal, city))), country if country and country != "DE" else ""]))
        candidates.append({"id": f"crm-{index}", "type": address_type, "label": label})
    for index, item in enumerate(contact.get("addresses", [])):
        value = str(item.get("value", "")).strip()
        if value:
            candidates.append({"id": f"contact-{index}", "type": str(item.get("label", "Adresse")), "label": value})
    choice = next((item for item in candidates if item["id"] == selected), None)
    choice = choice or next((item for item in candidates if item["type"].casefold() in {"billing", "rechnung", "rechnungsadresse"}), None)
    choice = choice or (candidates[0] if candidates else None)
    return (choice["label"] if choice else ""), candidates


def _markdown_flowables(markdown: str) -> list[Any]:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("LetterBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=10.5, leading=15, spaceAfter=7)
    heading = ParagraphStyle("LetterHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=17, spaceBefore=7, spaceAfter=7)
    flow: list[Any] = []
    bullets: list[ListItem] = []

    def inline(text: str) -> str:
        value = html.escape(text)
        value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
        return re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", value)

    def flush() -> None:
        nonlocal bullets
        if bullets:
            flow.append(ListFlowable(bullets, bulletType="bullet", leftIndent=15, bulletFontName="Helvetica", bulletFontSize=8))
            flow.append(Spacer(1, 4))
            bullets = []

    for raw in markdown.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.startswith("#"):
            flush(); flow.append(Paragraph(inline(line.lstrip("#").strip()), heading))
        elif re.match(r"^\s*[-*]\s+", line):
            bullets.append(ListItem(Paragraph(inline(re.sub(r"^\s*[-*]\s+", "", line)), body)))
        elif not line.strip():
            flush(); flow.append(Spacer(1, 5))
        else:
            flush(); flow.append(Paragraph(inline(line), body))
    flush()
    return flow


class _ContentDocTemplate(BaseDocTemplate):
    def __init__(self, target, *, recipient: str, subject: str):
        super().__init__(target, pagesize=A4, leftMargin=25 * mm, rightMargin=20 * mm, topMargin=55 * mm, bottomMargin=24 * mm)
        self.recipient, self.subject = recipient, subject
        self.addPageTemplates(PageTemplate(id="content", frames=[Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="letter")], onPage=self._header))

    def _header(self, canv, doc):
        if doc.page != 1:
            return
        canv.saveState(); y = A4[1] - 24 * mm; canv.setFont("Helvetica", 9)
        for line in self.recipient.splitlines():
            canv.drawString(25 * mm, y, line[:120]); y -= 4.3 * mm
        if self.subject:
            canv.setFont("Helvetica-Bold", 11); canv.drawString(25 * mm, A4[1] - 49 * mm, self.subject[:140])
        canv.restoreState()


def _content_pdf(recipient: str, subject: str, markdown: str) -> bytes:
    target = io.BytesIO(); _ContentDocTemplate(target, recipient=recipient, subject=subject).build(_markdown_flowables(markdown)); return target.getvalue()


def _cover_overlay(title: str, recipient: str) -> bytes:
    target = io.BytesIO(); c = canvas.Canvas(target, pagesize=A4); c.setFont("Helvetica-Bold", 24); c.drawCentredString(A4[0] / 2, A4[1] * .58, title[:100]); c.setFont("Helvetica", 12); y = A4[1] * .48
    for line in recipient.splitlines(): c.drawCentredString(A4[0] / 2, y, line[:120]); y -= 6 * mm
    c.save(); return target.getvalue()


def _number_overlay(number: int, total: int) -> bytes:
    target = io.BytesIO(); c = canvas.Canvas(target, pagesize=A4); c.setFont("Helvetica", 8.5); c.drawCentredString(A4[0] / 2, 10 * mm, f"{number} / {total}"); c.save(); return target.getvalue()


def render_business_pdf(root: Path, template_row: dict[str, Any], *, recipient: str, subject: str, markdown: str, cover: bool = False) -> bytes:
    backgrounds = PdfReader(_template_directory(root) / template_row["file"])
    content = PdfReader(io.BytesIO(_content_pdf(recipient, subject, markdown)))
    writer = PdfWriter()
    if cover:
        page = backgrounds.pages[0]; page.merge_page(PdfReader(io.BytesIO(_cover_overlay(subject or "Dokument", recipient))).pages[0]); writer.add_page(page)
    for index, overlay in enumerate(content.pages):
        page = backgrounds.pages[1 if index == 0 else 2]; page.merge_page(overlay); writer.add_page(page)
    total = len(writer.pages)
    if total > 1:
        for index, page in enumerate(writer.pages, 1): page.merge_page(PdfReader(io.BytesIO(_number_overlay(index, total))).pages[0])
    result = io.BytesIO(); writer.write(result); return result.getvalue()


def _link_path(root: Path) -> Path:
    path = root / CONTROL_DIR / LINK_FILE; path.parent.mkdir(parents=True, exist_ok=True); return path


def contact_links(root: Path, contact_id: str) -> list[dict[str, Any]]:
    rows = _read_json(_link_path(root), {"links": []}).get("links", [])
    return sorted((row for row in rows if row.get("contact_id") == contact_id), key=lambda row: row.get("created_at", ""), reverse=True)


def attach_contact_document(root: Path, contact_id: str, document_id: str, actor: str, *, relation: str = "correspondence", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    path = _link_path(root)
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"links": []})
        existing = next((row for row in payload.get("links", []) if row.get("contact_id") == contact_id and row.get("document_id") == document_id), None)
        if existing: return existing
        row = {"link_id": str(uuid.uuid4()), "contact_id": contact_id, "document_id": document_id, "relation": relation, "metadata": metadata or {}, "created_at": utc_now(), "created_by": actor}
        payload.setdefault("links", []).append(row); atomic_json_write(path, payload)
    DocumentStore(root).history.record("contact_document_attached", actor, "contacts", contact_id, row)
    return row


def _xml_values(root: ET.Element) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1]
        value = (element.text or "").strip()
        if value: values.setdefault(local, []).append(value)
    return values


def inspect_zugferd_pdf(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"detected": False, "xml_filename": "", "profile": "", "invoice_id": "", "issue_date": "", "currency": "", "seller": "", "buyer": "", "grand_total": "", "tax_total": "", "due_payable": "", "raw_xml": "", "validation": "not_validated"}
    try: reader = PdfReader(path)
    except Exception: return result
    try: attachments = dict(reader.attachments)
    except Exception: attachments = {}
    for filename, payloads in attachments.items():
        if str(filename).casefold() not in ZUGFERD_FILENAMES: continue
        payload = payloads[0] if isinstance(payloads, list) and payloads else payloads
        if not isinstance(payload, (bytes, bytearray)): continue
        try:
            text = bytes(payload).decode("utf-8"); xml_root = ET.fromstring(text)
        except (UnicodeDecodeError, ET.ParseError): continue
        values = _xml_values(xml_root)
        result.update({"detected": True, "xml_filename": str(filename), "raw_xml": text, "invoice_id": (values.get("ID") or [""])[0], "issue_date": (values.get("DateTimeString") or values.get("IssueDateTime") or [""])[0], "currency": (values.get("InvoiceCurrencyCode") or [""])[0], "grand_total": (values.get("GrandTotalAmount") or [""])[0], "tax_total": (values.get("TaxTotalAmount") or [""])[0], "due_payable": (values.get("DuePayableAmount") or [""])[0]})
        names = values.get("Name", [])
        if names: result["seller"] = names[0]
        if len(names) > 1: result["buyer"] = names[1]
        for value in values.get("ID", []):
            if "urn:factur-x" in value.casefold() or "zugferd" in value.casefold(): result["profile"] = value; break
        break
    return result


def embed_invoice_xml(pdf: bytes, xml: bytes, filename: str = "factur-x.xml") -> bytes:
    """Embed invoice XML for a later PDF/A-3 validation pipeline; does not claim compliance."""
    if filename.casefold() not in ZUGFERD_FILENAMES: raise ValueError("unsupported ZUGFeRD XML filename")
    try: ET.fromstring(xml)
    except ET.ParseError as exc: raise ValueError("invoice XML is not well formed") from exc
    reader = PdfReader(io.BytesIO(pdf)); writer = PdfWriter()
    for page in reader.pages: writer.add_page(page)
    writer.add_attachment(filename, xml)
    target = io.BytesIO(); writer.write(target); return target.getvalue()


def _store_generated_pdf(root: Path, contact_id: str, subject: str, pdf: bytes, actor: str, kind: str, template_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc); directory = root / "generated" / kind / now.strftime("%Y") / contact_id; directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{now.strftime('%Y%m%d-%H%M%S')}-{_safe_filename(subject)}-{uuid.uuid4().hex[:8]}.pdf"; path.write_bytes(pdf)
    store = DocumentStore(root); store.scan(); document = store.get_document(path)
    store.set_attribute(document["document_id"], "contact_id", contact_id, actor); store.set_attribute(document["document_id"], "business_document_kind", kind, actor); store.set_attribute(document["document_id"], "business_template_id", template_id, actor); store.set_tags(document["document_id"], [kind, "crm"], author=actor)
    attach_contact_document(root, contact_id, document["document_id"], actor, relation=kind, metadata={"subject": subject, "template_id": template_id})
    return store.get_document(document["document_id"])


@bp.get("/templates")
@login_required
def template_manager(): return render_template("documents/business_templates.html", templates=templates(_root()), is_admin=_is_admin())


@bp.post("/templates")
@login_required
def upload_template():
    if not _is_admin(): abort(403)
    upload = request.files.get("template")
    if upload is None: abort(400)
    try: save_template(_root(), upload, request.form.get("name", ""), _actor()); flash("Corporate-Design-Vorlage gespeichert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".template_manager"))


@bp.post("/templates/<template_id>/activate")
@login_required
def activate_template(template_id: str):
    if not _is_admin(): abort(403)
    try: set_active_template(_root(), template_id, _actor()); flash("Vorlage aktiviert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".template_manager"))


@bp.route("/contacts/<contact_id>/letter", methods=("GET", "POST"))
@login_required
def contact_letter(contact_id: str):
    root, actor = _root(), _actor(); contacts = ContactStore(root); contact = contacts.get(contact_id, actor)
    if not contacts.can_manage(contact_id, actor): abort(403)
    crm = ContactCRMStore(root).record(contact_id); selected = request.form.get("address", "") if request.method == "POST" else request.args.get("address", ""); address, addresses = address_labels(contact, crm, selected)
    if request.method == "POST" and request.form.get("body", "").strip():
        try:
            if not address: raise ValueError("recipient address is required")
            tpl = active_template(root, request.form.get("template_id", "")); subject = request.form.get("subject", "").strip(); pdf = render_business_pdf(root, tpl, recipient=address, subject=subject, markdown=request.form.get("body", "").strip(), cover=request.form.get("cover") == "1")
            document = _store_generated_pdf(root, contact_id, subject or "Brief", pdf, actor, "letter", tpl["template_id"]); flash("Brief erzeugt und mit dem CRM-Kontakt verknüpft."); return redirect(url_for("documents.detail", document_id=document["document_id"]))
        except ValueError as exc: flash(str(exc))
    return render_template("documents/contact_letter.html", contact=contact, crm=crm, address=address, addresses=addresses, templates=templates(root), links=contact_links(root, contact_id))


@bp.post("/contacts/<contact_id>/attach")
@login_required
def attach_existing(contact_id: str):
    root, actor = _root(), _actor(); contacts = ContactStore(root)
    if not contacts.can_manage(contact_id, actor): abort(403)
    document_id = request.form.get("document_id", "").strip(); store = DocumentStore(root)
    try: document = store.get_document(document_id)
    except ValueError: abort(404)
    metadata: dict[str, Any] = {}; path = root / str(document.get("last_path", ""))
    if path.suffix.casefold() == ".pdf" and path.is_file():
        details = inspect_zugferd_pdf(path)
        if details.get("detected"):
            metadata["zugferd"] = {key: value for key, value in details.items() if key != "raw_xml"}; store.set_attribute(document_id, "zugferd_detected", "yes", actor)
            for key in ("invoice_id", "profile", "currency", "grand_total", "due_payable"):
                if details.get(key): store.set_attribute(document_id, f"zugferd_{key}", str(details[key]), actor)
    attach_contact_document(root, contact_id, document_id, actor, relation=request.form.get("relation", "correspondence"), metadata=metadata); flash("Dokument mit Kontakt verknüpft.")
    return redirect(url_for(".contact_letter", contact_id=contact_id))


@bp.get("/zugferd/<document_id>")
@login_required
def zugferd_details(document_id: str):
    root = _root(); store = DocumentStore(root)
    try: document = store.get_document(document_id)
    except ValueError: abort(404)
    return render_template("documents/zugferd_details.html", document=document, details=inspect_zugferd_pdf(root / str(document.get("last_path", ""))))
