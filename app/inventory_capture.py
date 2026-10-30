"""Photo-inbox analysis for the inventory capture route.

Pure analysis plumbing: optimize the upload, run local OCR, ask the real
local ML backend for a label, route book vs. object, look up duplicates
and cached metadata. Nothing is stored here; missing backends are reported
honestly, never faked.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from flask import current_app, has_app_context, url_for

from .book_metadata_cache import BookMetadataCache
from .capture_analysis import (
    combine_batch_analyses,
    extract_year,
    isbn_from_text,
    missing_book_fields,
    route_kind,
    title_lines,
)


def capture_ocr(png: bytes) -> dict[str, Any]:
    """Run local OCR on optimized bytes; never raises, reports honestly."""
    from .object_vision import analyze_ocr

    try:
        with tempfile.TemporaryDirectory(prefix="capture-ocr-") as directory:
            path = Path(directory) / "capture.png"
            path.write_bytes(png)
            result = analyze_ocr(path)
    except (OSError, ValueError) as exc:
        return {"text": "", "engine": "", "status": "error",
                "characters": 0, "confidence": None, "error": str(exc)[:300]}
    return {
        "text": str(result.get("text", "") or "")[:20000],
        "engine": str(result.get("engine", "") or ""),
        "status": str(result.get("status", "") or ""),
        "characters": int(result.get("characters", 0) or 0),
        "confidence": result.get("confidence"),
        "error": str(result.get("error", "") or "")[:300],
    }


def capture_ml_label(
    png: bytes,
    isbn: str,
    settings: dict[str, Any] | None = None,
    *,
    consent_meta: bool = False,
    root: object = None,
) -> dict[str, Any]:
    """Ask a real Meta backend for an object label (mock never answers).

    Single-photo callers keep ``consent_meta=False`` (no consent
    element), so only real local backends answer. Batch callers may
    pass an explicit per-request consent plus the document root; Meta
    cloud then still requires the server flag, a key, and a verified
    synthetic demo root. Mock and missing backends raise, which is
    reported as unavailable — never faked. Works without a Flask
    context: explicit settings win, otherwise the app config is used
    when available, else backend defaults (honestly unavailable).
    """
    from .contextflow.vision import (
        MAX_IMAGE_BYTES,
        VisionError,
        suggest_inventory_object,
        vision_settings_from_mapping,
    )

    base: dict[str, Any] = {"available": False, "label": "", "item_type": "",
                            "isbn": "", "provider": "", "provider_label": "",
                            "model": "", "error": ""}
    if len(png) > MAX_IMAGE_BYTES:
        base["error"] = "Bild für ML-Analyse zu groß."
        return base
    try:
        if settings is None:
            config = current_app.config if has_app_context() else {}
            settings = vision_settings_from_mapping(config, os.environ)
        answer = suggest_inventory_object(
            png, "image/png", {"isbn": isbn}, settings,
            consent_meta is True, root,
        )
    except (VisionError, PermissionError) as exc:
        base["error"] = str(exc)[:300]
        return base
    suggestion = answer.get("suggestion", {})
    base.update({
        "available": True,
        "label": str(suggestion.get("object_name", "") or ""),
        "item_type": str(suggestion.get("item_type", "") or ""),
        "isbn": str(suggestion.get("isbn", "") or ""),
        "provider": str(answer.get("provider", "") or ""),
        "provider_label": str(answer.get("provider_label", "") or ""),
        "model": str(answer.get("model", "") or ""),
    })
    return base


def capture_duplicates(isbn: str) -> list[dict[str, str]]:
    """Find existing inventory/library records for an ISBN (best effort)."""
    if not isbn:
        return []
    duplicates: list[dict[str, str]] = []
    try:
        from .inventory import _find_exact

        item = _find_exact(isbn)
    except (ImportError, OSError, ValueError):
        item = None
    if item:
        duplicates.append({
            "scope": "inventory",
            "object_id": str(item.get("object_id", "")),
            "display_id": str(item.get("display_id", "")),
            "name": str(item.get("name", "")),
            "url": url_for("inventory.item_detail",
                           object_id=item.get("object_id", "")),
        })
    try:
        from .library.routes import _book_item

        exact = _book_item(isbn)
    except (ImportError, OSError, ValueError):
        exact = None
    if exact and all(entry["object_id"] != exact.get("object_id") for entry in duplicates):
        duplicates.append({
            "scope": "library",
            "object_id": str(exact.get("object_id", "")),
            "display_id": str(exact.get("display_id", "")),
            "name": str(exact.get("name", "")),
            "url": url_for("inventory.item_detail",
                           object_id=exact.get("object_id", "")),
        })
    return duplicates


def capture_metadata(isbn: str) -> dict[str, Any]:
    """Cached book metadata for an ISBN; cache only, no network here."""
    if not isbn:
        return {}
    try:
        cache = BookMetadataCache(current_app.config["DOCUMENT_ROOT"])
        found = cache.get(isbn)
    except (KeyError, OSError, ValueError):
        return {}
    if not isinstance(found, dict):
        return {}
    authors = found.get("authors", [])
    if isinstance(authors, str):
        authors = [authors] if authors.strip() else []
    return {
        "title": str(found.get("title", "") or "")[:300],
        "authors": [str(name)[:200] for name in authors[:6]],
        "year": str(found.get("year", "") or "")[:4],
        "publisher": str(found.get("publisher", "") or "")[:300],
    }


def analyze_photo(
    raw: bytes,
    photo_id: str,
    *,
    consent_meta: bool = False,
    root: object = None,
) -> dict[str, Any]:
    """Analyze one inbox photo and return the JSON payload (no storage).

    Raises ValueError for invalid image data. ``consent_meta`` is an
    explicit per-request opt-in for the Meta cloud path (batch review
    only); single-photo callers keep the default False.
    """
    from .library.ocr_input import optimize_upload

    optimized, _ = optimize_upload(raw)
    digest = hashlib.sha256(raw).hexdigest()
    ocr = capture_ocr(optimized)
    text = ocr.get("text", "")
    isbn = isbn_from_text(text)
    ml_result = capture_ml_label(
        optimized, isbn, consent_meta=consent_meta is True, root=root)
    if not isbn and ml_result.get("isbn"):
        isbn = ml_result["isbn"]
    route = route_kind(isbn, ml_result.get("label", ""),
                       ml_result["available"], ml_result.get("item_type", ""))
    duplicates = capture_duplicates(isbn)
    metadata = capture_metadata(isbn)
    lines = title_lines(text)
    year = str(metadata.get("year", "") or "") or extract_year(text)
    title = str(metadata.get("title", "") or "") or (lines[0] if lines else "")
    prefill: dict[str, Any] = {"isbn": isbn}
    if route["kind"] == "book":
        prefill.update({
            "title": title,
            "authors": metadata.get("authors", []),
            "year": year,
            "publisher": metadata.get("publisher", ""),
            "title_lines": lines,
            "metadata_source": "cache" if metadata.get("title") else "ocr",
        })
        missing = missing_book_fields(prefill)
    else:
        prefill.update({"name": ml_result.get("label", "")})
        missing = [] if prefill["name"] else ["name"]
    return {
        "ok": True,
        "photo_id": photo_id,
        "sha256": digest,
        "kind": route["kind"],
        "kind_reason": route["reason"],
        "needs_name": route["needs_name"],
        "isbn": isbn,
        "prefill": prefill,
        "ocr": {key: ocr.get(key) for key in
                ("engine", "status", "characters", "confidence", "error")},
        "ml": {key: ml_result.get(key) for key in
               ("available", "label", "item_type", "provider",
                "provider_label", "model", "error")},
        "duplicates": duplicates,
        "missing_fields": missing,
    }


def store_upload(
    upload: Any,
    client_request_id: str,
    actor: str,
    root: str,
    batch_id: str = "",
) -> tuple[dict[str, Any], bool, bytes]:
    """Persist one inbox photo immediately; returns (entry, duplicate, raw).

    Raises ValueError for missing, oversized, or unsupported uploads;
    same token with different bytes raises CaptureInboxConflict (409).
    """
    from .capture_inbox_store import CaptureInboxStore
    from .inventory import MAX_PHOTO_BYTES, _image_extension

    if upload is None:
        raise ValueError("Foto fehlt.")
    try:
        raw = upload.stream.read(MAX_PHOTO_BYTES + 1)
    except OSError as exc:
        raise ValueError("Foto konnte nicht gelesen werden.") from exc
    if not raw or len(raw) > MAX_PHOTO_BYTES:
        raise ValueError("Foto fehlt oder überschreitet 12 MiB.")
    extension = _image_extension(raw)
    entry, duplicate = CaptureInboxStore(root).create(
        actor, raw, extension, client_request_id, batch_id
    )
    return entry, duplicate, raw


def public_entry(entry: dict[str, Any], preview_url: str) -> dict[str, Any]:
    """JSON-safe inbox entry projection (no server paths)."""
    original = entry.get("original", {})
    if not isinstance(original, dict):
        original = {}
    return {
        "entry_id": str(entry.get("entry_id", "")),
        "batch_id": str(entry.get("batch_id", "") or ""),
        "photo_role": str(entry.get("photo_role", "") or ""),
        "status": str(entry.get("status", "")),
        "created_at": str(entry.get("created_at", "")),
        "updated_at": str(entry.get("updated_at", "")),
        "preview_url": preview_url,
        "original": {
            "sha256": str(original.get("sha256", "")),
            "size": int(original.get("size", 0) or 0),
        },
        "analysis": entry.get("analysis", {}),
        "assignment": entry.get("assignment", {}),
    }


def reanalyze_entry(entry_id: str, actor: str, root: str) -> dict[str, Any]:
    """Re-run analysis on the stored original; returns the stored entry.

    Raises ValueError when the entry or its original is unavailable.
    """
    from .capture_inbox_store import CaptureInboxStore

    store = CaptureInboxStore(root)
    path = store.media_path(entry_id, actor)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("Original ist nicht verfügbar") from exc
    payload = analyze_photo(raw, entry_id)
    updated = store.save_analysis(entry_id, actor, payload)
    if updated is None:
        raise ValueError("Erfassung ist nicht verfügbar")
    return updated


ATTACHED_ROLE_SYNC_ERROR = (
    "Rolle im Eingangskorb gespeichert, "
    "am zugeordneten Foto nicht aktualisiert."
)


def set_entry_role(
    entry_id: str, actor: str, role: str, root: str
) -> tuple[dict[str, Any] | None, bool, str]:
    """Set an inbox photo role; propagate it to an attached photo by SHA.

    Returns (entry, attached_updated, sync_error). The entry is None
    when it is unknown or owned by another actor. Unknown roles raise
    ValueError without mutating anything. When the entry is already
    assigned to a book/object and its original SHA matches an
    attached inventory photo, that photo row is updated idempotently
    (True only when a row actually changed); roles set before
    attaching apply at attach time. The sync runs on every call, so
    resubmitting the same role retries a failed update. The inbox
    role stays saved when the sync fails; sync_error then carries a
    fixed German hint without exception details, else "".
    """
    from .capture_inbox_store import CaptureInboxStore
    from .inventory import InventoryEnrichmentStore

    store = CaptureInboxStore(root)
    if store.get(entry_id, actor) is None:
        return None, False, ""
    updated = store.set_photo_role(entry_id, actor, role)
    if updated is None:
        return None, False, ""
    assignment = updated.get("assignment", {})
    if not isinstance(assignment, dict):
        return updated, False, ""
    target = str(assignment.get("object_id", "") or "")
    if not target:
        return updated, False, ""
    original = updated.get("original", {})
    digest = str(original.get("sha256", "") or "") if isinstance(original, dict) else ""
    if not digest:
        return updated, False, ""
    try:
        changed = InventoryEnrichmentStore(root).set_photo_role_by_sha(
            target, digest, str(updated.get("photo_role", "") or ""), actor)
    except (OSError, ValueError):
        return updated, False, ATTACHED_ROLE_SYNC_ERROR
    return updated, changed, ""


def _resolve_target(root: str, object_id: str) -> dict[str, Any]:
    """Resolve a UUID or #sequence reference; fail closed when unknown."""
    from .object_store import ObjectStore

    try:
        return ObjectStore(root).object(object_id)
    except (OSError, ValueError) as exc:
        raise ValueError("Zielobjekt ist nicht verfügbar") from exc


def _attach_entry_original(
    entry_id: str, object_id: str, actor: str, root: str
) -> tuple[bool, str]:
    """Attach one inbox original as an inventory photo (retry-safe).

    Returns (attached, notice). Photos whose sha256 is already
    attached are skipped without a duplicate; a missing original or
    a failed save returns a German notice instead of raising, so the
    inbox link itself is never blocked and a retry stays possible.
    """
    from .capture_inbox_store import CaptureInboxStore
    from .inventory import InventoryEnrichmentStore

    store = CaptureInboxStore(root)
    try:
        raw = store.media_path(entry_id, actor).read_bytes()
    except (OSError, ValueError):
        return False, "Original ist nicht verfügbar; Zuordnung bleibt bestehen."
    digest = hashlib.sha256(raw).hexdigest()
    sidecar = InventoryEnrichmentStore(root)
    try:
        attached_digests = {
            str(photo.get("sha256", ""))
            for photo in sidecar.object_meta(object_id).get("photos", [])
            if isinstance(photo, dict)
        }
    except (OSError, ValueError):
        attached_digests = set()
    if digest in attached_digests:
        return False, ""
    owned = store.get(entry_id, actor) or {}
    role = str(owned.get("photo_role", "") or "")
    try:
        sidecar.save_photo(
            object_id, _BatchPhotoUpload(entry_id, raw), actor,
            photo_role=role)
    except (OSError, ValueError) as exc:
        return False, f"Foto nicht übernommen: {exc}"
    return True, ""


def assign_entry(
    entry_id: str, actor: str, kind: str, object_id: str, root: str
) -> tuple[dict[str, Any], bool, str]:
    """Link an inbox entry to an existing book/object (idempotent).

    The target must exist and match the kind (book targets for
    kind=book, anything else for kind=object); the original and the
    history are preserved. The inbox original is attached to the
    target as an inventory photo (digest-skipped when already
    attached); attach problems are reported, never silent, and never
    block the link. Returns (entry, attached, notice).
    Raises ValueError when the entry, target, or kind is invalid.
    """
    from .capture_inbox_store import CaptureInboxStore
    from .inventory import BOOK_TYPE_NAMES

    item = _resolve_target(root, object_id)
    item_type = str(item.get("type", "") or "").strip().casefold()
    wants_book = str(kind or "").strip().casefold() == "book"
    if wants_book != (item_type in BOOK_TYPE_NAMES):
        raise ValueError(
            "Zielobjekt passt nicht zur Zuordnung "
            "(Buch erwartet)" if wants_book else
            "Zielobjekt passt nicht zur Zuordnung "
            "(Gegenstand erwartet)"
        )
    store = CaptureInboxStore(root)
    updated = store.assign(
        entry_id,
        actor,
        kind,
        str(item.get("object_id", "")),
        display_id=str(item.get("display_id", "")),
        name=str(item.get("name", "")),
    )
    if updated is None:
        raise ValueError("Erfassung ist nicht verfügbar")
    attached, notice = _attach_entry_original(
        entry_id, str(item.get("object_id", "")), actor, root)
    return updated, attached, notice


def assign_batch(
    batch_id: str, actor: str, object_id: str, root: str
) -> dict[str, Any]:
    """Assign a whole photo book group to one existing book (idempotent).

    The target is resolved from an explicit UUID or #sequence reference
    and must exist and be a book; nothing is ever matched by ISBN here.
    Group entries owned by the actor are pre-checked first: entries
    already linked elsewhere abort the whole call (move the photo out
    and retry). The link itself is then written atomically in one
    locked store step, bound to the expected batch, so a concurrent
    change cannot half-link a group; a race raises instead of
    writing anything, before any photo is attached. Photo attach
    problems after a successful link are collected as notices
    instead of raising, and a retry completes missing photos
    without duplicates (digest-skipped).
    """
    from .capture_inbox_store import CaptureInboxStore
    from .inventory import BOOK_TYPE_NAMES

    item = _resolve_target(root, object_id)
    item_type = str(item.get("type", "") or "").strip().casefold()
    if item_type not in BOOK_TYPE_NAMES:
        raise ValueError("Zielobjekt passt nicht zur Zuordnung (Buch erwartet)")
    target_id = str(item.get("object_id", ""))
    store = CaptureInboxStore(root)
    entries = store.list_batch(actor, batch_id)
    if not entries:
        raise ValueError("Buchgruppe ist leer oder unbekannt.")
    for entry in entries:
        assignment = entry.get("assignment", {})
        if not isinstance(assignment, dict):
            assignment = {}
        linked = str(assignment.get("object_id", "") or "")
        if linked and (linked != target_id or assignment.get("kind") != "book"):
            raise ValueError(
                f"Eintrag {str(entry.get('entry_id', ''))[:8]} ist bereits "
                "anders zugeordnet; bitte Foto auslagern und erneut versuchen."
            )
    entry_ids = [str(entry.get("entry_id", "")) for entry in entries]
    updated = store.assign_many(
        entry_ids, actor, "book", target_id,
        display_id=str(item.get("display_id", "")),
        name=str(item.get("name", "")),
        expected_batch_id=batch_id,
    )
    if updated is None:
        raise ValueError(
            "Gruppe wurde zwischenzeitlich geändert oder entfernt; "
            "nichts wurde zugeordnet. Bitte Seite neu laden und wiederholen."
        )
    assigned = len(updated)
    attached = 0
    notices: list[str] = []
    for entry_id in entry_ids:
        done, notice = _attach_entry_original(entry_id, target_id, actor, root)
        if done:
            attached += 1
        elif notice:
            notices.append(f"Foto {entry_id[:8]}: {notice}")
    return {
        "ok": True,
        "object_id": target_id,
        "display_id": str(item.get("display_id", "")),
        "name": str(item.get("name", "")),
        "detail_url": url_for("inventory.item_detail", object_id=target_id),
        "assigned": assigned,
        "photo_count": len(entries),
        "attached": attached,
        "notices": notices,
        "message": (
            f"Buchgruppe zugeordnet: #{item.get('display_id', '')} "
            f"{item.get('name', '')}."
        ),
    }


def parse_batch_consent(value: Any) -> bool:
    """Explicit per-request opt-in for the Meta cloud path (default off).

    Only an explicit true-ish value counts; a missing or empty value
    (unchecked checkbox) never consents. The server flag, key, and
    synthetic demo root are still enforced by the provider gate.
    """
    if value is True:
        return True
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "ja"}


def summarize_batch(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine stored per-photo analyses of one book group (no storage).

    Every entry contributes its stored analysis (or an explicit gap
    when none exists); capture order is preserved by the caller.
    """
    rows: list[dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        analysis = entry.get("analysis", {})
        row = dict(analysis) if isinstance(analysis, dict) else {}
        row.setdefault("photo_id", str(entry.get("entry_id", "")))
        rows.append(row)
    combined = combine_batch_analyses(rows)
    combined["entry_ids"] = [
        str(entry.get("entry_id", ""))
        for entry in entries or [] if isinstance(entry, dict)
    ]
    return combined


def analyze_batch(
    batch_id: str, actor: str, root: str, *, consent_meta: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    """Re-run analysis on every stored original of one book group.

    Per-photo results are saved individually; a single unreadable or
    invalid photo keeps its previous analysis and is reported in
    ``errors`` — the rest of the group is still analyzed. Returns
    (fresh entries in capture order, combined summary, errors).
    Raises ValueError when the group is empty or unknown.
    """
    from .capture_inbox_store import CaptureInboxStore

    store = CaptureInboxStore(root)
    entries = store.list_batch(actor, batch_id)
    if not entries:
        raise ValueError("Buchgruppe ist leer oder unbekannt.")
    errors: dict[str, str] = {}
    for entry in entries:
        entry_id = str(entry.get("entry_id", ""))
        try:
            raw = store.media_path(entry_id, actor).read_bytes()
        except (OSError, ValueError):
            errors[entry_id] = "Original ist nicht verfügbar."
            continue
        try:
            payload = analyze_photo(
                raw, entry_id, consent_meta=consent_meta is True, root=root)
        except ValueError as exc:
            errors[entry_id] = str(exc)[:300]
            continue
        updated = store.save_analysis(entry_id, actor, payload)
        if updated is None:
            errors[entry_id] = "Erfassung ist nicht verfügbar."
    fresh = store.list_batch(actor, batch_id)
    return fresh, summarize_batch(fresh), errors


class _BatchPhotoUpload:
    """File-like wrapper so batch adoption reuses ``save_photo``."""

    def __init__(self, entry_id: str, raw: bytes):
        from io import BytesIO

        self.filename = f"{entry_id}.bin"
        self.stream = BytesIO(raw)


def _batch_photo_notices(
    item_id: str, entries: list[dict[str, Any]], actor: str, root: str
) -> tuple[int, list[str]]:
    """Attach every group original as an inventory photo (best effort).

    Photos whose sha256 is already attached are skipped (retry-safe,
    no duplicates). Failures are collected as notices; inbox
    originals are never deleted here.
    """
    from .capture_inbox_store import CaptureInboxStore
    from .inventory import InventoryEnrichmentStore

    store = CaptureInboxStore(root)
    sidecar = InventoryEnrichmentStore(root)
    try:
        attached_digests = {
            str(photo.get("sha256", ""))
            for photo in sidecar.object_meta(item_id).get("photos", [])
            if isinstance(photo, dict)
        }
    except (OSError, ValueError):
        attached_digests = set()
    attached = 0
    notices: list[str] = []
    for entry in entries:
        entry_id = str(entry.get("entry_id", ""))
        try:
            raw = store.media_path(entry_id, actor).read_bytes()
        except (OSError, ValueError):
            notices.append(f"Foto {entry_id[:8]} konnte nicht gelesen werden.")
            continue
        if hashlib.sha256(raw).hexdigest() in attached_digests:
            continue
        role = str(entry.get("photo_role", "") or "") if isinstance(entry, dict) else ""
        try:
            sidecar.save_photo(
                item_id, _BatchPhotoUpload(entry_id, raw), actor,
                photo_role=role)
        except (OSError, ValueError) as exc:
            notices.append(f"Foto {entry_id[:8]} nicht gespeichert: {exc}")
            continue
        attached += 1
        attached_digests.add(hashlib.sha256(raw).hexdigest())
    return attached, notices


def adopt_batch(
    batch_id: str,
    actor: str,
    root: str,
    fields: dict[str, Any],
    capture_request_id: str,
    *,
    allow_duplicate: bool = False,
) -> dict[str, Any]:
    """Create ONE inventory item from a reviewed book group (idempotent).

    The caller passes the user-corrected fields (title, authors, year,
    publisher, isbn, item_type); nothing is invented. All group
    originals are attached as inventory photos via the existing
    sidecar, and all entries are assigned to the new item. A repeated
    call with the same capture token for the same book group returns
    the same item and completes missing assignments without
    duplicating photos. The same token for another group is rejected
    so photos are never attached across groups.
    Raises ValueError for invalid input, PermissionError when the
    capture token belongs to another session.
    """
    import re

    from .capture_inbox_store import CaptureInboxStore

    store = CaptureInboxStore(root)
    entries = store.list_batch(actor, batch_id)
    if not entries:
        raise ValueError("Buchgruppe ist leer oder unbekannt.")
    token = str(capture_request_id or "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError(
            "Erfassungskennung ungültig. Bitte die Erfassungsseite neu laden.")
    combined = summarize_batch(entries)
    kind = str(fields.get("item_type", "") or "").strip().lower()
    if kind not in {"book", "object"}:
        kind = str(combined.get("kind", "object") or "object")
    if kind not in {"book", "object"}:
        kind = "object"
    return _adopt_batch_locked(
        store, entries, combined, kind, actor, root, fields,
        token, allow_duplicate, batch_id)


def _adopt_batch_locked(
    store: Any,
    entries: list[dict[str, Any]],
    combined: dict[str, Any],
    kind: str,
    actor: str,
    root: str,
    fields: dict[str, Any],
    token: str,
    allow_duplicate: bool,
    batch_id: str,
) -> dict[str, Any]:
    """Batch adoption under the shared capture-receipts lock."""
    from .file_lock import exclusive_file_lock
    from .inventory_library_capture import (
        _capture_receipt_paths,
        _capture_receipts,
    )
    from .object_store import ObjectStore

    receipts_path, lock_path = _capture_receipt_paths()
    with exclusive_file_lock(lock_path):
        receipt = _capture_receipts(receipts_path).get(token)
        if receipt and receipt.get("actor") != str(actor):
            raise PermissionError(
                "Erfassungskennung gehört zu einer anderen Sitzung. "
                "Bitte die Erfassungsseite neu laden.")
        if receipt and receipt.get("batch_id", "") != str(batch_id):
            raise ValueError(
                "Erfassungskennung gehört zu einer anderen Buchgruppe. "
                "Bitte die Erfassungsseite neu laden.")
        if receipt:
            try:
                item = ObjectStore(root).object(receipt["object_id"])
            except (KeyError, OSError, ValueError):
                item = None
            if item is not None:
                attached, photo_notices = _batch_photo_notices(
                    item["object_id"], entries, actor, root)
                assigned, assign_notices = _assign_batch_entries(
                    store, entries, kind, item, actor)
                message = (
                    "Diese Buchgruppe wurde bereits gespeichert: "
                    f"#{item.get('display_id', '')} {item.get('name', '')}."
                )
                return {
                    "ok": True, "saved": False, "retried": True,
                    "duplicate": False, "assigned": assigned == len(entries),
                    "object_id": item["object_id"],
                    "display_id": item.get("display_id", ""),
                    "name": item.get("name", ""),
                    "detail_url": url_for(
                        "inventory.item_detail",
                        object_id=item["object_id"]),
                    "attached": attached,
                    "assigned_count": assigned,
                    "photo_count": len(entries),
                    "notices": photo_notices + assign_notices,
                    "message": message,
                }
        return _create_batch_item(
            store, entries, combined, kind, actor, root, fields,
            token, receipts_path, allow_duplicate, batch_id)


def _assign_batch_entries(
    store: Any,
    entries: list[dict[str, Any]],
    kind: str,
    item: dict[str, Any],
    actor: str,
) -> tuple[int, list[str]]:
    """Assign every group entry to the adopted item (best effort)."""
    assigned = 0
    notices: list[str] = []
    for entry in entries:
        entry_id = str(entry.get("entry_id", ""))
        try:
            updated = store.assign(
                entry_id, actor, kind, str(item.get("object_id", "")),
                display_id=str(item.get("display_id", "")),
                name=str(item.get("name", "")),
            )
        except (OSError, ValueError) as exc:
            notices.append(
                f"Eintrag {entry_id[:8]} nicht zugeordnet: {exc}")
            continue
        if updated is None:
            notices.append(
                f"Eintrag {entry_id[:8]} ist nicht verfügbar.")
            continue
        assigned += 1
    return assigned, notices


def _create_batch_item(  # noqa: C901 - linear validation chain
    store: Any,
    entries: list[dict[str, Any]],
    combined: dict[str, Any],
    kind: str,
    actor: str,
    root: str,
    fields: dict[str, Any],
    token: str,
    receipts_path: Any,
    allow_duplicate: bool,
    batch_id: str,
) -> dict[str, Any]:
    """Validate corrected fields, create the item, attach, assign."""
    from .inventory import BOOK_TYPE_NAMES
    from .inventory_library_capture import _remember_capture_request
    from .object_store import ObjectStore

    inventory_module = _inventory_module()
    isbn_raw = str(fields.get("isbn", "") or "").strip()
    isbn = ""
    if isbn_raw:
        try:
            isbn = inventory_module.normalize_isbn(isbn_raw)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    title = inventory_module._single_line(fields.get("title", ""), 300)
    if kind == "book" and not title:
        raise ValueError(
            "Bitte vor der Übernahme einen Buchtitel eingeben. "
            "Metadatenabruf und OCR sind optional.")
    if not title:
        fallback = str((combined.get("prefill", {}) or {}).get("name", ""))
        title = fallback or f"Gegenstand {combined.get('photo_count', len(entries))} Fotos"
        title = inventory_module._single_line(title, 300)
    barcode = isbn or inventory_module._single_line(
        fields.get("barcode", ""), 120)
    identifier = isbn or barcode
    duplicate = inventory_module._find_exact(identifier, "")
    if duplicate and not allow_duplicate:
        message = (
            f"Bereits vorhanden: #{duplicate.get('display_id', '')} "
            f"{duplicate.get('name', '')}. Kein Duplikat angelegt."
        )
        return {
            "ok": True, "saved": False, "duplicate": True,
            "assigned": False,
            "object_id": duplicate["object_id"],
            "display_id": duplicate.get("display_id", ""),
            "name": duplicate.get("name", ""),
            "detail_url": url_for(
                "inventory.item_detail",
                object_id=duplicate["object_id"]),
            "attached": 0, "assigned_count": 0,
            "photo_count": len(entries), "notices": [],
            "message": message,
        }
    authors = fields.get("authors", "")
    if isinstance(authors, list):
        authors = ", ".join(str(name) for name in authors)
    form_like = {
        "barcode": barcode,
        "authors": str(authors or ""),
        "publisher": str(fields.get("publisher", "") or ""),
        "published_date": str(
            fields.get("published_date", fields.get("year", "")) or ""),
        "metadata_source": "capture-batch",
    }
    stored_fields = inventory_module._object_fields(form_like, isbn)
    tags = {"Inventar"}
    if kind in BOOK_TYPE_NAMES or isbn:
        tags.add("Buch")
    if isbn:
        tags.add("ISBN")
    item = ObjectStore(root).create(
        {
            "name": title,
            "type": kind,
            "status": "active",
            "description": inventory_module._single_line(
                fields.get("description", ""), 8000),
            "identifier": identifier,
            "location": inventory_module._single_line(
                fields.get("location", ""), 300),
            "expires_at": "",
            "tags": ",".join(sorted(tags, key=str.casefold)),
            "fields": inventory_module._fields_text(stored_fields),
        },
        actor,
    )
    _remember_capture_request(
        receipts_path, token, item["object_id"], "", actor,
        batch_id=batch_id)
    inventory_module._inventory().record_snapshot(
        item["object_id"], stored_fields, actor)
    attached, photo_notices = _batch_photo_notices(
        item["object_id"], entries, actor, root)
    assigned, assign_notices = _assign_batch_entries(
        store, entries, kind, item, actor)
    notices = photo_notices + assign_notices
    label = "Buch" if kind == "book" else "Gegenstand"
    message = (
        f"{label} #{item.get('display_id', '')} {item.get('name', '')} "
        f"aus {len(entries)} Fotos gespeichert "
        f"({attached} Fotos, {assigned} Zuordnungen)."
    )
    return {
        "ok": True, "saved": True, "duplicate": False,
        "assigned": assigned == len(entries),
        "object_id": item["object_id"],
        "display_id": item.get("display_id", ""),
        "name": item.get("name", ""),
        "detail_url": url_for(
            "inventory.item_detail", object_id=item["object_id"]),
        "attached": attached, "assigned_count": assigned,
        "photo_count": len(entries), "notices": notices,
        "message": message,
    }


def _inventory_module():  # type: ignore[no-untyped-def]
    from . import inventory

    return inventory
