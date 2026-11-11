"""In-app visual business template editor and persistent layout store.

The visual editor deliberately uses its own index next to the existing three-page
PDF/Office corporate templates. This keeps the current production renderer fully
backwards compatible until visual layouts are connected to PDF generation.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from flask import abort, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock

VISUAL_TEMPLATE_DIR = "business-templates/visual"
VISUAL_TEMPLATE_INDEX = "visual-templates.json"
VISUAL_TEMPLATE_SCHEMA = 1
DOCUMENT_TYPES = {"invoice", "quote", "delivery_note", "letter"}
REGIONS = {"header", "content", "footer", "margin_left", "margin_right"}
ELEMENT_TYPES = {"text", "token", "line", "rect"}
ALIGNMENTS = {"left", "center", "right", "justify"}
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
TOKEN_PATTERN = re.compile(r"\{\{[a-z0-9_.-]{1,80}\}\}", re.IGNORECASE)
MAX_ELEMENTS = 160
MAX_TEXT = 4000

TOKEN_CATALOG = (
    ("firma.name", "Firmenname"),
    ("firma.anschrift", "Anschrift"),
    ("firma.ansprechpartner", "Ansprechpartner"),
    ("firma.email", "E-Mail"),
    ("firma.iban", "IBAN"),
    ("firma.bic", "BIC"),
    ("firma.bank", "Bank"),
    ("firma.steuer_nr", "Steuer-Nr."),
    ("firma.ust_idnr", "USt-IdNr."),
    ("rechnung.nummer", "Rechnungsnummer"),
    ("rechnung.datum", "Rechnungsdatum"),
    ("rechnung.zahlungsziel", "Zahlungsziel"),
    ("kunde.name", "Kundenname"),
    ("kunde.anschrift", "Kundenanschrift"),
    ("datum.heute", "Heutiges Datum"),
    ("freitext1", "Freitext 1"),
    ("freitext2", "Freitext 2"),
    ("freitext3", "Freitext 3"),
)


def _directory(root: Path) -> Path:
    path = root / CONTROL_DIR / VISUAL_TEMPLATE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path(root: Path) -> Path:
    return _directory(root) / VISUAL_TEMPLATE_INDEX


def _layout_path(root: Path, template_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", template_id):
        raise ValueError("invalid visual template identifier")
    return _directory(root) / f"{template_id}.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(default)


def visual_templates(root: Path) -> list[dict[str, Any]]:
    rows = _read_json(_index_path(root), {"templates": []}).get("templates", [])
    if not isinstance(rows, list):
        return []
    return sorted(
        (row for row in rows if isinstance(row, dict)),
        key=lambda row: (str(row.get("document_type", "")), str(row.get("name", "")).casefold()),
    )


def _style(**overrides: Any) -> dict[str, Any]:
    base = {
        "font_size": 10.0,
        "font_family": "Helvetica",
        "bold": False,
        "italic": False,
        "underline": False,
        "color": "#111827",
        "align": "left",
    }
    base.update(overrides)
    return base


def _element(
    element_id: str,
    element_type: str,
    region: str,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str = "",
    **style: Any,
) -> dict[str, Any]:
    return {
        "id": element_id,
        "type": element_type,
        "region": region,
        "x_mm": x,
        "y_mm": y,
        "width_mm": width,
        "height_mm": height,
        "text": text,
        "style": _style(**style),
        "z_index": 1,
        "locked": False,
        "visible": True,
    }


def default_visual_layout(document_type: str = "invoice") -> dict[str, Any]:
    document_type = document_type if document_type in DOCUMENT_TYPES else "invoice"
    title = {
        "invoice": "Rechnung",
        "quote": "Angebot",
        "delivery_note": "Lieferschein",
        "letter": "Brief",
    }[document_type]
    elements = [
        _element("company-name", "token", "header", 18, 12, 95, 9, "{{firma.name}}", font_size=15, bold=True),
        _element("company-address", "token", "header", 18, 23, 105, 12, "{{firma.anschrift}}", font_size=9),
        _element("contact", "token", "header", 132, 20, 60, 12, "Ansprechpartner:\n{{firma.ansprechpartner}}", font_size=8),
        _element("recipient", "token", "content", 18, 55, 86, 24, "{{kunde.name}}\n{{kunde.anschrift}}", font_size=9),
        _element("doc-meta", "token", "content", 118, 55, 74, 26,
                 "Nr.: {{rechnung.nummer}}\nDatum: {{rechnung.datum}}\nZahlungsziel: {{rechnung.zahlungsziel}}", font_size=8),
        _element("title", "text", "content", 18, 88, 100, 10, title, font_size=17, bold=True),
        _element("free-1", "token", "content", 18, 118, 174, 12, "{{freitext1}}", font_size=9),
        _element("free-2", "token", "content", 18, 145, 174, 16, "{{freitext2}}", font_size=9),
        _element("free-3", "token", "content", 18, 205, 174, 14, "{{freitext3}}", font_size=9),
        _element("bank", "token", "footer", 18, 263, 80, 22,
                 "IBAN: {{firma.iban}}\nBIC: {{firma.bic}}\nBank: {{firma.bank}}", font_size=7.5),
        _element("tax", "token", "footer", 112, 263, 80, 22,
                 "Steuer-Nr.: {{firma.steuer_nr}}\nUSt-IdNr.: {{firma.ust_idnr}}", font_size=7.5),
    ]
    return {
        "schema_version": VISUAL_TEMPLATE_SCHEMA,
        "page": {"format": "A4", "width_mm": 210, "height_mm": 297},
        "regions": {
            "header": {"enabled": True, "y_mm": 8, "height_mm": 38},
            "content": {"enabled": True, "y_mm": 48, "height_mm": 207},
            "footer": {"enabled": True, "y_mm": 257, "height_mm": 32},
            "margin_left": {"enabled": False, "width_mm": 8},
            "margin_right": {"enabled": False, "width_mm": 8},
        },
        "free_text": {
            "freitext1": "Ihre Referenz / Betreff",
            "freitext2": "Vielen Dank für Ihren Auftrag.",
            "freitext3": "Zahlung bitte bis zum {{rechnung.zahlungsziel}}.",
        },
        "elements": elements,
    }


def _number(value: Any, *, minimum: float, maximum: float, name: str) -> float:
    try:
        number = round(float(value), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} is outside the allowed range")
    return number


def _validate_style(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    color = str(raw.get("color", "#111827"))
    if not HEX_COLOR.fullmatch(color):
        raise ValueError("element color must use #RRGGBB")
    align = str(raw.get("align", "left"))
    if align not in ALIGNMENTS:
        raise ValueError("element alignment is invalid")
    family = str(raw.get("font_family", "Helvetica")).strip()
    if family not in {"Helvetica", "Times-Roman", "Courier"}:
        family = "Helvetica"
    return {
        "font_size": _number(raw.get("font_size", 10), minimum=6, maximum=72, name="font size"),
        "font_family": family,
        "bold": bool(raw.get("bold", False)),
        "italic": bool(raw.get("italic", False)),
        "underline": bool(raw.get("underline", False)),
        "color": color.upper(),
        "align": align,
    }


def validate_visual_layout(layout: Any) -> dict[str, Any]:
    if not isinstance(layout, dict):
        raise ValueError("visual template layout must be an object")
    elements = layout.get("elements", [])
    if not isinstance(elements, list) or len(elements) > MAX_ELEMENTS:
        raise ValueError("visual template has too many elements")
    page = layout.get("page", {})
    if not isinstance(page, dict) or page.get("format", "A4") != "A4":
        raise ValueError("only A4 visual templates are supported")
    normalized_elements: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(elements):
        if not isinstance(raw, dict):
            raise ValueError("template element must be an object")
        element_id = str(raw.get("id") or f"element-{index+1}")[:80]
        if element_id in ids:
            raise ValueError("template element identifiers must be unique")
        ids.add(element_id)
        element_type = str(raw.get("type", "text"))
        region = str(raw.get("region", "content"))
        if element_type not in ELEMENT_TYPES:
            raise ValueError("unsupported template element type")
        if region not in REGIONS:
            raise ValueError("unsupported template region")
        text = str(raw.get("text", ""))
        if len(text) > MAX_TEXT:
            raise ValueError("template element text is too long")
        normalized_elements.append({
            "id": element_id,
            "type": element_type,
            "region": region,
            "x_mm": _number(raw.get("x_mm", 10), minimum=0, maximum=210, name="x"),
            "y_mm": _number(raw.get("y_mm", 10), minimum=0, maximum=297, name="y"),
            "width_mm": _number(raw.get("width_mm", 40), minimum=1, maximum=210, name="width"),
            "height_mm": _number(raw.get("height_mm", 8), minimum=1, maximum=297, name="height"),
            "text": text,
            "style": _validate_style(raw.get("style")),
            "z_index": int(_number(raw.get("z_index", 1), minimum=0, maximum=999, name="z index")),
            "locked": bool(raw.get("locked", False)),
            "visible": bool(raw.get("visible", True)),
        })
    regions_in = layout.get("regions", {}) if isinstance(layout.get("regions"), dict) else {}
    regions = {
        "header": {"enabled": bool(regions_in.get("header", {}).get("enabled", True)), "y_mm": 8, "height_mm": 38},
        "content": {"enabled": True, "y_mm": 48, "height_mm": 207},
        "footer": {"enabled": bool(regions_in.get("footer", {}).get("enabled", True)), "y_mm": 257, "height_mm": 32},
        "margin_left": {"enabled": bool(regions_in.get("margin_left", {}).get("enabled", False)), "width_mm": 8},
        "margin_right": {"enabled": bool(regions_in.get("margin_right", {}).get("enabled", False)), "width_mm": 8},
    }
    free_in = layout.get("free_text", {}) if isinstance(layout.get("free_text"), dict) else {}
    free_text = {
        key: str(free_in.get(key, ""))[:500]
        for key in ("freitext1", "freitext2", "freitext3")
    }
    return {
        "schema_version": VISUAL_TEMPLATE_SCHEMA,
        "page": {"format": "A4", "width_mm": 210, "height_mm": 297},
        "regions": regions,
        "free_text": free_text,
        "elements": normalized_elements,
    }


def create_visual_template(root: Path, values: dict[str, Any], actor: str) -> dict[str, Any]:
    name = str(values.get("name", "")).strip()
    document_type = str(values.get("document_type", "invoice")).strip()
    if not name or len(name) > 120:
        raise ValueError("template name must contain 1 to 120 characters")
    if document_type not in DOCUMENT_TYPES:
        raise ValueError("unsupported document type")
    template_id = str(uuid.uuid4())
    now = utc_now()
    row = {
        "template_id": template_id,
        "name": name,
        "document_type": document_type,
        "language": str(values.get("language", "de"))[:8] or "de",
        "source": "visual",
        "schema_version": VISUAL_TEMPLATE_SCHEMA,
        "version": 1,
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
    }
    layout = default_visual_layout(document_type)
    directory = _directory(root)
    lock = directory / ".visual-templates-write.lock"
    with exclusive_file_lock(lock):
        payload = _read_json(_index_path(root), {"templates": []})
        payload.setdefault("templates", []).append(row)
        atomic_json_write(_layout_path(root, template_id), layout)
        atomic_json_write(_index_path(root), payload)
    DocumentStore(root).history.record(
        "visual_business_template_created", actor, "business-template", template_id,
        {"name": name, "document_type": document_type, "schema_version": VISUAL_TEMPLATE_SCHEMA},
    )
    return row


def visual_template(root: Path, template_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = next((item for item in visual_templates(root) if item.get("template_id") == template_id), None)
    if row is None:
        raise ValueError("unknown visual template")
    layout = validate_visual_layout(_read_json(_layout_path(root, template_id), {}))
    return row, layout


def save_visual_template(
    root: Path,
    template_id: str,
    metadata: dict[str, Any],
    layout: Any,
    actor: str,
) -> dict[str, Any]:
    normalized = validate_visual_layout(layout)
    directory = _directory(root)
    lock = directory / ".visual-templates-write.lock"
    with exclusive_file_lock(lock):
        payload = _read_json(_index_path(root), {"templates": []})
        rows = payload.get("templates", [])
        row = next((item for item in rows if item.get("template_id") == template_id), None)
        if row is None:
            raise ValueError("unknown visual template")
        name = str(metadata.get("name", row.get("name", ""))).strip()
        document_type = str(metadata.get("document_type", row.get("document_type", "invoice")))
        if not name or len(name) > 120:
            raise ValueError("template name must contain 1 to 120 characters")
        if document_type not in DOCUMENT_TYPES:
            raise ValueError("unsupported document type")
        row.update(
            name=name,
            document_type=document_type,
            language=str(metadata.get("language", row.get("language", "de")))[:8] or "de",
            version=int(row.get("version", 1)) + 1,
            updated_at=utc_now(),
            updated_by=actor,
        )
        atomic_json_write(_layout_path(root, template_id), normalized)
        atomic_json_write(_index_path(root), payload)
    DocumentStore(root).history.record(
        "visual_business_template_updated", actor, "business-template", template_id,
        {"name": row["name"], "version": row["version"]},
    )
    return row


def duplicate_visual_template(root: Path, template_id: str, actor: str) -> dict[str, Any]:
    source, layout = visual_template(root, template_id)
    created = create_visual_template(
        root,
        {"name": f"{source['name']} – Kopie", "document_type": source["document_type"], "language": source.get("language", "de")},
        actor,
    )
    save_visual_template(root, created["template_id"], created, layout, actor)
    return next(row for row in visual_templates(root) if row["template_id"] == created["template_id"])


def editor_preview_tokens(business: dict[str, Any], layout: dict[str, Any]) -> dict[str, str]:
    address = " ".join(filter(None, (business.get("seller_street", ""), business.get("seller_postal", ""), business.get("seller_city", "")))).strip()
    return {
        "firma.name": str(business.get("seller_name", "") or "Musterfirma GmbH"),
        "firma.anschrift": address or "Musterstraße 1 · 12345 Musterstadt",
        "firma.ansprechpartner": "Max Mustermann",
        "firma.email": str(business.get("seller_email", "") or "info@example.invalid"),
        "firma.iban": str(business.get("seller_iban", "") or "DE00 0000 0000 0000 0000 00"),
        "firma.bic": str(business.get("seller_bic", "") or "MUSTERBIC"),
        "firma.bank": str(business.get("seller_bank", "") or "Musterbank"),
        "firma.steuer_nr": str(business.get("seller_tax_number", "") or "12/345/67890"),
        "firma.ust_idnr": str(business.get("seller_vat_id", "") or "DE123456789"),
        "rechnung.nummer": "RE-2026-0001",
        "rechnung.datum": date.today().isoformat(),
        "rechnung.zahlungsziel": "14 Tage",
        "kunde.name": "Musterkunde GmbH",
        "kunde.anschrift": "Kundenstraße 5 · 54321 Kundenstadt",
        "datum.heute": date.today().isoformat(),
        **{key: str(value) for key, value in layout.get("free_text", {}).items()},
    }


def _is_admin() -> bool:
    try:
        return bool(g.user["is_admin"])
    except (KeyError, TypeError, IndexError):
        return False


def register_template_editor_routes(bp, *, root_provider, actor_provider, business_settings_provider) -> None:
    @bp.get("/templates/editor")
    @login_required
    def visual_template_overview():
        return render_template(
            "documents/business_template_overview.html",
            visual_templates=visual_templates(root_provider()),
            is_admin=_is_admin(),
            document_types=sorted(DOCUMENT_TYPES),
        )

    @bp.post("/templates/editor/new")
    @login_required
    def visual_template_create():
        if not _is_admin():
            abort(403)
        try:
            row = create_visual_template(root_provider(), request.form.to_dict(), actor_provider())
            flash("Visuelle Vorlage wurde angelegt.")
            return redirect(url_for(".visual_template_edit", template_id=row["template_id"]))
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for(".visual_template_overview"))

    @bp.get("/templates/editor/<template_id>")
    @login_required
    def visual_template_edit(template_id: str):
        try:
            row, layout = visual_template(root_provider(), template_id)
        except ValueError:
            abort(404)
        return render_template(
            "documents/business_template_editor.html",
            template_row=row,
            layout=layout,
            layout_json=json.dumps(layout, ensure_ascii=False),
            tokens=TOKEN_CATALOG,
            preview_tokens=editor_preview_tokens(business_settings_provider(root_provider()), layout),
            is_admin=_is_admin(),
            document_types=sorted(DOCUMENT_TYPES),
        )

    @bp.post("/templates/editor/<template_id>")
    @login_required
    def visual_template_save(template_id: str):
        if not _is_admin():
            abort(403)
        try:
            layout = json.loads(request.form.get("layout_json", "{}"))
            save_visual_template(
                root_provider(), template_id, request.form.to_dict(), layout, actor_provider()
            )
            flash("Visuelle Vorlage wurde gespeichert.")
        except (ValueError, json.JSONDecodeError) as exc:
            flash(str(exc))
        return redirect(url_for(".visual_template_edit", template_id=template_id))

    @bp.post("/templates/editor/<template_id>/duplicate")
    @login_required
    def visual_template_duplicate(template_id: str):
        if not _is_admin():
            abort(403)
        try:
            row = duplicate_visual_template(root_provider(), template_id, actor_provider())
            flash("Vorlage wurde dupliziert.")
            return redirect(url_for(".visual_template_edit", template_id=row["template_id"]))
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for(".visual_template_overview"))
