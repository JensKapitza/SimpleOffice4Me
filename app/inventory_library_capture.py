"""Library handoff flow for inventory book captures.

The inventory controller remains small: this module resolves the scan context,
persists request receipts for safe retries, and performs the canonical inventory
creation plus structured shelf assignment.
"""
from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

from flask import current_app, flash, g, jsonify, redirect, request, url_for

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


def _inventory_module():
    # Imported at request time to avoid a module cycle with the inventory blueprint.
    from . import inventory

    return inventory


def _library_store():
    from .library.store import LibraryStore

    return LibraryStore(current_app.config["DOCUMENT_ROOT"])


def _capture_context(barcode: Any, location_id: Any, *, allow_missing_barcode: bool = False) -> dict[str, Any]:
    inventory = _inventory_module()
    clean_barcode = inventory._single_line(barcode, 120)
    clean_location_id = inventory._single_line(location_id, 80)
    if (not clean_barcode and not allow_missing_barcode) or not clean_location_id:
        raise ValueError("Barcode und Bibliotheksstandort fehlen.")
    library = _library_store()
    location = library.location(clean_location_id)
    try:
        isbn = inventory.normalize_isbn(clean_barcode)
    except ValueError:
        isbn = inventory.isbn_from_barcode(clean_barcode)
    return {
        "enabled": True,
        "barcode": clean_barcode,
        "isbn": isbn,
        "location_id": location["location_id"],
        "location_name": location["name"],
        "location_path": library.location_path(location["location_id"]),
    }


def capture_context_from_query(args) -> dict[str, Any]:
    """Resolve the optional scan handoff for the inventory page."""
    capture: dict[str, Any] = {"enabled": False}
    if args.get("book") != "1" or args.get("library_return") != "1":
        return capture
    try:
        capture = _capture_context(args.get("barcode", ""), args.get("library_location", ""))
    except ValueError as exc:
        flash(str(exc))
        return {"enabled": False}
    token = args.get("capture_request_id", "")
    capture["request_id"] = token if re.fullmatch(r"[0-9a-f]{32}", token) else secrets.token_hex(16)
    return capture


def capture_locations() -> list[dict[str, Any]]:
    return _library_store().locations()


def _capture_index_url(context: dict[str, Any], token: str = "") -> str:
    values = {
        "barcode": context["barcode"],
        "book": "1",
        "library_location": context["location_id"],
        "library_return": "1",
    }
    if token:
        values["capture_request_id"] = token
    return url_for("inventory.index", **values)


def _capture_receipt_paths() -> tuple[Path, Path]:
    directory = Path(current_app.config["DOCUMENT_ROOT"]) / CONTROL_DIR / "inventory"
    return directory / "capture-requests.json", directory / ".capture-requests.lock"


def _capture_receipts(path: Path) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    requests_by_id = data.get("requests", {}) if isinstance(data, dict) else {}
    if not isinstance(requests_by_id, dict):
        return {}
    return {
        str(token): {str(key): str(value) for key, value in receipt.items()}
        for token, receipt in requests_by_id.items()
        if isinstance(receipt, dict)
    }


def _remember_capture_request(
    path: Path,
    token: str,
    object_id: str,
    location_id: str,
    actor: str,
) -> None:
    receipts = _capture_receipts(path)
    receipts[token] = {
        "object_id": object_id,
        "location_id": location_id,
        "actor": actor,
        "created_at": utc_now(),
    }
    recent = sorted(receipts.items(), key=lambda item: item[1].get("created_at", ""))[-500:]
    atomic_json_write(path, {"version": 1, "requests": dict(recent)})


def _capture_success_url(item: dict[str, Any], location_id: str) -> str:
    return url_for(
        "library.index",
        location=location_id,
        q=item.get("identifier") or item.get("name", ""),
        captured=item["object_id"],
    ) + "#books"


def _wants_json_response() -> bool:
    """Series saves post the same form but stay on the page (AJAX)."""
    return request.form.get("response_format") == "json"


def _json_response(payload: dict[str, Any], status: int):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _item_result(item: dict[str, Any], location_id: str = "") -> dict[str, Any]:
    payload = {
        "object_id": item["object_id"],
        "display_id": item.get("display_id", ""),
        "name": item.get("name", ""),
        "detail_url": url_for("inventory.item_detail", object_id=item["object_id"]),
    }
    if location_id:
        payload["library_url"] = _capture_success_url(item, location_id)
    return payload


def _create_error_fields(message: str) -> dict[str, str]:
    if message.startswith("Preis"):
        return {"market_price": "invalid"}
    if message.startswith("Kaufdatum"):
        return {"purchase_date": "invalid"}
    return {}


def _complete_capture_assignment(item: dict[str, Any], location_id: str, barcode: str) -> dict[str, Any]:
    assignment = _library_store().assignment(item["object_id"])
    if assignment and assignment.get("location_id") == location_id:
        return item
    from .library.routes import assign_object_to_location

    return assign_object_to_location(
        item["object_id"], location_id, str(g.user["username"]), scanned=barcode,
    )["item"]


def _ensure_capture_book(item: dict[str, Any], actor: str, barcode: str, isbn: str) -> dict[str, Any]:
    inventory = _inventory_module()
    fields = dict(item.get("fields", {})) if isinstance(item.get("fields"), dict) else {}
    if not fields.get("barcode"):
        fields["barcode"] = barcode
    if isbn:
        fields["isbn"] = isbn
    tags = {str(tag).strip() for tag in item.get("tags", []) if str(tag).strip()}
    tags.update({"Inventar", "Buch"})
    if isbn:
        tags.add("ISBN")
    if (
        item.get("type", "").casefold() in inventory.BOOK_TYPE_NAMES
        and fields == item.get("fields", {})
        and tags == set(item.get("tags", []))
    ):
        return item
    return inventory._objects().update(
        item["object_id"],
        {
            "name": item.get("name", ""),
            "type": "book",
            "status": item.get("status", "active"),
            "description": item.get("description", ""),
            "identifier": item.get("identifier", "") or isbn or barcode,
            "location": item.get("location", ""),
            "expires_at": item.get("expires_at", ""),
            "tags": sorted(tags, key=str.casefold),
            "fields": inventory._fields_text(fields),
        },
        actor,
    )


def create_item_response():
    """Route callback used by both inventory item creation endpoints."""
    inventory = _inventory_module()
    if request.form.get("library_return") != "1":
        return _create_plain_item_response(inventory)
    try:
        capture = _capture_context(
            request.form.get("barcode", ""),
            request.form.get("library_location_id", ""),
            allow_missing_barcode=_wants_json_response(),
        )
    except ValueError as exc:
        message = str(exc)
        if _wants_json_response():
            if str(request.form.get("barcode", "")).strip():
                fields = {"library_location_id": "invalid"}
            else:
                fields = {"barcode": "missing"}
            return _json_response({"ok": False, "error": message, "fields": fields}, 400)
        flash(message)
        return redirect(url_for("inventory.index"))
    token = request.form.get("capture_request_id", "")
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        message = "Erfassungskennung ungültig. Bitte die Erfassungsseite neu laden."
        if _wants_json_response():
            return _json_response({"ok": False, "error": message, "fields": {"capture_request_id": "invalid"}}, 400)
        flash(message)
        return redirect(_capture_index_url(capture))

    receipts_path, lock_path = _capture_receipt_paths()
    with exclusive_file_lock(lock_path):
        receipt = _capture_receipts(receipts_path).get(token)
        if receipt and receipt.get("actor") != str(g.user["username"]):
            message = "Erfassungskennung gehört zu einer anderen Sitzung. Bitte die Erfassungsseite neu laden."
            if _wants_json_response():
                return _json_response({"ok": False, "error": message}, 403)
            flash(message)
            return redirect(_capture_index_url(capture))
        if receipt:
            try:
                item = inventory._objects().object(receipt["object_id"])
            except (KeyError, ValueError):
                item = None
            if item:
                location_id = receipt.get("location_id", capture["location_id"])
                try:
                    item = _complete_capture_assignment(item, location_id, capture["barcode"])
                except Exception:
                    current_app.logger.exception("inventory_capture_retry_assignment_failed object=%s", item["object_id"])
                    message = f"Buch #{item.get('display_id', '')} {item.get('name', '')} ist gespeichert, aber das Regal konnte weiterhin nicht zugeordnet werden."
                    if _wants_json_response():
                        return _json_response({"ok": True, "saved": False, "retried": True, "assigned": False, **_item_result(item), "notices": [message], "message": message}, 200)
                    flash(message)
                    return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
                message = f"Diese Erfassung wurde bereits gespeichert: #{item.get('display_id', '')} {item.get('name', '')}."
                if _wants_json_response():
                    return _json_response({"ok": True, "saved": False, "retried": True, "duplicate": False, "assigned": True, **_item_result(item, location_id), "message": message}, 200)
                flash(message)
                return _capture_success_redirect(item, location_id)
        return _create_inventory_item(inventory, capture, token, receipts_path)


def _create_plain_item_response(inventory):
    """Plain (non-book) creation; series saves get JSON + token idempotency."""
    token = request.form.get("capture_request_id", "")
    if not _wants_json_response() or not token:
        return _create_inventory_item(inventory)
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        return _json_response({"ok": False, "error": "Erfassungskennung ungültig. Bitte die Erfassungsseite neu laden.", "fields": {"capture_request_id": "invalid"}}, 400)
    receipts_path, lock_path = _capture_receipt_paths()
    with exclusive_file_lock(lock_path):
        receipt = _capture_receipts(receipts_path).get(token)
        if receipt and receipt.get("actor") != str(g.user["username"]):
            return _json_response({"ok": False, "error": "Erfassungskennung gehört zu einer anderen Sitzung. Bitte die Erfassungsseite neu laden."}, 403)
        if receipt:
            try:
                item = inventory._objects().object(receipt["object_id"])
            except (KeyError, ValueError):
                item = None
            if item:
                message = f"Diese Erfassung wurde bereits gespeichert: #{item.get('display_id', '')} {item.get('name', '')}."
                return _json_response({"ok": True, "saved": False, "retried": True, "duplicate": False, **_item_result(item), "message": message}, 200)
        return _create_inventory_item(inventory, None, token, receipts_path)


def _capture_success_redirect(item: dict[str, Any], location_id: str):
    return redirect(_capture_success_url(item, location_id))


def _create_inventory_item(
    inventory,
    capture: dict[str, Any] | None = None,
    capture_token: str = "",
    receipts_path: Path | None = None,
):
    actor = str(g.user["username"])
    barcode = inventory._single_line(request.form.get("barcode", ""), 120)
    explicit_isbn = str(request.form.get("isbn", "") or "").strip()
    isbn_value = explicit_isbn or inventory.isbn_from_barcode(barcode)
    isbn = ""
    if isbn_value:
        try:
            isbn = inventory.normalize_isbn(isbn_value)
        except ValueError as exc:
            message = str(exc)
            if _wants_json_response():
                return _json_response({"ok": False, "error": message, "fields": {"isbn": "invalid"}}, 400)
            flash(message)
            return redirect(_capture_index_url(capture, capture_token) if capture else url_for("inventory.index"))
    item_type = inventory._single_line(request.form.get("item_type"), 80) or ("book" if isbn else "object")
    if capture:
        item_type = "book"
    elif isbn and item_type.casefold() == "object":
        item_type = "book"
    identifier = isbn or barcode
    nfc_id = inventory._single_line(request.form.get("nfc_id", ""), 240)
    duplicate = inventory._find_exact(identifier, nfc_id)
    if duplicate and request.form.get("allow_duplicate") != "1":
        if capture:
            from .library.routes import assign_object_to_location

            try:
                duplicate = _ensure_capture_book(duplicate, actor, barcode, isbn)
                item = assign_object_to_location(
                    duplicate["object_id"], capture["location_id"], actor, scanned=barcode,
                )["item"]
            except Exception:
                current_app.logger.exception("inventory_capture_existing_assignment_failed object=%s", duplicate["object_id"])
                message = f"Buch #{duplicate.get('display_id', '')} ist bereits vorhanden; Regalzuordnung fehlgeschlagen. Kein Duplikat angelegt."
                if _wants_json_response():
                    return _json_response({"ok": True, "saved": False, "duplicate": True, "assigned": False, **_item_result(duplicate), "message": message}, 200)
                flash(message)
                return redirect(url_for("inventory.item_detail", object_id=duplicate["object_id"]))
            if receipts_path:
                _remember_capture_request(receipts_path, capture_token, item["object_id"], capture["location_id"], actor)
            if _wants_json_response():
                message = f"Bereits vorhanden: #{item.get('display_id', '')} {item.get('name', '')}. Kein Duplikat angelegt."
                return _json_response({"ok": True, "saved": False, "duplicate": True, "assigned": True, **_item_result(item, capture["location_id"]), "message": message}, 200)
            return _capture_success_redirect(item, capture["location_id"])
        message = f"Bereits vorhanden: #{duplicate.get('display_id', '')} {duplicate.get('name', '')}. Kein Duplikat angelegt."
        if _wants_json_response():
            return _json_response({"ok": True, "saved": False, "duplicate": True, **_item_result(duplicate), "message": message}, 200)
        flash(message)
        return redirect(url_for("inventory.item_detail", object_id=duplicate["object_id"]))
    try:
        fields = inventory._object_fields(request.form, isbn)
        title = inventory._single_line(request.form.get("title", ""), 300)
        if capture and not title:
            message = "Bitte vor der Übernahme einen Buchtitel eingeben. Metadatenabruf und OCR sind optional."
            if _wants_json_response():
                return _json_response({"ok": False, "error": message, "fields": {"title": "missing"}}, 400)
            flash(message)
            return redirect(_capture_index_url(capture, capture_token))
        if not title:
            title = f"{item_type} {identifier}".strip() if identifier else item_type
        tags = {"Inventar"}
        tags.update(inventory._single_line(request.form.get("tags", ""), 500).split(","))
        tags = {tag.strip() for tag in tags if tag.strip()}
        if item_type.casefold() in inventory.BOOK_TYPE_NAMES or isbn:
            tags.add("Buch")
        if isbn:
            tags.add("ISBN")
        item = inventory._objects().create(
            {
                "name": title,
                "type": item_type,
                "status": "active",
                "description": str(request.form.get("description", "") or "").strip()[:8000],
                "identifier": identifier,
                "location": inventory._single_line(request.form.get("location", ""), 300),
                "expires_at": "",
                "tags": ",".join(sorted(tags, key=str.casefold)),
                "fields": inventory._fields_text(fields),
            },
            actor,
        )
    except ValueError as exc:
        message = str(exc)
        if _wants_json_response():
            return _json_response({"ok": False, "error": message, "fields": _create_error_fields(message)}, 400)
        flash(message)
        return redirect(_capture_index_url(capture, capture_token) if capture else url_for("inventory.index"))

    if capture_token and receipts_path:
        location_id = capture["location_id"] if capture else ""
        _remember_capture_request(receipts_path, capture_token, item["object_id"], location_id, actor)
    sidecar = inventory._inventory()
    sidecar.record_snapshot(item["object_id"], fields, actor)
    notices: list[str] = []
    try:
        sidecar.save_photo(item["object_id"], request.files.get("photo"), actor)
    except (OSError, ValueError) as exc:
        notices.append(f"Foto nicht gespeichert: {exc}")
    try:
        inspection = inventory._inspection_input(request.form)
        if inspection:
            task = inventory._create_inspection_task(item, inspection, actor)
            try:
                sidecar.add_inspection(item["object_id"], inspection, actor, task["id"])
            except Exception:
                inventory._todos().soft_delete(task["id"], actor)
                raise
    except (OSError, ValueError) as exc:
        notices.append(f"Prüftermin nicht angelegt: {exc}")
    if capture:
        try:
            from .library.routes import assign_object_to_location

            item = assign_object_to_location(
                item["object_id"], capture["location_id"], actor, scanned=barcode,
            )["item"]
        except Exception:
            current_app.logger.exception("inventory_capture_assignment_failed object=%s", item["object_id"])
            message = f"Buch #{item.get('display_id', '')} {item.get('name', '')} wurde gespeichert, aber das Regal konnte nicht zugeordnet werden. Bitte erneut aus der Inventarakte zuordnen."
            if _wants_json_response():
                return _json_response({"ok": True, "saved": True, "assigned": False, **_item_result(item), "notices": [message], "message": message}, 200)
            flash(message)
            return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
        if notices:
            flash(" ".join(notices))
        if _wants_json_response():
            message = f"Buch #{item.get('display_id', '')} {item.get('name', '')} wurde gespeichert."
            return _json_response({"ok": True, "saved": True, "assigned": True, **_item_result(item, capture["location_id"]), "notices": notices, "message": message}, 200)
        return _capture_success_redirect(item, capture["location_id"])
    message = f"Inventarobjekt #{item.get('display_id', '')} wurde angelegt." + (" " + " ".join(notices) if notices else "")
    if _wants_json_response():
        return _json_response({"ok": True, "saved": True, **_item_result(item), "notices": notices, "message": message}, 200)
    flash(message)
    return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
