"""In-app visual business template editor and persistent layout store.

The visual editor deliberately uses its own index next to the existing three-page
PDF/Office corporate templates. This keeps the current production renderer fully
backwards compatible until visual layouts are connected to PDF generation.
"""
from __future__ import annotations

import copy
import io
import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from flask import abort, flash, g, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .business_template_macros import MacroSyntaxError, macro_tokens, validate_macro_text

VISUAL_TEMPLATE_DIR = "business-templates/visual"
VISUAL_TEMPLATE_INDEX = "visual-templates.json"
VISUAL_TEMPLATE_STATE = "visual-template-state.json"
VISUAL_TEMPLATE_SCHEMA = 1
VISUAL_TEMPLATE_EXPORT_SCHEMA = 1
MAX_TEMPLATE_IMPORT_BYTES = 1_000_000
DOCUMENT_TYPES = {"invoice", "quote", "delivery_note", "letter"}
REGIONS = {"header", "content", "footer", "margin_left", "margin_right"}
ELEMENT_TYPES = {"text", "token", "line", "rect", "invoice_lines", "invoice_totals", "body"}
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

def _state_path(root: Path) -> Path:
    return _directory(root) / VISUAL_TEMPLATE_STATE


def _history_path(root: Path, template_id: str, version: int) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", template_id):
        raise ValueError("invalid visual template identifier")
    if not 1 <= int(version) <= 999_999:
        raise ValueError("invalid visual template version")
    path = _directory(root) / "history" / template_id / f"{int(version)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _visual_state(root: Path) -> dict[str, Any]:
    state = _read_json(_state_path(root), {"active_by_type": {}})
    if not isinstance(state, dict) or not isinstance(state.get("active_by_type", {}), dict):
        return {"active_by_type": {}}
    return state



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
    active_by_type = _visual_state(root).get("active_by_type", {})
    normalized = []
    for source in rows:
        if not isinstance(source, dict):
            continue
        row = dict(source)
        row["active"] = active_by_type.get(str(row.get("document_type", ""))) == row.get("template_id")
        normalized.append(row)
    return sorted(
        normalized,
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
        _element("free-1", "token", "content", 18, 101, 174, 10, "{{freitext1}}", font_size=9),
        _element("bank", "token", "footer", 18, 263, 80, 22,
                 "IBAN: {{firma.iban}}\nBIC: {{firma.bic}}\nBank: {{firma.bank}}", font_size=7.5),
        _element("tax", "token", "footer", 112, 263, 80, 22,
                 "Steuer-Nr.: {{firma.steuer_nr}}\nUSt-IdNr.: {{firma.ust_idnr}}", font_size=7.5),
    ]
    if document_type == "invoice":
        elements += [
            _element("invoice-lines", "invoice_lines", "content", 18, 116, 174, 68, "", font_size=8),
            _element("invoice-totals", "invoice_totals", "content", 112, 188, 80, 30, "", font_size=9),
            _element("free-2", "token", "content", 18, 188, 86, 14, "{{freitext2}}", font_size=9),
            _element("free-3", "token", "content", 18, 222, 174, 14, "{{freitext3}}", font_size=9),
        ]
    elif document_type == "letter":
        elements += [
            _element("body", "body", "content", 18, 116, 174, 118, "", font_size=10),
            _element("free-3", "token", "content", 18, 238, 174, 10, "{{freitext3}}", font_size=9),
        ]
    else:
        elements += [
            _element("body", "body", "content", 18, 116, 174, 102, "", font_size=10),
            _element("free-2", "token", "content", 18, 222, 174, 12, "{{freitext2}}", font_size=9),
            _element("free-3", "token", "content", 18, 238, 174, 10, "{{freitext3}}", font_size=9),
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
        try:
            validate_macro_text(text)
        except MacroSyntaxError as exc:
            raise ValueError(f"invalid template macro: {exc}") from exc
        known_tokens = {token for token, _label in TOKEN_CATALOG}
        unknown_tokens = sorted(macro_tokens(text) - known_tokens)
        if unknown_tokens:
            raise ValueError(f"unknown template token: {unknown_tokens[0]}")
        x_mm = _number(raw.get("x_mm", 10), minimum=0, maximum=210, name="x")
        y_mm = _number(raw.get("y_mm", 10), minimum=0, maximum=297, name="y")
        width_mm = _number(raw.get("width_mm", 40), minimum=1, maximum=210, name="width")
        height_mm = _number(raw.get("height_mm", 8), minimum=1, maximum=297, name="height")
        if x_mm + width_mm > 210.01 or y_mm + height_mm > 297.01:
            raise ValueError("template element must stay inside the A4 page")
        normalized_elements.append({
            "id": element_id,
            "type": element_type,
            "region": region,
            "x_mm": x_mm,
            "y_mm": y_mm,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "text": text,
            "style": _validate_style(raw.get("style")),
            "z_index": int(_number(raw.get("z_index", 1), minimum=0, maximum=999, name="z index")),
            "locked": bool(raw.get("locked", False)),
            "visible": bool(raw.get("visible", True)),
        })
    dynamic_types = ("invoice_lines", "invoice_totals", "body")
    for dynamic_type in dynamic_types:
        matching = [item for item in normalized_elements if item["type"] == dynamic_type]
        if len(matching) > 1:
            raise ValueError(f"visual template may contain only one {dynamic_type} element")
        if matching and matching[0]["region"] != "content":
            raise ValueError(f"{dynamic_type} element must be in the content region")
    regions_in = layout.get("regions", {}) if isinstance(layout.get("regions"), dict) else {}
    def region(name: str, enabled: bool) -> dict[str, Any]:
        raw_region = regions_in.get(name, {})
        if raw_region is not None and not isinstance(raw_region, dict):
            raise ValueError("template region configuration must be an object")
        return {"enabled": bool((raw_region or {}).get("enabled", enabled))}
    regions = {
        "header": {**region("header", True), "y_mm": 8, "height_mm": 38},
        "content": {"enabled": True, "y_mm": 48, "height_mm": 207},
        "footer": {**region("footer", True), "y_mm": 257, "height_mm": 32},
        "margin_left": {**region("margin_left", False), "width_mm": 8},
        "margin_right": {**region("margin_right", False), "width_mm": 8},
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


def _write_snapshot(root: Path, row: dict[str, Any], layout: dict[str, Any]) -> None:
    atomic_json_write(
        _history_path(root, str(row["template_id"]), int(row["version"])),
        {
            "export_schema": VISUAL_TEMPLATE_EXPORT_SCHEMA,
            "metadata": copy.deepcopy(row),
            "layout": copy.deepcopy(layout),
        },
    )


def visual_template_versions(root: Path, template_id: str) -> list[dict[str, Any]]:
    visual_template(root, template_id)
    directory = _directory(root) / "history" / template_id
    if not directory.is_dir():
        return []
    result = []
    for path in directory.glob("*.json"):
        try:
            version = int(path.stem)
        except ValueError:
            continue
        payload = _read_json(path, {})
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        result.append({
            "version": version,
            "updated_at": metadata.get("updated_at") or metadata.get("created_at") or "",
            "updated_by": metadata.get("updated_by") or metadata.get("created_by") or "",
        })
    return sorted(result, key=lambda item: int(item["version"]), reverse=True)


def active_visual_template(root: Path, document_type: str) -> dict[str, Any] | None:
    if document_type not in DOCUMENT_TYPES:
        raise ValueError("unsupported document type")
    active_id = _visual_state(root).get("active_by_type", {}).get(document_type)
    return next((row for row in visual_templates(root) if row.get("template_id") == active_id), None)


def set_active_visual_template(root: Path, template_id: str, actor: str) -> dict[str, Any]:
    row, _layout = visual_template(root, template_id)
    directory = _directory(root)
    with exclusive_file_lock(directory / ".visual-templates-write.lock"):
        state = _visual_state(root)
        state.setdefault("active_by_type", {})[row["document_type"]] = template_id
        atomic_json_write(_state_path(root), state)
    DocumentStore(root).history.record(
        "visual_business_template_activated", actor, "business-template", template_id,
        {"document_type": row["document_type"], "name": row["name"]},
    )
    return next(item for item in visual_templates(root) if item["template_id"] == template_id)


def export_visual_template(root: Path, template_id: str) -> dict[str, Any]:
    row, layout = visual_template(root, template_id)
    metadata = {
        key: row.get(key)
        for key in ("name", "document_type", "language", "schema_version")
    }
    return {
        "export_schema": VISUAL_TEMPLATE_EXPORT_SCHEMA,
        "metadata": metadata,
        "layout": layout,
    }


def create_visual_template(root: Path, values: dict[str, Any], actor: str, layout: Any | None = None) -> dict[str, Any]:
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
    layout = validate_visual_layout(layout if layout is not None else default_visual_layout(document_type))
    directory = _directory(root)
    lock = directory / ".visual-templates-write.lock"
    with exclusive_file_lock(lock):
        payload = _read_json(_index_path(root), {"templates": []})
        payload.setdefault("templates", []).append(row)
        atomic_json_write(_layout_path(root, template_id), layout)
        atomic_json_write(_index_path(root), payload)
        _write_snapshot(root, row, layout)
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
        _write_snapshot(root, row, normalized)
    DocumentStore(root).history.record(
        "visual_business_template_updated", actor, "business-template", template_id,
        {"name": row["name"], "version": row["version"]},
    )
    return row


def duplicate_visual_template(root: Path, template_id: str, actor: str) -> dict[str, Any]:
    source, layout = visual_template(root, template_id)
    return create_visual_template(
        root,
        {"name": f"{source['name']} – Kopie", "document_type": source["document_type"], "language": source.get("language", "de")},
        actor,
        layout=layout,
    )


def import_visual_template(root: Path, payload: Any, actor: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("export_schema") != VISUAL_TEMPLATE_EXPORT_SCHEMA:
        raise ValueError("unsupported visual template export schema")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("visual template export metadata is missing")
    layout = validate_visual_layout(payload.get("layout"))
    name = str(metadata.get("name", "")).strip()
    document_type = str(metadata.get("document_type", "")).strip()
    language = str(metadata.get("language", "de")).strip()
    row = create_visual_template(
        root,
        {"name": name, "document_type": document_type, "language": language},
        actor,
        layout=layout,
    )
    DocumentStore(root).history.record(
        "visual_business_template_imported", actor, "business-template", row["template_id"],
        {"name": row["name"], "document_type": row["document_type"]},
    )
    return row


def restore_visual_template_version(root: Path, template_id: str, version: int, actor: str) -> dict[str, Any]:
    current, _layout = visual_template(root, template_id)
    snapshot = _read_json(_history_path(root, template_id, version), {})
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("layout"), dict):
        raise ValueError("visual template version not found")
    restored = save_visual_template(root, template_id, current, snapshot["layout"], actor)
    DocumentStore(root).history.record(
        "visual_business_template_restored", actor, "business-template", template_id,
        {"restored_from_version": int(version), "new_version": restored["version"]},
    )
    return restored


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


def visual_template_choices(root: Path, document_type: str) -> list[dict[str, Any]]:
    """Return visual templates that are valid choices for one document workflow."""
    if document_type not in DOCUMENT_TYPES:
        raise ValueError("unsupported document type")
    return [
        {**row, "visual": True}
        for row in visual_templates(root)
        if row.get("document_type") == document_type
    ]


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
            versions=visual_template_versions(root_provider(), template_id),
        )

    @bp.post("/templates/editor/import")
    @login_required
    def visual_template_import():
        if not _is_admin():
            abort(403)
        upload = request.files.get("template_json")
        if upload is None:
            abort(400)
        raw = upload.stream.read(MAX_TEMPLATE_IMPORT_BYTES + 1)
        if len(raw) > MAX_TEMPLATE_IMPORT_BYTES:
            flash("Vorlagendatei ist zu groß.")
            return redirect(url_for(".visual_template_overview"))
        try:
            payload = json.loads(raw.decode("utf-8"))
            row = import_visual_template(root_provider(), payload, actor_provider())
            flash("Visuelle Vorlage wurde importiert.")
            return redirect(url_for(".visual_template_edit", template_id=row["template_id"]))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            flash(str(exc))
            return redirect(url_for(".visual_template_overview"))

    @bp.get("/templates/editor/<template_id>/export.json")
    @login_required
    def visual_template_export(template_id: str):
        try:
            row, _layout = visual_template(root_provider(), template_id)
            payload = json.dumps(export_visual_template(root_provider(), template_id), ensure_ascii=False, indent=2)
        except ValueError:
            abort(404)
        return send_file(
            io.BytesIO(payload.encode("utf-8")),
            mimetype="application/json",
            as_attachment=True,
            download_name=f"{row['name']}.simpleoffice-template.json",
        )

    @bp.post("/templates/editor/<template_id>/activate")
    @login_required
    def visual_template_activate(template_id: str):
        if not _is_admin():
            abort(403)
        try:
            set_active_visual_template(root_provider(), template_id, actor_provider())
            flash("Visuelle Vorlage wurde für diesen Dokumenttyp aktiviert.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for(".visual_template_overview"))

    @bp.post("/templates/editor/<template_id>/restore/<int:version>")
    @login_required
    def visual_template_restore(template_id: str, version: int):
        if not _is_admin():
            abort(403)
        try:
            restore_visual_template_version(root_provider(), template_id, version, actor_provider())
            flash(f"Version {version} wurde als neue Version wiederhergestellt.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for(".visual_template_edit", template_id=template_id))

    @bp.get("/templates/editor/<template_id>/preview.pdf")
    @login_required
    def visual_template_preview(template_id: str):
        try:
            row, _layout = visual_template(root_provider(), template_id)
            from .business_template_render import (
                render_visual_invoice_pdf,
                render_visual_letter_pdf,
                sample_invoice,
            )
            if row.get("document_type") == "invoice":
                sample = sample_invoice()
                business = business_settings_provider(root_provider())
                sample["seller"].update({
                    "name": business.get("seller_name") or sample["seller"]["name"],
                    "street": business.get("seller_street") or sample["seller"]["street"],
                    "postal": business.get("seller_postal") or sample["seller"]["postal"],
                    "city": business.get("seller_city") or sample["seller"]["city"],
                    "email": business.get("seller_email") or sample["seller"]["email"],
                    "iban": business.get("seller_iban") or sample["seller"]["iban"],
                    "bic": business.get("seller_bic") or sample["seller"]["bic"],
                    "bank": business.get("seller_bank") or sample["seller"]["bank"],
                    "vat_id": business.get("seller_vat_id") or sample["seller"]["vat_id"],
                    "tax_number": business.get("seller_tax_number") or sample["seller"]["tax_number"],
                })
                pdf = render_visual_invoice_pdf(root_provider(), template_id, sample)
            else:
                pdf = render_visual_letter_pdf(
                    root_provider(), template_id,
                    business=business_settings_provider(root_provider()),
                    recipient="Musterkunde GmbH\nKundenstraße 5\n54321 Kundenstadt",
                    subject="Vorschau",
                    body="Dies ist eine Vorschau des frei positionierten Dokumenttextes.\n\n"
                         "Die gleiche Layoutdefinition wird für die produktive PDF-Ausgabe verwendet.",
                )
            return send_file(
                io.BytesIO(pdf), mimetype="application/pdf", as_attachment=False,
                download_name=f"{row['name']}-Vorschau.pdf",
            )
        except ValueError:
            abort(409)

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
