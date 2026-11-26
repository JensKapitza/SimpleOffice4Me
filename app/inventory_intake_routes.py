"""Mobile mixed intake: private capture before any analysis or confirmation."""

from __future__ import annotations

from contextlib import nullcontext
from threading import BoundedSemaphore

from flask import (
    abort, current_app, flash, g, jsonify, make_response, redirect,
    render_template, request, url_for,
)
from werkzeug.datastructures import FileStorage

from .auth import login_required
from .capture_inbox_store import CaptureInboxConflict, CaptureInboxIndexError, CaptureInboxStore
from .capture_intake_state import (
    FIELD_LIMITS, IntakeRevisionConflict, catalog_view, compatible_selection, current_proposal, edit_draft, intake_storage_supported,
    manual_fields, selectable_fields, select_evidence, status,
)
from .file_lock import exclusive_file_lock
from .inventory import _objects, bp
from .inventory_capture import store_upload
from .inventory_intake_matches import match_key, matching_owned_copies
from .inventory_intake_evidence import local_proposal, local_target, ocr_fields
from .inventory_library_capture import (
    _capture_context, _capture_receipt_paths, _capture_receipts,
    _create_inventory_item, _inventory_module, _library_store, capture_locations,
)


_LOCAL_ANALYSIS = BoundedSemaphore(1)


def _actor() -> str:
    return str(g.user["username"])


def _store() -> CaptureInboxStore:
    return CaptureInboxStore(current_app.config["DOCUMENT_ROOT"])


def _entry(entry_id):
    entry = _store().get(entry_id, _actor())
    return entry if entry and entry.get("intake") else abort(404)


def _public(entry):
    state = entry["intake"]
    entry_id = entry["entry_id"]
    has_photo = bool(entry.get("original"))
    return {
        "id": entry_id, "created_at": entry["created_at"], "state": status(state),
        "revision": state["revision"], "sequence": state["sequence"],
        "manual": state["manual"], "ocr": state["ocr"], "catalog": catalog_view(state),
        "compatible_selection": compatible_selection(state),
        "proposal": current_proposal(state), "confirmed": state["confirmed"],
        "selected": state["selected"], "evidence": selectable_fields(state),
        "preview_url": url_for("inventory.capture_preview", entry_id=entry_id) if has_photo else None,
    }


def _result(entry=None, *, error="", code=200):
    if request.accept_mimetypes.best == "application/json":
        reply = jsonify({"ok": not error, **({"error": error} if error else {}),
                         **({"draft": _public(entry)} if entry is not None else {})})
        reply.headers["Cache-Control"] = "no-store"
        return reply, code
    if error:
        flash(error)
    anchor = f"draft-{entry['entry_id']}" if entry else None
    return redirect(url_for("inventory.mixed_intake", _anchor=anchor), code=303)


def _revision():
    value = request.form.get("revision", "")
    if not value.isascii() or not value.isdigit() or len(value) > 10:
        raise ValueError("Entwurfskennung ungültig. Bitte neu laden.")
    return int(value)


def _update(entry_id, revision, change):
    updated = _store().update_intake(entry_id, _actor(), revision, change)
    return updated if updated is not None else abort(404)


def _manual():
    values = manual_fields({k: request.form[k] for k in FIELD_LIMITS if k in request.form})
    if values.get("location_id"):
        _library_store().location(values["location_id"])
    return values


def _error(exc):
    if isinstance(exc, CaptureInboxIndexError):
        return _result(error="Entwürfe sind vorübergehend nicht verfügbar.", code=503)
    if isinstance(exc, CaptureInboxConflict):
        return _result(error="Der Entwurf für diesen Erfassungsvorgang ist bereits vorhanden. Bitte neu laden, bevor ein anderes Foto oder Barcode gespeichert wird.", code=409)
    if isinstance(exc, IntakeRevisionConflict):
        return _result(error=str(exc), code=409)
    current_app.logger.info("inventory_intake_rejected error_type=%s", type(exc).__name__)
    return _result(error="Eingaben prüfen und erneut versuchen. Der Entwurf bleibt erhalten.", code=400)


@bp.before_request
def _verify_intake_storage():
    if not g.user:
        return None
    if request.endpoint in {
        "inventory.capture_intake", "inventory.edit_intake", "inventory.ocr_intake", "inventory.confirm_intake",
        "inventory.catalog_intake", "inventory.choose_intake", "inventory.propose_intake",
        "inventory.save_and_review_intake_copy",
    } and request.method == "POST" and not intake_storage_supported(current_app.config["DOCUMENT_ROOT"]):
        return _result(error="Private Entwurfsfotos sind mit dieser Speicherart noch nicht verfügbar.", code=409)
    return None


@bp.get("/capture/intake")
@login_required
def mixed_intake():
    import secrets

    supported = intake_storage_supported(current_app.config["DOCUMENT_ROOT"])
    try:
        drafts = [_public(row) for row in _store().list_intake(_actor())] if supported else []
    except CaptureInboxIndexError:
        abort(503)
    keys = {draft["id"]: match_key(draft["manual"].get("barcode")) for draft in drafts if not draft["confirmed"]}
    known = {key for key in keys.values() if key is not None}
    matches = matching_owned_copies(_objects(), _library_store(), _actor(), known) if known else {}
    for draft in drafts:
        draft["existing_copies"] = matches.get(keys.get(draft["id"]), ())
    reply = make_response(render_template(
        "inventory/intake.html", drafts=drafts, locations=capture_locations() if supported else [],
        storage_supported=supported,
        capture_token=secrets.token_hex(16), local_ai_available=bool(local_target(current_app.config)),
    ))
    reply.headers["Cache-Control"] = "no-store"
    return reply


def _exact_copy_for_draft(row, object_id):
    state = row["intake"]
    key = match_key(state["manual"].get("barcode"))
    if state["confirmed"] or key is None:
        abort(404)
    matches = matching_owned_copies(_objects(), _library_store(), _actor(), {key}, object_id=object_id)[key]
    if not matches:
        abort(404)
    return key, matches[0]


@bp.get("/capture/intake/<entry_id>/existing/<object_id>")
@login_required
def intake_existing_copy(entry_id, object_id):
    _, item = _exact_copy_for_draft(_entry(entry_id), object_id)
    response = make_response(render_template("inventory/intake_existing_copy.html", item=item, entry_id=entry_id))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/capture/intake/<entry_id>/existing/<object_id>")
@login_required
def save_and_review_intake_copy(entry_id, object_id):
    """Keep explicit draft edits while opening a read-only lookup."""
    try:
        row = _entry(entry_id)
        key, _ = _exact_copy_for_draft(row, object_id)
        fields = _manual()
        updated = _update(entry_id, _revision(), lambda state: edit_draft(state, fields))
    except (ValueError, OSError, CaptureInboxIndexError) as exc:
        return _error(exc)
    if match_key(updated["intake"]["manual"].get("barcode")) != key:
        return _result(updated, error="Barcode im Entwurf aktualisiert. Bitte das Exemplar zum neuen Code auswählen.",
                       code=409)
    return redirect(url_for("inventory.intake_existing_copy", entry_id=entry_id, object_id=object_id), code=303)


@bp.post("/capture/intake")
@login_required
def capture_intake():
    try:
        fields = _manual()
        token = request.form.get("capture_token", "")
        upload = request.files.get("photo")
        if upload and upload.filename:
            entry, _, _ = store_upload(
                upload, token, _actor(), current_app.config["DOCUMENT_ROOT"], intake=fields,
            )
        else:
            from .inventory_catalog import normalize_catalog_code

            try:
                normalized = normalize_catalog_code(fields.get("barcode", ""))
            except ValueError:
                return _result(error="Bitte ein Foto oder einen gültigen ISBN/EAN-Barcode eingeben.", code=400)
            fields["barcode"] = normalized.value
            entry, _ = _store().create(_actor(), b"", "", token, intake=fields)
    except (ValueError, OSError, CaptureInboxIndexError) as exc:
        return _error(exc)
    # No worker, file content extraction or network lookup here.
    return _result(entry, code=201)


@bp.post("/capture/intake/<entry_id>/edit")
@login_required
def edit_intake(entry_id):
    try:
        values = _manual()
        row = _update(entry_id, _revision(), lambda state: edit_draft(state, values))
    except (ValueError, CaptureInboxIndexError) as exc:
        return _error(exc)
    return _result(row)


@bp.post("/capture/intake/<entry_id>/ocr")
@login_required
def ocr_intake(entry_id):
    from .object_vision import _run_tesseract

    try:
        row = _entry(entry_id)
        revision = _revision()
        if row["intake"]["revision"] != revision or row["intake"]["confirmed"]:
            raise IntakeRevisionConflict("Der Entwurf wurde geändert. Bitte neu laden.")
        if not row.get("original"):
            return _result(error="Dieser Entwurf hat kein Foto und braucht keine Foto-OCR.", code=422)
        path = _store().media_path(entry_id, _actor())
        if not _LOCAL_ANALYSIS.acquire(blocking=False):
            return _result(error="Lokale Erkennung läuft bereits. Bitte später erneut versuchen.", code=429)
        try:
            result = _run_tesseract(path, timeout=10)
        finally:
            _LOCAL_ANALYSIS.release()
        snippet = result.get("text", "")[:3000]
        evidence = {
            "source": "tesseract", "status": result["status"] if snippet or result["status"] != "completed" else "empty",
            "text": snippet, "fields": ocr_fields(snippet),
        }
        def apply(state):
            state.update(ocr=evidence, proposal={})
            state["revision"] += 1
            state["selected"] = {key: item for key, item in state["selected"].items()
                                 if item["kind"] not in {"ocr", "local_ai"}}

        row = _update(entry_id, revision, apply)
    except (ValueError, OSError, CaptureInboxIndexError) as exc:
        return _error(exc)
    return _result(row)


def _save_confirmed(entry, state, fields):
    """Reuse inventory's canonical write, photo and idempotent receipt."""
    from .inventory import isbn_from_barcode

    edit_draft(state, fields)
    if not compatible_selection(state):
        raise IntakeRevisionConflict("Quellen widersprechen sich. Bitte zuerst die betroffenen Auswahlen entfernen.")
    manual = {**state["manual"], **{key: row["value"] for key, row in state["selected"].items()}}
    if not manual.get("title") or manual.get("kind") not in {"book", "object"}:
        raise ValueError("Titel und Art sind erforderlich.")
    barcode = manual.get("barcode", "")
    if manual["kind"] == "object" and isbn_from_barcode(barcode):
        raise ValueError("Eine ISBN gehört zu einem Buch.")
    location_id = manual.get("location_id", "")
    capture = None
    form = {
        **manual, "item_type": manual["kind"], "share_private": "1",
        # One draft is one physical copy; repeated confirmation is a receipt.
        "allow_duplicate": "1",
    }
    if location_id and manual["kind"] == "book":
        capture = _capture_context(barcode, location_id, allow_missing_barcode=True)
    elif location_id:
        form["location"] = _library_store().location_path(location_id)

    receipt_file, lock_path = _capture_receipt_paths()
    # Separate keyspace from client-chosen legacy capture tokens.
    receipt_file = receipt_file.with_name("intake-" + receipt_file.name)
    token = entry["entry_id"]
    actor = _actor()
    with exclusive_file_lock(lock_path):
        previous = _capture_receipts(receipt_file).get(token)
        if previous:
            if previous.get("actor") != actor:
                abort(404)
            item = _objects().object(previous["object_id"])
            outcome = {"object_id": item["object_id"], "notices": [
                "Bereits gespeichert. Foto und Standort ggf. in der Inventarakte prüfen."
            ]}
        else:
            original = _store().media_path(token, actor) if entry.get("original") else None
            with original.open("rb") if original is not None else nullcontext() as stream:
                photo = FileStorage(stream=stream, filename=original.name) if original is not None else None
                response = _create_inventory_item(
                    _inventory_module(), capture, token, receipt_file,
                    form=form, photo=photo, force_json=True,
                    extra_fields={k: manual[k] for k in ("size", "variant", "edition") if manual.get(k)},
                )
            outcome = response.get_json()
            if response.status_code != 200 or not outcome.get("saved"):
                raise ValueError("Der Entwurf konnte nicht bestätigt werden.")
    state["confirmed"] = {
        "object_id": outcome["object_id"], "notices": outcome.get("notices", []),
        "fields": manual, "sources": dict(state["selected"]),
    }
    state["revision"] += 1


@bp.post("/capture/intake/<entry_id>/confirm")
@login_required
def confirm_intake(entry_id):
    try:
        row = _entry(entry_id)
        if row["intake"]["confirmed"]:
            return _result(row)
        revision = _revision()
        fields = _manual()
        saved = match_key(row["intake"]["manual"].get("barcode"))
        submitted = match_key(fields.get("barcode"))
        if "barcode" in fields and submitted is not None and submitted != saved:
            # The result list was for the saved code, not this newly entered
            # identity. Preserve the submitted edits, but create no inventory.
            updated = _update(entry_id, revision, lambda state: edit_draft(state, fields))
            return _result(updated, error="Barcode im Entwurf gespeichert. Bestand zu diesem Code prüfen und danach erneut bestätigen.",
                           code=409)
        row = _update(entry_id, revision, lambda state: _save_confirmed(row, state, fields))
    except (ValueError, OSError, CaptureInboxIndexError) as exc:
        return _error(exc)
    return _result(row)


@bp.post("/capture/intake/<entry_id>/catalog")
@login_required
def catalog_intake(entry_id):
    from .inventory_catalog import lookup_catalog

    try:
        row = _entry(entry_id)
        revision = _revision()
        state = row["intake"]
        if state["confirmed"] or state["revision"] != revision:
            raise IntakeRevisionConflict("Der Entwurf wurde geändert. Bitte neu laden.")
        if request.form.get("catalog_consent") != "1":
            raise ValueError("Externer Katalogabgleich ist optional.")
        fields = state["manual"]
        result = lookup_catalog(
            fields.get("barcode", ""), observed_title=fields.get("title", ""),
            observed_variant=fields.get("variant", ""), observed_size=fields.get("size", ""),
            observed_unit=fields.get("unit", ""),
        )
        def apply(state):
            state.update(catalog=result, proposal={})
            state["revision"] += 1
            state["selected"] = {k: v for k, v in state["selected"].items()
                                 if v["kind"] not in {"external_catalog", "local_ai"}}

        row = _update(entry_id, revision, apply)
    except (ValueError, CaptureInboxIndexError) as exc:
        return _error(exc)
    return _result(row)


@bp.post("/capture/intake/<entry_id>/choose")
@login_required
def choose_intake(entry_id):
    try:
        clear = request.form.get("clear_field", "")
        if clear:
            def remove(state):
                if clear not in FIELD_LIMITS or clear not in state["selected"]:
                    raise ValueError("Keine Auswahl für dieses Feld.")
                del state["selected"][clear]
                state["revision"] += 1

            return _result(_update(entry_id, _revision(), remove))
        source = request.form.get("source_id", "")
        if not source or len(source) > 180:
            raise ValueError("Quellenkennung ist ungültig.")
        row = _update(entry_id, _revision(), lambda state: select_evidence(state, source))
    except (ValueError, CaptureInboxIndexError) as exc:
        return _error(exc)
    return _result(row)


@bp.post("/capture/intake/<entry_id>/proposal")
@login_required
def propose_intake(entry_id):
    from .contextflow.vision import VisionError

    try:
        row = _entry(entry_id)
        revision = _revision()
        state = row["intake"]
        if state["confirmed"] or state["revision"] != revision:
            raise IntakeRevisionConflict("Der Entwurf wurde geändert. Bitte neu laden.")
        if not _LOCAL_ANALYSIS.acquire(blocking=False):
            return _result(error="Lokale Erkennung läuft bereits. Bitte später erneut versuchen.", code=429)
        try:
            proposal = local_proposal(state, current_app.config)
        except (ValueError, OSError, VisionError):
            return _result(error="Keine belegte lokale KI-Antwort. Bitte Quellen vergleichen oder manuell fortfahren.", code=422)
        finally:
            _LOCAL_ANALYSIS.release()
        if proposal is None:
            return _result(error="Lokale KI nicht verifiziert oder noch keine Quellen. Verwende ein installiertes lokales Ollama-Modell mit abgeschalteter Cloud oder fahre manuell fort.", code=422)

        def apply(state):
            state["proposal"] = {"fields": proposal}
            state["revision"] += 1
            state["selected"] = {k: v for k, v in state["selected"].items() if v["kind"] != "local_ai"}

        row = _update(entry_id, revision, apply)
    except (ValueError, CaptureInboxIndexError) as exc:
        return _error(exc)
    return _result(row)
