"""Authenticated file and folder import routes."""
from __future__ import annotations
from uuid import UUID, uuid4, uuid5
from .documents_core import *  # noqa: F401,F403
from .v3_inbox_web import begin_web_inbox_upload, complete_web_inbox_upload, fail_web_inbox_upload
from .v2.contracts import ErrorCode, StorageLocation
from .folder_import_recovery import existing_matching_folder_import, folder_import_origin


@bp.post("/upload")
@login_required
def upload():
    files = [item for item in request.files.getlist("files") if item and item.filename]
    batch_upload = request.form.get("batch_upload") == "1"
    json_response = batch_upload or (
        request.form.get("folder_import") == "1" and bool(request.form.get("import_id"))
    )

    def failure(message: str, status: int = 400):
        if json_response:
            return jsonify({"ok": False, "stored": 0, "expected": len(files),
                            "retryable": False, "error": message}), status
        flash(message)
        return redirect(url_for("documents.index"))

    if not files:
        return failure("Bitte mindestens eine Datei auswählen.")
    from .folder_import_validation import (
        decode_relative_path_manifest,
        decode_source_identity_manifest,
        validate_source_identity_groups,
        validate_upload_batch,
    )
    folder_import = request.form.get("folder_import") == "1"
    relative_paths = []
    source_identities = [""] * len(files)
    if folder_import:
        relative_paths, error = decode_relative_path_manifest(
            request.form.get("relative_paths", ""), len(files)
        )
        if error:
            return failure(error)
        source_identities, error = decode_source_identity_manifest(
            request.form.get("source_ids", ""), len(files)
        )
        if error:
            return failure(error)
        error = validate_source_identity_groups(relative_paths, source_identities)
        if error:
            return failure(error)
    error = validate_upload_batch(files, relative_paths, folder_import=folder_import)
    if error:
        return failure(error)
    import_root = ""
    import_id = ""
    resumable_import = False
    if folder_import:
        supplied_import_id = request.form.get("import_id", "")
        if supplied_import_id and (len(supplied_import_id) != 32 or any(char not in "0123456789abcdef" for char in supplied_import_id)):
            return failure("Ungültige Importkennung.")
        # Bind client batch IDs to the authenticated actor and server secret.
        # A caller must not be able to select another user's or an old import directory.
        if supplied_import_id:
            resumable_import = True
            namespace = uuid5(UUID(int=0), str(current_app.secret_key))
            import_id = uuid5(namespace, str(g.user["username"]) + ":" + supplied_import_id).hex
        else:
            import_id = uuid4().hex
        import_root = "imports/" + import_id
    stored = 0
    errors = []
    retryable = True
    actor = str(g.user["username"])
    defaults = _settings().settings()["documents"]
    storage = _storage(actor)
    archive = request.form.get("archive") == "1"
    if folder_import and archive:
        return failure("Ordnerimport und Archivablage können nicht kombiniert werden.")
    max_bytes = int(current_app.config["MAX_CONTENT_LENGTH"])
    for item_index, item in enumerate(files):
        upload_name = item.filename.replace(chr(92), "/").rsplit("/", 1)[-1] if folder_import else item.filename
        relative_path = relative_paths[item_index].replace(chr(92), "/") if folder_import else ""
        existing = None
        if resumable_import:
            existing = existing_matching_folder_import(
                _store(), current_app.config["DOCUMENT_ROOT"],
                import_root + "/" + relative_path, relative_path, import_id,
                actor, source_identities[item_index], item.stream, current_app.logger,
            )
        if existing is not None:
            stored += 1
            continue
        source_key = f"{getattr(g, 'request_id', 'web')}:{item_index}:{item.filename}"
        inbox = begin_web_inbox_upload(current_app.config, item, actor, source_key)
        if inbox["error"]:
            message = f"{item.filename}: {inbox['error']}"
            errors.append(message)
            flash(message)
            continue
        try:
            if folder_import:
                result = storage.import_stream_at(
                    StorageLocation(import_root + "/" + relative_path),
                    item.stream,
                    max_bytes=max_bytes,
                )
            else:
                result = storage.import_stream(
                    item.stream,
                    upload_name,
                    archive=archive,
                    max_bytes=max_bytes,
                )
            if not result.ok:
                if result.error.code == ErrorCode.CONFLICT and resumable_import:
                    item.stream.seek(0)
                    existing = existing_matching_folder_import(
                        _store(), current_app.config["DOCUMENT_ROOT"],
                        import_root + "/" + relative_path, relative_path, import_id,
                        actor, source_identities[item_index], item.stream, current_app.logger,
                    )
                    if existing is not None:
                        complete_web_inbox_upload(
                            current_app.config["DOCUMENT_ROOT"], inbox,
                            source_key=source_key, original_name=item.filename,
                            actor=actor, document_id=existing["document_id"],
                            imported_metadata=existing, imported_size=int(existing.get("size") or 0),
                            mime_type=item.mimetype or "",
                        )
                        stored += 1
                        continue
                if result.error.code == ErrorCode.CONFLICT:
                    retryable = False
                fail_web_inbox_upload(
                    current_app.config["DOCUMENT_ROOT"], inbox["item"], result.error.message
                )
                message = f"{item.filename}: {result.error.message}"
                errors.append(message)
                flash(message)
                continue
            if defaults["default_tags"] or defaults["default_state"] != "new":
                metadata = _store().get_document(result.value.object_id.value)
                if defaults["default_tags"]:
                    _store().set_tags(
                        metadata["document_id"],
                        [*metadata.get("tags", []), *defaults["default_tags"]],
                        actor,
                    )
                if defaults["default_state"] != "new":
                    _store().set_state(metadata["document_id"], defaults["default_state"], actor)
            document_id = result.value.object_id.value
            if folder_import:
                source_origin = folder_import_origin(
                    import_id, relative_path, actor, source_identities[item_index],
                )
                if source_origin:
                    try:
                        _store().update_metadata(
                            document_id,
                            attributes={"folder_import_origin": source_origin},
                            author=actor,
                        )
                    except (OSError, RuntimeError, ValueError) as exc:
                        current_app.logger.warning(
                            "Folder import provenance could not be stored for document %s (%s)",
                            document_id,
                            type(exc).__name__,
                        )
                        flash(
                            f"{upload_name}: Datei gespeichert, Herkunftsordner konnte nicht protokolliert werden."
                        )
            imported_metadata = _store().get_document(document_id)
            complete_web_inbox_upload(
                current_app.config["DOCUMENT_ROOT"],
                inbox,
                source_key=source_key,
                original_name=item.filename,
                actor=actor,
                document_id=document_id,
                imported_metadata=imported_metadata,
                imported_size=result.value.size,
                mime_type=item.mimetype or "",
            )
            stored += 1
        except (OSError, ValueError) as exc:
            fail_web_inbox_upload(current_app.config["DOCUMENT_ROOT"], inbox["item"], str(exc))
            message = f"{item.filename}: {exc}"
            errors.append(message)
            flash(message)
    if stored:
        flash(f"{stored} Datei(en) vollständig und hashbasiert importiert.")
    if json_response:
        if stored != len(files):
            return jsonify({"ok": False, "stored": stored, "expected": len(files),
                            "retryable": retryable, "errors": errors}), 409
        return jsonify({"ok": True, "stored": stored}), 200
    return redirect(url_for("documents.index"))
