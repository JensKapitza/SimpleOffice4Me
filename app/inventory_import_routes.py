"""HTTP workflow for staged CSV bulk imports with duplicate checks."""
from __future__ import annotations

from flask import current_app, flash, g, jsonify, redirect, request, url_for

from .auth import login_required
from .inventory import _find_exact, _objects, bp
from .inventory_import import MAX_FILE_BYTES, ImportStageStore, parse_csv, validate_row


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


def _actor() -> str:
    return str(g.user["username"])


def _root() -> str:
    return current_app.config["DOCUMENT_ROOT"]


def _preview_rows(rows: list[dict[str, str]]) -> tuple[list[dict], list[dict]]:
    valid: list[dict] = []
    problems: list[dict] = []
    seen: set[str] = set()
    for position, row in enumerate(rows, start=2):
        values, problem = validate_row(row, seen)
        identifier = (values or {}).get("identifier", "") if values else row.get("identifier", "")
        if values and identifier:
            seen.add(str(identifier).casefold())
        if problem:
            problems.append({"line": position, "name": row.get("name", ""), "problem": problem})
            continue
        assert values is not None
        duplicate = _find_exact(values["identifier"], "") if values["identifier"] else None
        if duplicate:
            problems.append({"line": position, "name": values["name"],
                             "problem": f"bereits vorhanden als #{duplicate.get('display_id', '')}"})
            continue
        valid.append(values)
    return valid, problems


@bp.post("/import/preview")
@login_required
def preview_csv_import():
    """Parse an upload and stage importable rows without writing objects."""
    from flask import render_template

    upload = request.files.get("file")
    data = upload.read(MAX_FILE_BYTES + 1) if upload else b""
    try:
        unmapped, rows = parse_csv(data)
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("inventory.index") + "#import")
    valid, problems = _preview_rows(rows)
    token = ImportStageStore(_root()).save(
        [{"values": item} for item in valid], _actor())
    if _wants_json():
        return jsonify({"ok": True, "token": token, "total": len(rows),
                        "valid": len(valid), "unmapped": unmapped,
                        "problems": problems,
                        "preview": valid[:10]})
    return render_template("inventory/import_preview.html", token=token, total=len(rows),
                           valid=valid, problems=problems, unmapped=unmapped)


@bp.post("/import/commit")
@login_required
def commit_csv_import():
    """Create staged rows; duplicates stay skipped, failures are reported."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        staged = ImportStageStore(_root()).take(str(fields.get("token", "")), _actor())
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("inventory.index") + "#import")
    created: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in staged:
        values = entry.get("values", {}) if isinstance(entry, dict) else {}
        name = str(values.get("name", "") or "").strip()
        identifier = str(values.get("identifier", "") or "").strip()
        if not name:
            failed.append({"name": "", "problem": "Name fehlt"})
            continue
        folded = identifier.casefold()
        if identifier and (folded in seen or _find_exact(identifier, "")):
            skipped.append({"name": name, "problem": "bereits vorhanden"})
            continue
        if identifier:
            seen.add(folded)
        try:
            item = _objects().create(values, _actor())
        except ValueError as exc:
            failed.append({"name": name, "problem": str(exc)})
            continue
        created.append({"object_id": item["object_id"],
                        "display_id": item.get("display_id", ""), "name": item["name"]})
    if _wants_json():
        return jsonify({"ok": True, "created": created, "skipped": skipped, "failed": failed,
                        "counts": {"created": len(created), "skipped": len(skipped),
                                   "failed": len(failed)}})
    flash(f"CSV-Import: {len(created)} angelegt, {len(skipped)} Duplikate übersprungen, "
          f"{len(failed)} fehlerhaft.")
    return redirect(url_for("inventory.index"))
