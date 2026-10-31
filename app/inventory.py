"""Mobile-first inventory capture for arbitrary physical objects and books.

ObjectStore remains the canonical inventory. This module adds camera/barcode/NFC
capture, optional book metadata, photos and audited inspection schedules backed
by the existing VTODO task store.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import time
import uuid
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from .auth import login_required
from .book_metadata_cache import (
    BOOK_METADATA_CACHE_STALE_SECONDS,
    BookMetadataCache,
)
from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .inventory_metadata import (
    _http_json,
    _metadata_candidate,
    isbn_from_barcode as isbn_from_barcode,
    merge_book_metadata,
    normalize_isbn,
    parse_google_books,
    parse_openlibrary,
    parse_openlibrary_search,
)
from .inventory_values import date_only as _date_only
from .inventory_values import fields_text as _fields_text
from .inventory_values import money as _money
from .inventory_values import single_line as _single_line
from .object_store import ObjectStore
from .todo_store import TodoStore


bp = Blueprint("inventory", __name__, url_prefix="/inventory")

LOOKUP_INTERVAL_SECONDS = 5
MAX_PHOTO_BYTES = 12 * 1024 * 1024
INSPECTION_UNITS = {"days", "weeks", "months", "years"}
BOOK_TYPE_NAMES = {"book", "buch"}


def _objects() -> ObjectStore:
    return ObjectStore(current_app.config["DOCUMENT_ROOT"])


def _inventory() -> "InventoryEnrichmentStore":
    return InventoryEnrichmentStore(current_app.config["DOCUMENT_ROOT"])


def _todos() -> TodoStore:
    return TodoStore(current_app.config["DOCUMENT_ROOT"])


def lookup_book_metadata(isbn: str) -> dict[str, Any]:
    google: dict[str, Any] = {}
    openlibrary_search: dict[str, Any] = {}
    openlibrary_legacy: dict[str, Any] = {}
    candidates: list[dict[str, str]] = []
    errors: list[str] = []
    reachable_sources = 0
    required_fields = ("title", "authors", "publisher", "published_date")
    fields = "key,title,subtitle,author_name,isbn,publisher,first_publish_year,number_of_pages_median,language,subject"

    try:
        payload = _http_json(
            "https://openlibrary.org/search.json?q="
            + quote_plus(f"isbn:{isbn}")
            + "&fields="
            + quote_plus(fields)
            + "&limit=5"
        )
        reachable_sources += 1
        openlibrary_search = parse_openlibrary_search(payload, isbn)
        if openlibrary_search:
            candidates.append(_metadata_candidate(openlibrary_search))
    except (TimeoutError, OSError, ValueError, json.JSONDecodeError, http.client.HTTPException) as exc:
        errors.append(f"openlibrary-search:{type(exc).__name__}")

    result = dict(openlibrary_search)
    if not result or any(not result.get(key) for key in required_fields):
        try:
            payload = _http_json(
                "https://openlibrary.org/api/books?bibkeys="
                + quote_plus(f"ISBN:{isbn}")
                + "&format=json&jscmd=data"
            )
            reachable_sources += 1
            openlibrary_legacy = parse_openlibrary(payload, isbn)
            if openlibrary_legacy:
                candidates.append(_metadata_candidate(openlibrary_legacy))
        except (TimeoutError, OSError, ValueError, json.JSONDecodeError, http.client.HTTPException) as exc:
            errors.append(f"openlibrary-legacy:{type(exc).__name__}")
    result = merge_book_metadata(result, openlibrary_legacy)

    # Google Books remains a last fallback, preserving the existing network and
    # dependency behavior while exposing consulted-provider provenance.
    if not result.get("title"):
        try:
            payload = _http_json(
                "https://www.googleapis.com/books/v1/volumes?q="
                + quote_plus(f"isbn:{isbn}")
                + "&maxResults=5"
            )
            reachable_sources += 1
            google = parse_google_books(payload, isbn)
            if google:
                candidates.append(_metadata_candidate(google))
        except (TimeoutError, OSError, ValueError, json.JSONDecodeError, http.client.HTTPException) as exc:
            errors.append(f"google:{type(exc).__name__}")
        result = merge_book_metadata(result, google)

    if result:
        result["metadata_checked_at"] = utc_now()
    if errors:
        result["lookup_errors"] = errors
    result["metadata_candidates"] = candidates[:3]
    result["lookup_reachable"] = reachable_sources > 0
    return result


def _image_extension(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("Nur JPEG, PNG und WebP werden als Inventarfoto akzeptiert")


def _inspection_rrule(interval: Any, unit: str) -> str:
    try:
        count = int(interval or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Prüfintervall ist ungültig") from exc
    if count <= 0:
        return ""
    if count > 366:
        raise ValueError("Prüfintervall ist zu groß")
    unit = str(unit or "").strip().lower()
    mapping = {"days": "DAILY", "weeks": "WEEKLY", "months": "MONTHLY", "years": "YEARLY"}
    if unit not in mapping:
        raise ValueError("Unbekannte Einheit für das Prüfintervall")
    return f"FREQ={mapping[unit]};INTERVAL={count}"


def _advance_due(value: str, interval: Any, unit: str) -> str:
    current = date.fromisoformat(_date_only(value, "Fälligkeit"))
    count = int(interval or 0)
    if count <= 0:
        return ""
    if unit == "days":
        return (current + timedelta(days=count)).isoformat()
    if unit == "weeks":
        return (current + timedelta(weeks=count)).isoformat()
    if unit == "months":
        absolute = current.year * 12 + current.month - 1 + count
        year, month_index = divmod(absolute, 12)
        month = month_index + 1
        return current.replace(year=year, month=month, day=min(current.day, monthrange(year, month)[1])).isoformat()
    if unit == "years":
        year = current.year + count
        return current.replace(year=year, day=min(current.day, monthrange(year, current.month)[1])).isoformat()
    raise ValueError("Unbekannte Einheit für das Prüfintervall")


def _object_fields(form: Any, isbn: str) -> dict[str, str]:
    values = {
        "isbn": isbn,
        "barcode": _single_line(form.get("barcode"), 120),
        "nfc_id": _single_line(form.get("nfc_id"), 240),
        "manufacturer": _single_line(form.get("manufacturer"), 240),
        "model": _single_line(form.get("model"), 240),
        "serial_number": _single_line(form.get("serial_number"), 240),
        "purchase_date": _date_only(form.get("purchase_date"), "Kaufdatum") if form.get("purchase_date") else "",
        "authors": _single_line(form.get("authors"), 600),
        "publisher": _single_line(form.get("publisher"), 300),
        "published_date": _single_line(form.get("published_date"), 80),
        "page_count": _single_line(form.get("page_count"), 20),
        "language": _single_line(form.get("language"), 20),
        "categories": _single_line(form.get("categories"), 600),
        "market_price": _money(form.get("market_price")),
        "currency": _single_line(form.get("currency"), 3).upper(),
        "price_source": _single_line(form.get("price_source"), 120),
        "metadata_source": _single_line(form.get("metadata_source"), 160),
        "metadata_checked_at": _single_line(form.get("metadata_checked_at"), 80),
    }
    return {key: value for key, value in values.items() if value}


def _find_exact(identifier: str = "", nfc_id: str = "") -> dict[str, Any] | None:
    identifier = _single_line(identifier, 120)
    nfc_id = _single_line(nfc_id, 240)
    queries = list(dict.fromkeys(value for value in (identifier, nfc_id) if value))
    if not queries:
        return None
    store = _objects()
    candidates: dict[str, dict[str, Any]] = {}
    for query in queries:
        for item in store.objects(query):
            candidates[str(item.get("object_id", ""))] = item
    for item in candidates.values():
        if identifier and str(item.get("identifier", "")).strip() == identifier:
            return item
        if nfc_id and str(item.get("fields", {}).get("nfc_id", "")).strip() == nfc_id:
            return item
    return None


def _inspection_input(form: Any) -> dict[str, Any] | None:
    name = _single_line(form.get("inspection_name"), 240)
    due = _date_only(form.get("inspection_due"), "Prüffälligkeit") if form.get("inspection_due") else ""
    responsible = _single_line(form.get("inspection_responsible"), 240)
    note = _single_line(form.get("inspection_note"), 1000)
    if not any((name, due, responsible, note, str(form.get("inspection_interval") or "").strip())):
        return None
    if not name:
        raise ValueError("Bezeichnung der Prüfung fehlt")
    if not due:
        raise ValueError("Fälligkeit der Prüfung fehlt")
    try:
        interval = int(form.get("inspection_interval") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Prüfintervall ist ungültig") from exc
    unit = str(form.get("inspection_unit") or "months").strip().lower()
    if interval < 0 or interval > 366:
        raise ValueError("Prüfintervall liegt außerhalb des zulässigen Bereichs")
    if interval and unit not in INSPECTION_UNITS:
        raise ValueError("Unbekannte Einheit für das Prüfintervall")
    return {"name": name, "next_due": due, "interval": interval, "unit": unit, "responsible": responsible, "note": note}


class InventoryEnrichmentStore:
    """Sidecar for inventory photos, captured metadata and inspection history."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "inventory"
        self.media_directory = self.directory / "media"
        self.index_path = self.directory / "index.json"
        self.rate_path = self.directory / "rate-limits.json"
        self.lock_path = self.directory / ".inventory-write.lock"

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def object_meta(self, object_id: str) -> dict[str, Any]:
        data = self._read(self.index_path)
        value = data.get("objects", {}).get(str(object_id), {}) if isinstance(data.get("objects"), dict) else {}
        return dict(value) if isinstance(value, dict) else {}

    def object_metas(self) -> dict[str, dict[str, Any]]:
        data = self._read(self.index_path)
        objects = data.get("objects", {}) if isinstance(data.get("objects"), dict) else {}
        return {str(object_id): dict(value) for object_id, value in objects.items() if isinstance(value, dict)}

    def record_snapshot(self, object_id: str, fields: dict[str, str], actor: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            data = self._read(self.index_path)
            entry = data.setdefault("objects", {}).setdefault(object_id, {})
            entry["fields_snapshot"] = dict(fields)
            if fields.get("isbn"):
                entry["book"] = dict(fields)
            entry["updated_at"] = utc_now()
            entry["updated_by"] = actor
            data["version"] = 2
            atomic_json_write(self.index_path, data)

    def add_inspection(self, object_id: str, values: dict[str, Any], actor: str, task_id: str = "") -> dict[str, Any]:
        rule = {
            "rule_id": str(uuid.uuid4()),
            "name": _single_line(values.get("name"), 240),
            "next_due": _date_only(values.get("next_due"), "Prüffälligkeit"),
            "interval": int(values.get("interval") or 0),
            "unit": str(values.get("unit") or "months").strip().lower(),
            "responsible": _single_line(values.get("responsible"), 240),
            "note": _single_line(values.get("note"), 1000),
            "task_id": str(task_id or "").strip(),
            "active": True,
            "last_completed_at": "",
            "last_result": "",
            "created_at": utc_now(),
            "created_by": actor,
        }
        if not rule["name"] or not rule["next_due"]:
            raise ValueError("Prüfbezeichnung und Fälligkeit sind erforderlich")
        _inspection_rrule(rule["interval"], rule["unit"]) if rule["interval"] else ""
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            data = self._read(self.index_path)
            entry = data.setdefault("objects", {}).setdefault(object_id, {})
            rules = entry.setdefault("inspections", [])
            if not isinstance(rules, list):
                rules = []
                entry["inspections"] = rules
            rules.append(rule)
            entry["updated_at"] = utc_now()
            entry["updated_by"] = actor
            data["version"] = 2
            atomic_json_write(self.index_path, data)
        return rule

    def inspection_by_task(self, object_id: str, task_id: str) -> dict[str, Any] | None:
        return next(
            (
                dict(rule)
                for rule in self.object_meta(object_id).get("inspections", [])
                if isinstance(rule, dict) and str(rule.get("task_id", "")) == str(task_id)
            ),
            None,
        )

    def complete_inspection(
        self,
        object_id: str,
        rule_id: str,
        actor: str,
        *,
        result: str = "",
        completed_at: str = "",
        next_due: str | None = None,
        source: str = "inventory",
    ) -> dict[str, Any]:
        when = _date_only(completed_at, "Prüfdatum") if completed_at else date.today().isoformat()
        with exclusive_file_lock(self.lock_path):
            data = self._read(self.index_path)
            entry = data.setdefault("objects", {}).setdefault(object_id, {})
            rules = entry.setdefault("inspections", [])
            rule = next((row for row in rules if isinstance(row, dict) and row.get("rule_id") == rule_id), None)
            if rule is None:
                raise ValueError("Unbekannte Prüfregel")
            if not rule.get("active", True):
                raise ValueError("Prüfung ist bereits abgeschlossen")
            calculated_next = next_due
            if calculated_next is None:
                calculated_next = _advance_due(rule.get("next_due", when), rule.get("interval", 0), rule.get("unit", "months"))
            if calculated_next:
                calculated_next = _date_only(calculated_next, "Nächste Prüffälligkeit")
            event = {
                "history_id": str(uuid.uuid4()),
                "rule_id": rule_id,
                "name": str(rule.get("name", "")),
                "completed_at": when,
                "result": _single_line(result, 4000),
                "next_due": calculated_next or "",
                "source": source,
                "created_at": utc_now(),
                "created_by": actor,
            }
            history = entry.setdefault("inspection_history", [])
            if not isinstance(history, list):
                history = []
                entry["inspection_history"] = history
            history.append(event)
            rule["last_completed_at"] = when
            rule["last_result"] = event["result"]
            rule["next_due"] = calculated_next or ""
            rule["active"] = bool(calculated_next)
            entry["updated_at"] = utc_now()
            entry["updated_by"] = actor
            data["version"] = 2
            atomic_json_write(self.index_path, data)
        return event

    def save_photo(self, object_id: str, upload: Any, actor: str, photo_role: str = "") -> dict[str, Any] | None:
        if upload is None or not getattr(upload, "filename", ""):
            return None
        data = upload.stream.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            raise ValueError("Inventarfoto ist größer als 12 MiB")
        extension = _image_extension(data)
        digest = hashlib.sha256(data).hexdigest()
        filename = f"{uuid.uuid4().hex}{extension}"
        target_dir = self.media_directory / object_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        target.write_bytes(data)
        photo = {"filename": filename, "sha256": digest, "size": len(data), "created_at": utc_now(), "created_by": actor}
        if str(photo_role or "").strip():
            photo["role"] = str(photo_role).strip()
        try:
            with exclusive_file_lock(self.lock_path):
                index = self._read(self.index_path)
                entry = index.setdefault("objects", {}).setdefault(object_id, {})
                photos = entry.setdefault("photos", [])
                if not isinstance(photos, list):
                    photos = []
                    entry["photos"] = photos
                photos.append(photo)
                entry["updated_at"] = utc_now()
                entry["updated_by"] = actor
                index["version"] = 2
                atomic_json_write(self.index_path, index)
        except Exception:
            try:
                target.unlink(missing_ok=True)
            finally:
                raise
        return photo

    def set_photo_role_by_sha(
        self, object_id: str, sha256: str, role: str, actor: str
    ) -> bool:
        """Set the role of attached photo(s) matching a SHA digest.

        The role must be caller-validated (PHOTO_ROLES or "" to
        clear). True only when a row actually changed; unknown
        objects/digests and equal roles yield False without a write.
        """
        digest = str(sha256 or "").strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return False
        clean_role = str(role or "").strip()
        changed = False
        with exclusive_file_lock(self.lock_path):
            index = self._read(self.index_path)
            objects = index.get("objects")
            entry = objects.get(str(object_id)) if isinstance(objects, dict) else None
            photos = entry.get("photos", []) if isinstance(entry, dict) else []
            if not isinstance(photos, list):
                return False
            for photo in photos:
                if (not isinstance(photo, dict)
                        or str(photo.get("sha256", "")).casefold() != digest):
                    continue
                if clean_role:
                    if str(photo.get("role", "") or "") == clean_role:
                        continue
                    photo["role"] = clean_role
                elif "role" not in photo:
                    continue
                else:
                    del photo["role"]
                changed = True
            if not changed or not isinstance(entry, dict):
                return False
            entry["updated_at"] = utc_now()
            entry["updated_by"] = str(actor)
            index["version"] = 2
            atomic_json_write(self.index_path, index)
        return True

    def media_path(self, object_id: str, filename: str) -> Path:
        meta = self.object_meta(object_id)
        allowed = {str(photo.get("filename")) for photo in meta.get("photos", []) if isinstance(photo, dict) and photo.get("filename")}
        if filename not in allowed or not re.fullmatch(r"[0-9a-f]{32}\.(?:jpg|png|webp)", filename):
            raise ValueError("Unbekanntes Inventarfoto")
        path = (self.media_directory / object_id / filename).resolve()
        base = self.media_directory.resolve()
        if base not in path.parents or not path.is_file() or path.is_symlink():
            raise ValueError("Inventarfoto ist nicht verfügbar")
        return path

    def consume_rate_limit(self, actor: str, action: str, interval: int = LOOKUP_INTERVAL_SECONDS) -> tuple[bool, int]:
        now = time.time()
        key = hashlib.sha256(actor.encode("utf-8")).hexdigest()[:24]
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read(self.rate_path)
            actions = state.setdefault(action, {})
            try:
                last = float(actions.get(key, 0))
            except (TypeError, ValueError):
                last = 0.0
            remaining = interval - (now - last)
            if remaining > 0:
                return False, max(1, int(math.ceil(remaining)))
            actions[key] = now
            state["version"] = 1
            atomic_json_write(self.rate_path, state)
        return True, 0


def _create_inspection_task(item: dict[str, Any], values: dict[str, Any], actor: str) -> dict[str, Any]:
    interval = int(values.get("interval") or 0)
    unit = str(values.get("unit") or "months")
    rrule = _inspection_rrule(interval, unit) if interval else ""
    task_values: dict[str, Any] = {
        "due": values["next_due"],
        "rrule": rrule,
        "categories": ["Inventar", "Prüfung"],
        "related_to": [f"urn:simpleoffice:object:{item['object_id']}"],
        "description": f"Inventar #{item.get('display_id', '')}: {item.get('name', '')}. {values.get('note', '')}".strip(),
        "url": url_for("inventory.item_detail", object_id=item["object_id"], _external=True),
    }
    if values.get("responsible"):
        task_values["assigned_to"] = [values["responsible"]]
    return _todos().add(f"Inventar #{item.get('display_id', '')} – {values['name']}", actor, task_values)

def record_inventory_task_completion(root: str | Path, task: dict[str, Any], actor: str, next_due: str = "") -> bool:
    """Mirror completion of a linked VTODO into the inventory inspection history."""
    relations = task.get("related_to", [])
    if isinstance(relations, str):
        relations = [relations]
    object_id = next(
        (str(value).split("urn:simpleoffice:object:", 1)[1] for value in relations if str(value).startswith("urn:simpleoffice:object:")),
        "",
    )
    if not object_id:
        return False
    store = InventoryEnrichmentStore(root)
    rule = store.inspection_by_task(object_id, str(task.get("id", "")))
    if not rule:
        return False
    store.complete_inspection(
        object_id,
        str(rule["rule_id"]),
        actor,
        result=str(task.get("result", "")),
        next_due=next_due,
        source="tasks",
    )
    return True


@bp.get("")
@login_required
def index():
    items = _objects().objects()
    metadata = _inventory().object_metas()
    rows = [{**item, "inventory": metadata.get(item["object_id"], {})} for item in reversed(items[-80:])]
    from .inventory_library_capture import capture_context_from_query, capture_locations

    capture = capture_context_from_query(request.args)
    locations = capture_locations()
    from .contextflow.demo import is_verified_demo_root
    from .contextflow.vision import vision_settings_from_mapping

    vision_settings = vision_settings_from_mapping(current_app.config, os.environ)
    meta_photo_consent_available = bool(
        str(vision_settings.get("provider", "")).strip().lower() == "meta"
        and vision_settings.get("meta_consent") is True
        and vision_settings.get("meta_api_key")
        and is_verified_demo_root(Path(current_app.config["DOCUMENT_ROOT"]))
    )
    return render_template(
        "inventory/index.html",
        inventory_rows=rows,
        today=date.today().isoformat(),
        capture_context=capture,
        library_locations=locations if capture.get("enabled") else [],
        series_locations=locations,
        meta_photo_consent_available=meta_photo_consent_available,
    )


@bp.get("/<object_id>")
@login_required
def item_detail(object_id: str):
    try:
        item = _objects().object(object_id)
    except ValueError:
        abort(404)
    meta = _inventory().object_meta(item["object_id"])
    return render_template("inventory/detail.html", item=item, inventory=meta, today=date.today().isoformat())


def _book_lookup_response(isbn: str, metadata: dict[str, Any]):
    payload = dict(metadata)
    payload["ok"] = True
    payload["amazon_search"] = url_for("inventory.amazon_search", q=isbn)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response

@bp.get("/lookup")
@login_required
def book_lookup():
    try:
        isbn = normalize_isbn(request.args.get("isbn", ""))
    except ValueError:
        return jsonify({"ok": False, "error": "Ungültige ISBN."}), 400

    store = _inventory()
    cache = BookMetadataCache(current_app.config["DOCUMENT_ROOT"])
    cached = cache.get(isbn)
    if cached:
        cached["metadata_cache"] = "fresh"
        return _book_lookup_response(isbn, cached)

    allowed, retry_after = store.consume_rate_limit(str(g.user["username"]), "book-metadata")
    if not allowed:
        response = jsonify({"ok": False, "error": "Metadatenabruf ist auf einen Klick je 5 Sekunden begrenzt.", "retry_after": retry_after})
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        response.headers["Cache-Control"] = "no-store"
        return response

    metadata = lookup_book_metadata(isbn)
    if metadata.get("title"):
        try:
            cache.put(isbn, metadata)
        except OSError:
            pass
        metadata["metadata_cache"] = "miss"
        return _book_lookup_response(isbn, metadata)

    if metadata.get("lookup_errors") and not metadata.get("lookup_reachable"):
        stale = cache.get(isbn, max_age_seconds=BOOK_METADATA_CACHE_STALE_SECONDS)
        if stale:
            stale["metadata_cache"] = "stale"
            stale["lookup_warning"] = (
                "Buchdatenquellen sind derzeit nicht erreichbar; angezeigt werden ältere lokal gespeicherte Metadaten."
            )
            return _book_lookup_response(isbn, stale)
        response = jsonify({"ok": False, "error": "Buchdatenquellen sind derzeit nicht erreichbar. Internetverbindung bzw. DNS/HTTPS prüfen und erneut versuchen."})
        response.status_code = 503
    else:
        response = jsonify({"ok": False, "error": "Keine Buchmetadaten für diese ISBN gefunden."})
        response.status_code = 404
    response.headers["Cache-Control"] = "no-store"
    return response

@bp.get("/find")
@login_required
def find_item():
    identifier = _single_line(request.args.get("identifier", ""), 120)
    nfc_id = _single_line(request.args.get("nfc", ""), 240)
    item = _find_exact(identifier, nfc_id)
    if not item:
        return jsonify({"found": False})
    return jsonify({
        "found": True,
        "object_id": item["object_id"],
        "display_id": item.get("display_id", ""),
        "name": item.get("name", ""),
        "url": url_for("inventory.item_detail", object_id=item["object_id"]),
    })


@bp.get("/amazon")
@login_required
def amazon_search():
    query = _single_line(request.args.get("q", ""), 160)
    if not query:
        abort(400)
    allowed, retry_after = _inventory().consume_rate_limit(str(g.user["username"]), "amazon-search")
    if not allowed:
        return render_template("inventory/rate_limit.html", retry_after=retry_after), 429, {"Retry-After": str(retry_after), "Cache-Control": "no-store"}
    response = redirect("https://www.amazon.de/s?k=" + quote_plus(query), code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/items")
@bp.post("/books", endpoint="create_book")
@login_required
def create_item():
    from .inventory_library_capture import create_item_response

    return create_item_response()


@bp.post("/<object_id>/photo")
@login_required
def add_photo(object_id: str):
    try:
        item = _objects().object(object_id)
        photo = _inventory().save_photo(item["object_id"], request.files.get("photo"), str(g.user["username"]))
        if photo is None:
            raise ValueError("Kein Foto ausgewählt")
        flash("Inventarfoto wurde gespeichert.")
        return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
    except (OSError, ValueError):
        abort(400)


@bp.post("/<object_id>/inspections")
@login_required
def add_inspection(object_id: str):
    actor = str(g.user["username"])
    try:
        item = _objects().object(object_id)
        values = _inspection_input(request.form)
        if values is None:
            raise ValueError("Prüfdaten fehlen")
        task = _create_inspection_task(item, values, actor)
        try:
            _inventory().add_inspection(item["object_id"], values, actor, task["id"])
        except Exception:
            _todos().soft_delete(task["id"], actor)
            raise
        flash("Prüftermin wurde angelegt und als Aufgabe verknüpft.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("inventory.item_detail", object_id=object_id))


@bp.post("/<object_id>/inspections/<rule_id>/complete")
@login_required
def complete_inspection(object_id: str, rule_id: str):
    actor = str(g.user["username"])
    try:
        item = _objects().object(object_id)
        store = _inventory()
        meta = store.object_meta(item["object_id"])
        rule = next((row for row in meta.get("inspections", []) if isinstance(row, dict) and row.get("rule_id") == rule_id), None)
        if rule is None:
            raise ValueError("Unbekannte Prüfregel")
        if not rule.get("active", True):
            raise ValueError("Prüfung ist bereits abgeschlossen")
        next_due = _advance_due(str(rule.get("next_due", "")), rule.get("interval", 0), str(rule.get("unit", "months")))
        task_id = str(rule.get("task_id", ""))
        if task_id:
            if next_due:
                _todos().update(task_id, {"due": next_due, "status": "needs-action", "percent_complete": 0, "completed_at": "", "result": ""}, actor)
            else:
                _todos().update(task_id, {"status": "completed", "percent_complete": 100}, actor)
        store.complete_inspection(
            item["object_id"],
            rule_id,
            actor,
            result=str(request.form.get("result", "")),
            completed_at=str(request.form.get("completed_at", "")),
            next_due=next_due,
        )
        flash("Prüfung dokumentiert." + (f" Nächster Termin: {next_due}." if next_due else ""))
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("inventory.item_detail", object_id=object_id))


@bp.get("/media/<object_id>/<filename>")
@login_required
def inventory_media(object_id: str, filename: str):
    try:
        _objects().object(object_id)
        path = _inventory().media_path(object_id, filename)
    except ValueError:
        abort(404)
    response = send_file(path, conditional=True)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


# Registers the capture-inbox routes on the shared blueprint above.
# Bottom module import on purpose: the routes module needs the finished
# `bp`, and a name import here would break if the routes module is
# imported first (partial circular import).
from . import inventory_capture_routes as _capture_routes  # noqa: E402,F401
