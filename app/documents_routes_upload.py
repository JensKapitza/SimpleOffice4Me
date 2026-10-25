"""Authenticated file and folder import routes."""
from __future__ import annotations
from pathlib import PurePosixPath
from uuid import UUID, uuid4, uuid5
from .documents_core import *  # noqa: F401,F403
from .v3_inbox_web import begin_web_inbox_upload, complete_web_inbox_upload, fail_web_inbox_upload
from .v2.contracts import StorageLocation


@bp.post("/upload")
@login_required
def upload():
    files = [item for item in request.files.getlist("files") if item and item.filename]
    if not files:
        flash("Bitte mindestens eine Datei auswählen.")
        return redirect(url_for("documents.index"))
    from .folder_import_validation import (
        decode_relative_path_manifest,
        decode_source_identity_manifest,
        stable_source_folder_id,
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
            flash(error)
            return redirect(url_for("documents.index"))
        source_identities, error = decode_source_identity_manifest(
            request.form.get("source_ids", ""), len(files)
        )
        if error:
            flash(error)
            return redirect(url_for("documents.index"))
        error = validate_source_identity_groups(relative_paths, source_identities)
        if error:
            flash(error)
            return redirect(url_for("documents.index"))
    error = validate_upload_batch(files, relative_paths, folder_import=folder_import)
    if error:
        flash(error)
        return redirect(url_for("documents.index"))
    import_root = ""
    import_id = ""
    if folder_import:
        supplied_import_id = request.form.get("import_id", "")
        if supplied_import_id and (len(supplied_import_id) != 32 or any(char not in "0123456789abcdef" for char in supplied_import_id)):
            flash("Ungültige Importkennung.")
            return redirect(url_for("documents.index"))
        # Bind client batch IDs to the authenticated actor and server secret.
        # A caller must not be able to select another user's or an old import directory.
        if supplied_import_id:
            namespace = uuid5(UUID(int=0), str(current_app.secret_key))
            import_id = uuid5(namespace, str(g.user["username"]) + ":" + supplied_import_id).hex
        else:
            import_id = uuid4().hex
        import_root = "imports/" + import_id
    stored = 0
    actor = str(g.user["username"])
    defaults = _settings().settings()["documents"]
    storage = _storage(actor)
    archive = request.form.get("archive") == "1"
    if folder_import and archive:
        flash("Ordnerimport und Archivablage können nicht kombiniert werden.")
        return redirect(url_for("documents.index"))
    max_bytes = int(current_app.config["MAX_CONTENT_LENGTH"])
    for item_index, item in enumerate(files):
        upload_name = item.filename.replace(chr(92), "/").rsplit("/", 1)[-1] if folder_import else item.filename
        source_key = f"{getattr(g, 'request_id', 'web')}:{item_index}:{item.filename}"
        inbox = begin_web_inbox_upload(current_app.config, item, actor, source_key)
        if inbox["error"]:
            flash(f"{item.filename}: {inbox['error']}")
            continue
        try:
            if folder_import:
                result = storage.import_stream_at(
                    StorageLocation(import_root + "/" + relative_paths[item_index].replace(chr(92), "/")),
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
                fail_web_inbox_upload(
                    current_app.config["DOCUMENT_ROOT"], inbox["item"], result.error.message
                )
                flash(f"{item.filename}: {result.error.message}")
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
                relative_source = PurePosixPath(
                    relative_paths[item_index].replace(chr(92), "/")
                ).parent
                if relative_source.as_posix() != ".":
                    source_path = relative_source.as_posix()
                    source_origin = {
                        "import_id": import_id,
                        "folder_id": uuid5(UUID(hex=import_id), source_path).hex,
                        "source_path": source_path,
                        "source_name": relative_source.name,
                    }
                    source_identity = source_identities[item_index]
                    if source_identity:
                        source_origin["source_identity"] = source_identity
                        source_origin["folder_id"] = stable_source_folder_id(
                            actor, source_identity, source_path,
                        )
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
            flash(f"{item.filename}: {exc}")
    if stored:
        flash(f"{stored} Datei(en) vollständig und hashbasiert importiert.")
    if folder_import and request.form.get("import_id"):
        if stored != len(files):
            return jsonify({"ok": False, "stored": stored, "expected": len(files)}), 409
        return jsonify({"ok": True, "stored": stored}), 200
    return redirect(url_for("documents.index"))
