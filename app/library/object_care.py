"""Unified lifecycle workspace for ObjectStore items.

Inventory capture and the library are workflow views over the same ObjectStore
records.  This module adds the cross-cutting capabilities which belong to every
physical object: photos, local OCR/image analysis, condition history,
compliance evidence, inspection scheduling and structured library placement.

Existing inventory media and inspection data remain in the established
InventoryEnrichmentStore sidecar so no migration is required.
"""
from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..document_store import atomic_json_write, utc_now
from ..file_lock import exclusive_file_lock
from ..inventory import InventoryEnrichmentStore, _create_inspection_task
from ..object_store import ObjectStore
from ..object_vision import analyze_ocr
from .routes import assign_object_to_location, bp
from .store import LibraryStore


CONDITION_STATES = {
    "new": "Neu",
    "very_good": "Sehr gut",
    "good": "Gut",
    "used": "Gebraucht",
    "worn": "Abgenutzt",
    "damaged": "Beschädigt",
    "repair": "Reparatur nötig",
    "retired": "Ausgesondert",
}
COMPLIANCE_STATUSES = {
    "unknown": "Ungeprüft",
    "present": "Vorhanden",
    "verified": "Geprüft",
    "due": "Prüfung fällig",
    "failed": "Nicht bestanden / mangelhaft",
    "not_applicable": "Nicht zutreffend",
}
COMPLIANCE_PRESETS = (
    ("CE", "CE-Kennzeichnung / Konformitätsunterlagen"),
    ("DGUV V3", "DGUV Vorschrift 3 · elektrische Prüfung"),
    ("VDE", "VDE / elektrische Sicherheit"),
    ("GS", "GS-Zeichen / Produktsicherheit"),
    ("RoHS", "RoHS / Stoffbeschränkung"),
    ("WEEE", "WEEE / Elektrokennzeichnung"),
    ("Leiterprüfung", "Leiter-/Trittprüfung"),
    ("Kalibrierung", "Kalibrierung / Messmittelprüfung"),
    ("Sichtprüfung", "Allgemeine Sichtprüfung"),
)


def _root() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"])


def _objects() -> ObjectStore:
    return ObjectStore(_root())


def _library() -> LibraryStore:
    return LibraryStore(_root())


def _care() -> "ObjectCareStore":
    return ObjectCareStore(_root())


def _actor() -> str:
    return str(g.user["username"])


def _line(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _iso_date(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} ist ungültig") from exc


def _image_metadata(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return {"metadata_status": "unavailable", "metadata_error": "Pillow ist nicht installiert"}
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            sample = image.convert("L")
            sample.thumbnail((256, 256))
            brightness = round(float(ImageStat.Stat(sample).mean[0]), 1)
            orientation = "landscape" if width > height else "portrait" if height > width else "square"
            return {
                "metadata_status": "completed",
                "format": str(image.format or path.suffix.lstrip(".").upper()),
                "width": int(width),
                "height": int(height),
                "megapixels": round(width * height / 1_000_000, 2),
                "orientation": orientation,
                "brightness": brightness,
                "quality_hint": "low_resolution" if min(width, height) < 720 else "ok",
            }
    except (OSError, ValueError, SyntaxError) as exc:
        return {"metadata_status": "failed", "metadata_error": _line(exc, 300)}


def _ocr_image(path: Path) -> tuple[str, str, str]:
    """Compatibility wrapper around the shared ML-first OCR service."""
    result = analyze_ocr(path)
    return (
        str(result.get("text", "")),
        str(result.get("status", "failed")),
        str(result.get("error", "")),
    )


def _extract_labeled(text: str, labels: str) -> str:
    pattern = rf"(?:{labels})\s*[:#=-]?\s*([A-Z0-9][A-Z0-9._/+\- ]{{1,63}})"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return _line(match.group(1), 64).strip(" .,:;-") if match else ""


def _analysis_suggestions(text: str) -> dict[str, Any]:
    upper = text.upper()
    terms: list[str] = []
    checks = (
        ("CE", r"(?<![A-Z0-9])CE(?![A-Z0-9])"),
        ("DGUV", r"\bDGUV\b"),
        ("VDE", r"\bVDE\b"),
        ("GS", r"(?<![A-Z0-9])GS(?![A-Z0-9])"),
        ("RoHS", r"\bROHS\b"),
        ("WEEE", r"\bWEEE\b"),
    )
    for label, pattern in checks:
        if re.search(pattern, upper):
            terms.append(label)
    fields: dict[str, str] = {}
    serial = _extract_labeled(text, r"S/?N|SERIAL(?:\s+NUMBER)?|SERIEN(?:NUMMER)?")
    model = _extract_labeled(text, r"MODEL|MODELL|TYPE|TYP")
    if serial:
        fields["serial_number"] = serial
    if model:
        fields["model"] = model
    if "CE" in terms:
        fields["ce_marking"] = "erkannt"
    if "DGUV" in terms:
        fields["dguv_marking"] = "erkannt"
    words = re.findall(r"[A-Za-zÄÖÜäöüß0-9][A-Za-zÄÖÜäöüß0-9._/-]{3,}", text)
    keywords = list(dict.fromkeys(_line(word, 48) for word in words if not word.isdigit()))[:20]
    return {"detected_terms": terms, "suggested_fields": fields, "keywords": keywords}


def _object_update_values(item: dict[str, Any], fields: dict[str, str], tags: list[str]) -> dict[str, Any]:
    return {
        "name": item.get("name", ""),
        "type": item.get("type", "object"),
        "status": item.get("status", "active"),
        "description": item.get("description", ""),
        "identifier": item.get("identifier", ""),
        "location": item.get("location", ""),
        "expires_at": item.get("expires_at", ""),
        "tags": tags,
        "fields": fields,
    }


def _merge_ocr_analysis(analysis: dict[str, Any], ocr: dict[str, Any]) -> None:
    analysis["ocr_engine"] = str(ocr.get("engine", ""))
    analysis["ocr_status"] = str(ocr.get("status", "failed"))
    analysis["ocr_text"] = str(ocr.get("text", ""))
    analysis["ocr_characters"] = int(ocr.get("characters", len(analysis["ocr_text"])) or 0)
    analysis["ocr_confidence"] = ocr.get("confidence")
    analysis["ocr_blocks"] = ocr.get("blocks", []) if isinstance(ocr.get("blocks"), list) else []
    for key in (
        "error",
        "fallback_from",
        "fallback_reason",
        "fallback_engine",
        "fallback_status",
        "fallback_error",
    ):
        value = ocr.get(key)
        if value not in (None, ""):
            analysis[f"ocr_{key}"] = value


class ObjectCareStore(InventoryEnrichmentStore):
    """Cross-cutting object lifecycle data backed by the existing sidecar."""

    def record_condition(self, object_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        state = str(values.get("state", "")).strip().casefold()
        if state not in CONDITION_STATES:
            raise ValueError("Unbekannter Objektzustand")
        try:
            rating = int(values.get("rating") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Zustandsbewertung ist ungültig") from exc
        if rating not in range(1, 6):
            raise ValueError("Zustandsbewertung muss zwischen 1 und 5 liegen")
        event = {
            "condition_id": str(uuid.uuid4()),
            "state": state,
            "rating": rating,
            "note": _line(values.get("note"), 4000),
            "captured_on": _iso_date(values.get("captured_on"), "Zustandsdatum") or date.today().isoformat(),
            "photo_filename": _line(values.get("photo_filename"), 80),
            "created_at": utc_now(),
            "created_by": actor,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            data = self._read(self.index_path)
            entry = data.setdefault("objects", {}).setdefault(str(object_id), {})
            history = entry.setdefault("condition_history", [])
            if not isinstance(history, list):
                history = []
                entry["condition_history"] = history
            history.append(event)
            entry["condition"] = dict(event)
            entry["updated_at"] = utc_now()
            entry["updated_by"] = actor
            data["version"] = 3
            atomic_json_write(self.index_path, data)
        return event

    def record_compliance(self, object_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        scheme = _line(values.get("scheme"), 160)
        status = str(values.get("status", "unknown")).strip().casefold()
        if not scheme:
            raise ValueError("Prüf-/Konformitätsart fehlt")
        if status not in COMPLIANCE_STATUSES:
            raise ValueError("Unbekannter Konformitätsstatus")
        record = {
            "compliance_id": str(uuid.uuid4()),
            "scheme": scheme,
            "status": status,
            "reference": _line(values.get("reference"), 500),
            "valid_until": _iso_date(values.get("valid_until"), "Gültigkeit"),
            "note": _line(values.get("note"), 4000),
            "document_id": _line(values.get("document_id"), 80),
            "photo_filename": _line(values.get("photo_filename"), 80),
            "recorded_on": _iso_date(values.get("recorded_on"), "Nachweisdatum") or date.today().isoformat(),
            "created_at": utc_now(),
            "created_by": actor,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            data = self._read(self.index_path)
            entry = data.setdefault("objects", {}).setdefault(str(object_id), {})
            history = entry.setdefault("compliance_history", [])
            if not isinstance(history, list):
                history = []
                entry["compliance_history"] = history
            history.append(record)
            current = entry.setdefault("compliance", {})
            if not isinstance(current, dict):
                current = {}
                entry["compliance"] = current
            current[scheme.casefold()] = dict(record)
            entry["updated_at"] = utc_now()
            entry["updated_by"] = actor
            data["version"] = 3
            atomic_json_write(self.index_path, data)
        return record

    def analyze_photo(self, object_id: str, filename: str, actor: str) -> dict[str, Any]:
        path = self.media_path(object_id, filename)
        analysis = {
            "analyzed_at": utc_now(),
            "analyzed_by": actor,
            **_image_metadata(path),
        }
        ocr = analyze_ocr(path)
        _merge_ocr_analysis(analysis, ocr)
        analysis.update(_analysis_suggestions(str(ocr.get("text", ""))))
        with exclusive_file_lock(self.lock_path):
            data = self._read(self.index_path)
            entry = data.setdefault("objects", {}).setdefault(str(object_id), {})
            photos = entry.setdefault("photos", [])
            photo = next((row for row in photos if isinstance(row, dict) and row.get("filename") == filename), None)
            if photo is None:
                raise ValueError("Unbekanntes Objektfoto")
            photo["analysis"] = analysis
            entry["last_image_analysis"] = analysis
            entry["updated_at"] = utc_now()
            entry["updated_by"] = actor
            data["version"] = 3
            atomic_json_write(self.index_path, data)
        return analysis


def _apply_analysis(object_id: str, analysis: dict[str, Any], actor: str, *, overwrite: bool = False) -> dict[str, Any]:
    store = _objects()
    item = store.object(object_id)
    fields = dict(item.get("fields", {}))
    suggestions = analysis.get("suggested_fields", {}) if isinstance(analysis.get("suggested_fields"), dict) else {}
    for key, value in suggestions.items():
        if value and (overwrite or not fields.get(key)):
            fields[str(key)] = _line(value, 500)
    ocr_text = str(analysis.get("ocr_text", "") or "").strip()
    if ocr_text and (overwrite or not fields.get("ocr_excerpt")):
        fields["ocr_excerpt"] = _line(ocr_text, 1000)
    keywords = analysis.get("keywords", []) if isinstance(analysis.get("keywords"), list) else []
    if keywords and (overwrite or not fields.get("ocr_keywords")):
        fields["ocr_keywords"] = ", ".join(_line(value, 48) for value in keywords[:20])
    tags = list(item.get("tags", []))
    for term in analysis.get("detected_terms", []) if isinstance(analysis.get("detected_terms"), list) else []:
        if term and term not in tags:
            tags.append(str(term))
    return store.update(item["object_id"], _object_update_values(item, fields, tags), actor)


def _inspection_values(form: Any, scheme: str) -> dict[str, Any] | None:
    due = str(form.get("inspection_due", "") or "").strip()
    if not due:
        return None
    try:
        interval = int(form.get("inspection_interval") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Prüfintervall ist ungültig") from exc
    unit = str(form.get("inspection_unit", "months") or "months").strip().lower()
    return {
        "name": _line(form.get("inspection_name"), 240) or f"{scheme} prüfen",
        "next_due": _iso_date(due, "Prüffälligkeit"),
        "interval": interval,
        "unit": unit,
        "responsible": _line(form.get("inspection_responsible"), 240),
        "note": _line(form.get("note"), 1000),
    }


@bp.get("/objects/<object_id>/care")
@login_required
def object_care(object_id: str):
    try:
        item = _objects().object(object_id)
    except ValueError:
        abort(404)
    library = _library()
    assignment = library.assignment(item["object_id"])
    return render_template(
        "library/object_care.html",
        item=item,
        care=_care().object_meta(item["object_id"]),
        condition_states=CONDITION_STATES,
        compliance_statuses=COMPLIANCE_STATUSES,
        compliance_presets=COMPLIANCE_PRESETS,
        locations=library.locations(),
        assignment=assignment or {},
        assigned_path=library.location_path(str((assignment or {}).get("location_id", ""))) if assignment else "",
        today=date.today().isoformat(),
    )


@bp.post("/objects/<object_id>/care/photos")
@login_required
def object_care_add_photo(object_id: str):
    actor = _actor()
    try:
        item = _objects().object(object_id)
        store = _care()
        photo = store.save_photo(item["object_id"], request.files.get("photo"), actor)
        if photo is None:
            raise ValueError("Kein Foto ausgewählt")
        if request.form.get("analyze", "1") == "1":
            analysis = store.analyze_photo(item["object_id"], str(photo["filename"]), actor)
            _apply_analysis(item["object_id"], analysis, actor)
            flash("Foto gespeichert, lokal analysiert und erkannte Merkmale übernommen.")
        else:
            flash("Foto wurde an der Objektakte gespeichert.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("library.object_care", object_id=object_id) + "#media")


@bp.post("/objects/<object_id>/care/photos/<filename>/analyze")
@login_required
def object_care_analyze_photo(object_id: str, filename: str):
    actor = _actor()
    try:
        _objects().object(object_id)
        analysis = _care().analyze_photo(object_id, filename, actor)
        _apply_analysis(object_id, analysis, actor, overwrite=request.form.get("overwrite") == "1")
        flash("Bildanalyse/OCR aktualisiert. Erkannte Merkmale sind in der Objektakte verfügbar.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("library.object_care", object_id=object_id) + "#media")


@bp.post("/objects/<object_id>/care/conditions")
@login_required
def object_care_condition(object_id: str):
    actor = _actor()
    try:
        item = _objects().object(object_id)
        store = _care()
        photo = store.save_photo(item["object_id"], request.files.get("photo"), actor)
        if photo and request.form.get("analyze", "1") == "1":
            analysis = store.analyze_photo(item["object_id"], str(photo["filename"]), actor)
            _apply_analysis(item["object_id"], analysis, actor)
        store.record_condition(
            item["object_id"],
            {
                "state": request.form.get("state"),
                "rating": request.form.get("rating"),
                "note": request.form.get("note"),
                "captured_on": request.form.get("captured_on"),
                "photo_filename": str((photo or {}).get("filename", "")),
            },
            actor,
        )
        flash("Zustand wurde als neuer Verlaufspunkt dokumentiert.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("library.object_care", object_id=object_id) + "#condition")


@bp.post("/objects/<object_id>/care/compliance")
@login_required
def object_care_compliance(object_id: str):
    actor = _actor()
    try:
        item = _objects().object(object_id)
        store = _care()
        photo = store.save_photo(item["object_id"], request.files.get("photo"), actor)
        scheme = _line(request.form.get("scheme"), 160)
        store.record_compliance(
            item["object_id"],
            {
                "scheme": scheme,
                "status": request.form.get("status"),
                "reference": request.form.get("reference"),
                "valid_until": request.form.get("valid_until"),
                "recorded_on": request.form.get("recorded_on"),
                "note": request.form.get("note"),
                "document_id": request.form.get("document_id"),
                "photo_filename": str((photo or {}).get("filename", "")),
            },
            actor,
        )
        values = _inspection_values(request.form, scheme)
        if values:
            task = _create_inspection_task(item, values, actor)
            try:
                store.add_inspection(item["object_id"], values, actor, task["id"])
            except Exception:
                from ..todo_store import TodoStore
                TodoStore(_root()).soft_delete(task["id"], actor)
                raise
        flash("Prüf-/Konformitätsnachweis wurde dokumentiert." + (" Wiedervorlage wurde angelegt." if values else ""))
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("library.object_care", object_id=object_id) + "#compliance")


@bp.post("/objects/<object_id>/care/location")
@login_required
def object_care_location(object_id: str):
    try:
        result = assign_object_to_location(object_id, request.form.get("location_id", ""), _actor())
        flash(f"Objekt wurde {result['location_path']} zugeordnet.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("library.object_care", object_id=object_id) + "#location")


@bp.post("/locations/sync-objects")
@login_required
def sync_location_objects():
    try:
        rows = _library().sync_location_objects(_actor())
        flash(f"{len(rows)} Bibliotheksstandort(e) sind jetzt mit echten Objekten verknüpft.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("library.index") + "#locations")
