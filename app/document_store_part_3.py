"""DocumentStore implementation part 3 of 5."""
from __future__ import annotations

from .document_store_core import *  # noqa: F401,F403


class _DocumentStorePart3:
    def inbox_page(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        """Load an inbox page from the SQLite projection, never all sidecars."""
        self.initialize()
        page = max(1, page)
        page_size = max(1, min(500, page_size))
        with self._db() as db:
            total = int(db.execute(
                """SELECT COUNT(*) FROM document_listing
                   WHERE state='new' AND has_notes=0 AND has_relationships=0"""
            ).fetchone()[0])
            rows = db.execute(
                """SELECT document_id FROM document_listing
                   WHERE state='new' AND has_notes=0 AND has_relationships=0
                   ORDER BY last_seen_at DESC, path LIMIT ? OFFSET ?""",
                (page_size, (page - 1) * page_size),
            ).fetchall()
        documents = []
        for (document_id,) in rows:
            metadata = self._read_json(self.documents / f"{document_id}.json", {})
            if metadata.get("document_id"):
                documents.append(metadata)
        return {
            "documents": documents,
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_next": page * page_size < total,
        }

    def move_document(
        self,
        reference: str,
        destination_folder: str,
        actor: str,
        *,
        destination_name: str = "",
        allow_locked: bool = False,
    ) -> dict[str, Any]:
        """Move or rename a managed file without changing its stable ID."""
        self._require_actor(actor)
        document = self.get_document(reference)
        if not allow_locked:
            self._require_document_editable(document)
        try:
            source = resolve_file_under(self.root, document.get("last_path", ""))
        except (OSError, ValueError) as exc:
            raise ValueError("only an available regular document file can be moved") from exc
        requested = Path(destination_folder.strip())
        if not destination_folder.strip() or requested.is_absolute() or ".." in requested.parts:
            raise ValueError("choose a relative destination folder inside the document store")
        if any(part in {CONTROL_DIR, HISTORY_DIR, PREVIEW_CACHE_DIR, POLICY_FILE} for part in requested.parts):
            raise ValueError("the destination folder is reserved for system metadata")
        destination_directory = (self.root / requested).resolve()
        try:
            destination_directory.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("destination must remain inside the document store") from exc
        requested_name = destination_name.strip() or source.name
        if Path(requested_name).name != requested_name or requested_name in {"", ".", "..", POLICY_FILE}:
            raise ValueError("choose a safe destination file name")
        destination = destination_directory / requested_name
        if destination == source:
            return document
        if destination.exists():
            raise ValueError("a file with the same name already exists in the destination folder")
        destination_directory.mkdir(parents=True, exist_ok=True)
        self.ensure_folder_policy(destination_directory)
        previous_path = str(document.get("last_path", ""))
        shutil.move(str(source), str(destination))
        relative_path = self.relative(destination)
        document["last_path"] = relative_path
        document["last_seen_at"] = utc_now()
        document.setdefault("location_history", []).append({"from": previous_path, "to": relative_path, "at": document["last_seen_at"], "actor": actor})
        document["location_history"] = document["location_history"][-200:]
        self._write_xattrs(destination, document["document_id"], document.get("sha256", ""), document.get("tags", []))
        self._save_document(document)
        self._refresh_search_index(document)
        with self._db() as db:
            db.execute("DELETE FROM scan_file WHERE relative_path = ?", (previous_path,))
        self._scan_file(destination)
        fingerprint_path = self.fingerprints / f"{document.get('sha256', '')}.json"
        fingerprint = self._read_json(fingerprint_path, {})
        if fingerprint:
            paths = set(fingerprint.get("paths", [])); paths.discard(previous_path); paths.add(relative_path)
            fingerprint["paths"] = sorted(paths); fingerprint["last_seen_at"] = utc_now()
            atomic_json_write(fingerprint_path, fingerprint)
        self._event("document_moved", {"document_id": document["document_id"], "from": previous_path, "to": relative_path, "actor": actor})
        self._record_revision("document_moved", actor, "documents", document["document_id"], document)
        return self.get_document(document["document_id"])

    def replace_document_via_move(
        self,
        source_reference: str,
        destination_reference: str,
        actor: str,
        *,
        expected_source_sha256: str,
        expected_destination_sha256: str,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Replace one destination from a MOVE source with recovery and rollback."""
        self._require_actor(actor)
        source = self.get_document(source_reference)
        destination = self.get_document(destination_reference)
        if source["document_id"] == destination["document_id"]:
            raise ValueError("source and destination are the same document")
        self._require_document_editable(source)
        self._require_document_editable(destination)
        try:
            source_path = resolve_file_under(self.root, source.get("last_path", ""))
            destination_path = resolve_file_under(self.root, destination.get("last_path", ""))
        except (OSError, ValueError) as exc:
            raise ValueError("MOVE replacement requires two available regular document files") from exc
        if source_path.stat().st_size > max_bytes:
            raise ValueError("document exceeds the configured upload size limit")
        source_content = source_path.read_bytes()
        source_sha256 = hashlib.sha256(source_content).hexdigest()
        destination_sha256 = sha256_file(destination_path)
        if not expected_source_sha256 or not hmac.compare_digest(expected_source_sha256, source_sha256):
            raise ValueError("source content changed since it was opened")
        if not expected_destination_sha256 or not hmac.compare_digest(expected_destination_sha256, destination_sha256):
            raise ValueError("destination content changed since it was opened")

        destination_changed = not hmac.compare_digest(source_sha256, destination_sha256)
        updated = self.replace_content(
            destination["document_id"], source_content, actor,
            expected_sha256=destination_sha256, source="webdav-move-overwrite", max_bytes=max_bytes,
        )
        try:
            deleted = self.soft_delete_document(
                source["document_id"], actor, expected_sha256=source_sha256,
            )
        except Exception as exc:
            rollback_error = ""
            if destination_changed:
                archive = self.control / "content-versions" / destination["document_id"] / destination_sha256
                try:
                    previous_content = archive.read_bytes()
                    self.replace_content(
                        destination["document_id"], previous_content, actor,
                        expected_sha256=source_sha256, source="webdav-move-overwrite-rollback",
                        max_bytes=max(max_bytes, len(previous_content)),
                    )
                except Exception as rollback_exc:
                    rollback_error = str(rollback_exc)
            rollback = {
                "source_document_id": source["document_id"],
                "destination_document_id": destination["document_id"],
                "source": str(source.get("last_path", "")),
                "destination": str(destination.get("last_path", "")),
                "reason": str(exc), "rollback_error": rollback_error,
                "rolled_back": not rollback_error, "actor": actor, "at": utc_now(),
            }
            self._event("webdav_document_replace_rolled_back", rollback)
            self._record_revision(
                "webdav_document_replace_rolled_back", actor, "documents",
                destination["document_id"], rollback,
            )
            if rollback_error:
                raise RuntimeError("MOVE replacement failed and destination rollback failed") from exc
            raise

        details = {
            "source_document_id": source["document_id"],
            "destination_document_id": destination["document_id"],
            "source": str(source.get("last_path", "")),
            "destination": str(destination.get("last_path", "")),
            "source_sha256": source_sha256,
            "previous_destination_sha256": destination_sha256,
            "recovery": deleted.get("recovery", ""),
            "actor": actor, "at": utc_now(),
        }
        self._event("webdav_document_replaced_via_move", details)
        self._record_revision(
            "webdav_document_replaced_via_move", actor, "documents",
            destination["document_id"], {**updated, "move_replacement": details},
        )
        return {"document": self.get_document(destination["document_id"]), "source_deleted": deleted}

    def replace_document_via_copy(
        self,
        source_reference: str,
        destination_reference: str,
        actor: str,
        *,
        expected_source_sha256: str,
        expected_destination_sha256: str,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Copy source bytes over a destination while preserving its identity."""
        self._require_actor(actor)
        source = self.get_document(source_reference)
        destination = self.get_document(destination_reference)
        if source["document_id"] == destination["document_id"]:
            raise ValueError("source and destination are the same document")
        self._require_document_editable(source)
        self._require_document_editable(destination)
        try:
            source_path = resolve_file_under(self.root, source.get("last_path", ""))
            destination_path = resolve_file_under(self.root, destination.get("last_path", ""))
        except (OSError, ValueError) as exc:
            raise ValueError("COPY replacement requires two available regular document files") from exc
        if source_path.stat().st_size > max_bytes:
            raise ValueError("document exceeds the configured upload size limit")

        source_content = source_path.read_bytes()
        source_sha256 = hashlib.sha256(source_content).hexdigest()
        destination_sha256 = sha256_file(destination_path)
        if not expected_source_sha256 or not hmac.compare_digest(expected_source_sha256, source_sha256):
            raise ValueError("source content changed since it was opened")
        if not expected_destination_sha256 or not hmac.compare_digest(expected_destination_sha256, destination_sha256):
            raise ValueError("destination content changed since it was opened")

        updated = self.replace_content(
            destination["document_id"], source_content, actor,
            expected_sha256=destination_sha256, source="webdav-copy-overwrite", max_bytes=max_bytes,
        )
        details = {
            "source_document_id": source["document_id"],
            "destination_document_id": destination["document_id"],
            "source": str(source.get("last_path", "")),
            "destination": str(destination.get("last_path", "")),
            "source_sha256": source_sha256,
            "previous_destination_sha256": destination_sha256,
            "actor": actor,
            "at": utc_now(),
        }
        self._event("webdav_document_replaced_via_copy", details)
        self._record_revision(
            "webdav_document_replaced_via_copy", actor, "documents",
            destination["document_id"], {**updated, "copy_replacement": details},
        )
        return self.get_document(destination["document_id"])

    def create_document_at(
        self,
        relative_path: str,
        content: bytes,
        actor: str,
        *,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Atomically create a new regular file at an existing managed folder."""
        self._require_actor(actor)
        if len(content) > max_bytes:
            raise ValueError("document exceeds the configured upload size limit")
        relative = self._safe_managed_relative_path(relative_path, require_name=True)
        destination = self.root / relative
        if not destination.parent.is_dir() or destination.parent.is_symlink():
            raise ValueError("destination collection does not exist")
        self.ensure_folder_policy(destination.parent)
        self.initialize()
        from .file_lock import exclusive_file_lock

        with exclusive_file_lock(self.control / ".document-content.lock"):
            if destination.exists():
                raise FileExistsError("destination resource already exists")
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
            try:
                with temporary.open("xb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
            self._scan_file(destination, force_hash=True)
            metadata = self.get_document(destination)
            metadata.setdefault("content_history", []).append({
                "number": 1,
                "at": utc_now(),
                "actor": actor,
                "source": "webdav",
                "previous_sha256": "",
                "sha256": metadata["sha256"],
                "previous_size": 0,
                "size": len(content),
                "archive": "",
            })
            metadata["content_revision"] = 1
            self._save_document(metadata)
            self._event("document_created", {"document_id": metadata["document_id"], "path": self.relative(destination), "actor": actor, "sha256": metadata["sha256"]})
            self._record_revision("document_created", actor, "documents", metadata["document_id"], metadata)
            return metadata

    def copy_document(self, reference: str, destination_path: str, actor: str) -> dict[str, Any]:
        """Create an independent, audited copy without carrying access grants."""
        self._require_actor(actor)
        source_metadata = self.get_document(reference)
        self._require_document_editable(source_metadata)
        try:
            source = resolve_file_under(self.root, source_metadata.get("last_path", ""))
        except (OSError, ValueError) as exc:
            raise ValueError("only an available regular document file can be copied") from exc
        relative = self._safe_managed_relative_path(destination_path, require_name=True)
        destination = self.root / relative
        if not destination.parent.is_dir() or destination.parent.is_symlink():
            raise ValueError("destination collection does not exist")
        self.ensure_folder_policy(destination.parent)
        from .file_lock import exclusive_file_lock

        with exclusive_file_lock(self.control / ".document-content.lock"):
            if destination.exists():
                raise FileExistsError("destination resource already exists")
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
            try:
                shutil.copyfile(source, temporary)
                if sha256_file(temporary) != sha256_file(source):
                    raise RuntimeError("copied document could not be verified")
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
            self._scan_file(destination, force_hash=True)
            copied = self.get_document(destination)
            copied["tags"] = list(source_metadata.get("tags", []))
            copied["tagged_at"] = dict(source_metadata.get("tagged_at", {}))
            copied["attributes"] = {
                key: value for key, value in source_metadata.get("attributes", {}).items()
                if key in {"description", "attachment_origin", "malware_scan"}
            }
            copied["attributes"]["copied_from"] = source_metadata["document_id"]
            self._write_xattrs(destination, copied["document_id"], copied.get("sha256", ""), copied["tags"])
            self._save_document(copied)
            self._refresh_search_index(copied)
            details = {"document_id": copied["document_id"], "copied_from": source_metadata["document_id"], "path": self.relative(destination), "actor": actor}
            self._event("document_copied", details)
            self._record_revision("document_copied", actor, "documents", copied["document_id"], copied)
            return copied

    def collection_manifest(
        self,
        relative_path: str,
        actor: str,
        *,
        depth: str = "infinity",
        max_members: int = MAX_WEBDAV_COLLECTION_MEMBERS,
        max_depth: int = MAX_WEBDAV_COLLECTION_DEPTH,
    ) -> dict[str, Any]:
        """Preflight a bounded collection tree without following unsafe nodes."""
        self._require_actor(actor)
        relative = self._safe_managed_relative_path(relative_path, require_name=True)
        source = self.root / relative
        if not source.is_dir() or source.is_symlink():
            raise ValueError("source collection does not exist")
        if depth == "0":
            return {
                "source": source, "source_relative": str(relative), "directories": [Path(".")],
                "files": [], "member_count": 0, "total_bytes": 0,
            }
        if depth != "infinity":
            raise ValueError("collection operation requires Depth: 0 or infinity")
        directories: list[Path] = [Path(".")]
        files: list[dict[str, Any]] = []
        total_bytes = 0
        member_count = 0
        for current, names, filenames in os.walk(source, topdown=True, followlinks=False):
            parent = Path(current)
            nested_parent = parent.relative_to(source)
            if len(nested_parent.parts) > max_depth:
                raise ValueError("collection exceeds the supported nesting depth")
            safe_names: list[str] = []
            for name in sorted(names, key=str.casefold):
                child = parent / name
                if name == PREVIEW_CACHE_DIR:
                    continue
                if name == CONTROL_DIR:
                    if child.is_symlink() or not child.is_dir():
                        raise ValueError("collection contains unsafe internal metadata")
                    for sidecar in child.iterdir():
                        if sidecar.is_symlink() or not sidecar.is_file():
                            raise ValueError("collection contains unknown internal metadata")
                        try:
                            document_id = str(uuid.UUID(sidecar.stem))
                        except (ValueError, AttributeError):
                            raise ValueError("collection contains unknown internal metadata") from None
                        portable = self._read_json(sidecar, {})
                        if portable.get("document_id") != document_id:
                            raise ValueError("collection contains unknown internal metadata")
                        portable_value = str(portable.get("last_path", ""))
                        try:
                            portable_relative = self._safe_managed_relative_path(portable_value, require_name=True)
                        except ValueError:
                            raise ValueError("collection contains out-of-scope internal metadata") from None
                        portable_path = self.root / portable_relative
                        try:
                            portable_path.resolve(strict=False).relative_to(source.resolve())
                        except (OSError, ValueError):
                            raise ValueError("collection contains out-of-scope internal metadata") from None
                        registered = self.get_document(document_id)
                        active_match = False
                        if registered.get("last_path") == portable_value:
                            try:
                                resolve_file_under(self.root, portable_relative)
                                active_match = True
                            except (OSError, ValueError):
                                active_match = False
                        deleted_match = (
                            registered.get("system_state") == "webdav_deleted"
                            and registered.get("deleted_from") == portable_value
                            and not portable_path.exists()
                        )
                        if not active_match and not deleted_match:
                            raise ValueError("collection contains stale internal metadata")
                    continue
                if name == HISTORY_DIR:
                    raise ValueError("collection contains a reserved history directory")
                if child.is_symlink() or not child.is_dir():
                    raise ValueError("collection contains a symbolic link or special directory")
                nested = child.relative_to(source)
                if len(nested.parts) > max_depth:
                    raise ValueError("collection exceeds the supported nesting depth")
                directories.append(nested)
                safe_names.append(name)
                member_count += 1
            names[:] = safe_names
            for name in sorted(filenames, key=str.casefold):
                if name == POLICY_FILE:
                    policy = parent / name
                    if policy.is_symlink() or not policy.is_file():
                        raise ValueError("collection contains an unsafe folder policy")
                    loaded_policy = self._read_json(policy, {})
                    if not isinstance(loaded_policy, dict) or not loaded_policy.get("folder_id"):
                        raise ValueError("collection contains an invalid folder policy")
                    continue
                child = parent / name
                if child.is_symlink() or not child.is_file():
                    raise ValueError("collection contains a symbolic link or special file")
                nested = child.relative_to(source)
                if len(nested.parts) > max_depth:
                    raise ValueError("collection exceeds the supported nesting depth")
                document = self.get_document(child)
                self._require_document_editable(document)
                size = child.stat().st_size
                files.append({"nested": nested, "document": document, "size": size})
                total_bytes += size
                member_count += 1
            if member_count > max_members:
                raise ValueError("collection contains too many resources for one operation")
        return {
            "source": source,
            "source_relative": str(relative),
            "directories": directories,
            "files": files,
            "member_count": member_count,
            "total_bytes": total_bytes,
        }

    def copy_collection(
        self,
        source_path: str,
        destination_path: str,
        actor: str,
        *,
        depth: str = "infinity",
    ) -> dict[str, Any]:
        """Copy a collection with new IDs/grants and recoverable rollback."""
        if depth not in {"0", "infinity"}:
            raise ValueError("collection COPY requires Depth: 0 or infinity")
        manifest = self.collection_manifest(source_path, actor, depth=depth)
        destination_relative = self._safe_managed_relative_path(destination_path, require_name=True)
        destination = self.root / destination_relative
        source = manifest["source"]
        if source == destination or source in destination.parents:
            raise ValueError("a collection cannot be copied into itself")
        if destination.exists():
            raise FileExistsError("destination resource already exists")
        if not destination.parent.is_dir() or destination.parent.is_symlink():
            raise ValueError("destination parent collection does not exist")
        directories = manifest["directories"] if depth == "infinity" else [Path(".")]
        file_entries = manifest["files"] if depth == "infinity" else []
        created_documents: list[dict[str, Any]] = []
        created_directories: list[Path] = []
        try:
            for nested in sorted(directories, key=lambda item: (len(item.parts), str(item).casefold())):
                target = destination if nested == Path(".") else destination / nested
                self.create_collection(self.relative(target), actor)
                created_directories.append(target)
            for entry in file_entries:
                target = destination / entry["nested"]
                copied = self.copy_document(entry["document"]["document_id"], self.relative(target), actor)
                created_documents.append({
                    "source": manifest["source"] / entry["nested"],
                    "source_document": entry["document"],
                    "destination": target,
                    "destination_document": copied,
                })
        except Exception:
            for item in reversed(created_documents):
                try:
                    self.soft_delete_document(item["destination_document"]["document_id"], actor)
                except (OSError, ValueError):
                    pass
            for directory in sorted(created_directories, key=lambda item: len(item.parts), reverse=True):
                try:
                    self.delete_empty_collection(self.relative(directory), actor)
                except (OSError, ValueError):
                    pass
            self._record_revision(
                "webdav_collection_copy_rolled_back", actor, "collections",
                hashlib.sha256(f"{source_path}:{destination_path}".encode()).hexdigest(),
                {"source": source_path, "destination": destination_path, "at": utc_now(), "actor": actor},
            )
            raise
        details = {
            "source": manifest["source_relative"],
            "destination": str(destination_relative),
            "depth": depth,
            "collections": len(created_directories),
            "documents": len(created_documents),
            "bytes": sum(int(item["destination"].stat().st_size) for item in created_documents),
            "actor": actor,
            "at": utc_now(),
        }
        self._event("webdav_collection_copied", details)
        self._record_revision(
            "webdav_collection_copied", actor, "collections",
            hashlib.sha256(str(destination_relative).encode()).hexdigest(), details,
        )
        return {**details, "resources": created_documents, "directories_relative": directories}

    def move_collection(self, source_path: str, destination_path: str, actor: str) -> dict[str, Any]:
        """Atomically remap a collection and retain every document's stable ID."""
        manifest = self.collection_manifest(source_path, actor)
        destination_relative = self._safe_managed_relative_path(destination_path, require_name=True)
        destination = self.root / destination_relative
        source = manifest["source"]
        if source == self.root:
            raise ValueError("the document root cannot be moved")
        if source == destination or source in destination.parents:
            raise ValueError("a collection cannot be moved into itself")
        if destination.exists():
            raise FileExistsError("destination resource already exists")
        if not destination.parent.is_dir() or destination.parent.is_symlink():
            raise ValueError("destination parent collection does not exist")
        snapshots = {
            entry["document"]["document_id"]: json.loads(json.dumps(entry["document"]))
            for entry in manifest["files"]
        }
        moved_documents: list[dict[str, Any]] = []
        from .file_lock import exclusive_file_lock

        with exclusive_file_lock(self.control / ".document-content.lock"):
            source.replace(destination)
            try:
                changed_at = utc_now()
                for entry in manifest["files"]:
                    document = json.loads(json.dumps(entry["document"]))
                    previous_path = str(document.get("last_path", ""))
                    target = destination / entry["nested"]
                    relative = self.relative(target)
                    document["last_path"] = relative
                    document["last_seen_at"] = changed_at
                    document.setdefault("location_history", []).append({
                        "from": previous_path, "to": relative, "at": changed_at, "actor": actor,
                    })
                    document["location_history"] = document["location_history"][-200:]
                    self._write_xattrs(target, document["document_id"], document.get("sha256", ""), document.get("tags", []))
                    self._save_document(document)
                    self._refresh_search_index(document)
                    with self._db() as db:
                        db.execute("DELETE FROM scan_file WHERE relative_path = ?", (previous_path,))
                    self._scan_file(target)
                    moved_documents.append({"before": previous_path, "after": relative, "document": document})
                for item in moved_documents:
                    fingerprint_path = self.fingerprints / f"{item['document'].get('sha256', '')}.json"
                    fingerprint = self._read_json(fingerprint_path, {})
                    if fingerprint:
                        paths = set(fingerprint.get("paths", []))
                        paths.discard(item["before"])
                        paths.add(item["after"])
                        fingerprint["paths"] = sorted(paths)
                        fingerprint["last_seen_at"] = changed_at
                        atomic_json_write(fingerprint_path, fingerprint)
            except Exception:
                if destination.exists() and not source.exists():
                    destination.replace(source)
                for document_id, snapshot in snapshots.items():
                    self._save_document(snapshot)
                    self._refresh_search_index(snapshot)
                    try:
                        old_path = resolve_file_under(self.root, snapshot.get("last_path", ""))
                    except (OSError, ValueError):
                        continue
                    self._scan_file(old_path)
                self._record_revision(
                    "webdav_collection_move_rolled_back", actor, "collections",
                    hashlib.sha256(f"{source_path}:{destination_path}".encode()).hexdigest(),
                    {"source": source_path, "destination": destination_path, "at": utc_now(), "actor": actor},
                )
                raise
        for item in moved_documents:
            details = {
                "document_id": item["document"]["document_id"], "from": item["before"],
                "to": item["after"], "actor": actor,
            }
            self._event("document_moved", details)
            self._record_revision("document_moved", actor, "documents", item["document"]["document_id"], item["document"])
        details = {
            "source": manifest["source_relative"], "destination": str(destination_relative),
            "collections": len(manifest["directories"]), "documents": len(moved_documents),
            "bytes": manifest["total_bytes"], "actor": actor, "at": utc_now(),
        }
        self._event("webdav_collection_moved", details)
        self._record_revision(
            "webdav_collection_moved", actor, "collections",
            hashlib.sha256(str(destination_relative).encode()).hexdigest(), details,
        )
        return {**details, "resources": moved_documents, "directories_relative": manifest["directories"]}

    def soft_delete_collection(self, source_path: str, actor: str) -> dict[str, Any]:
        """Atomically unmap a collection and retain every document for recovery."""
        self._require_actor(actor)
        from .file_lock import exclusive_file_lock

        with exclusive_file_lock(self.control / ".document-content.lock"):
            manifest = self.collection_manifest(source_path, actor)
            source = manifest["source"]
            if source == self.root:
                raise ValueError("the document root cannot be deleted")
            deletion_id = str(uuid.uuid4())
            operation = self.control / COLLECTION_TRASH_DIR / deletion_id
            staged = operation / "tree"
            snapshots = {
                entry["document"]["document_id"]: json.loads(json.dumps(entry["document"]))
                for entry in manifest["files"]
            }
            entries = {
                entry["document"]["document_id"]: {
                    "nested": str(entry["nested"]),
                    "deleted_from": str(entry["document"].get("last_path", "")),
                    "sha256": str(entry["document"].get("sha256", "")),
                    "size": int(entry["size"]),
                }
                for entry in manifest["files"]
            }
            operation.mkdir(parents=True)
            operation_manifest = {
                "version": 1,
                "deletion_id": deletion_id,
                "state": "prepared",
                "source": manifest["source_relative"],
                "actor": actor,
                "prepared_at": utc_now(),
                "directories": [str(item) for item in manifest["directories"]],
                "entries": entries,
                "document_snapshots": snapshots,
            }
            manifest_path = operation / "manifest.json"
            deleted_documents: list[dict[str, Any]] = []
            try:
                atomic_json_write(manifest_path, operation_manifest)
                source.replace(staged)
                operation_manifest["state"] = "staged"
                operation_manifest["staged_at"] = utc_now()
                atomic_json_write(manifest_path, operation_manifest)
                deleted_at = utc_now()
                for entry in manifest["files"]:
                    metadata = json.loads(json.dumps(entry["document"]))
                    previous_path = str(metadata.get("last_path", ""))
                    recovery = staged / entry["nested"]
                    if recovery.is_symlink() or not recovery.is_file():
                        raise RuntimeError("collection recovery payload became unavailable")
                    if sha256_file(recovery) != str(metadata.get("sha256", "")):
                        raise RuntimeError("collection recovery payload failed integrity verification")
                    metadata["deleted_at"] = deleted_at
                    metadata["deleted_by"] = actor
                    metadata["deleted_from"] = previous_path
                    metadata["deleted_collection_root"] = manifest["source_relative"]
                    metadata["collection_recovery_id"] = deletion_id
                    metadata["recovery_path"] = str(recovery.relative_to(self.control))
                    metadata["last_path"] = ""
                    metadata["system_state"] = "webdav_deleted"
                    metadata.setdefault("location_history", []).append({
                        "from": previous_path, "to": "[webdav-collection-trash]",
                        "at": deleted_at, "actor": actor,
                    })
                    metadata["location_history"] = metadata["location_history"][-200:]
                    self._save_document(metadata)
                    self._refresh_search_index(metadata)
                    with self._db() as db:
                        db.execute("DELETE FROM scan_file WHERE relative_path = ?", (previous_path,))
                    fingerprint_path = self.fingerprints / f"{metadata.get('sha256', '')}.json"
                    fingerprint = self._read_json(fingerprint_path, {})
                    if fingerprint:
                        fingerprint["paths"] = sorted(set(fingerprint.get("paths", [])) - {previous_path})
                        fingerprint["last_seen_at"] = deleted_at
                        atomic_json_write(fingerprint_path, fingerprint)
                    deleted_documents.append(metadata)
                operation_manifest["state"] = "committed"
                operation_manifest["committed_at"] = utc_now()
                operation_manifest.pop("document_snapshots", None)
                atomic_json_write(manifest_path, operation_manifest)
            except Exception:
                if staged.exists() and not source.exists():
                    staged.replace(source)
                for snapshot in snapshots.values():
                    self._save_document(snapshot)
                    self._refresh_search_index(snapshot)
                    try:
                        original = resolve_file_under(self.root, snapshot.get("last_path", ""))
                    except (OSError, ValueError):
                        continue
                    self._scan_file(original, force_hash=True)
                manifest_path.unlink(missing_ok=True)
                if operation.exists() and not any(operation.iterdir()):
                    operation.rmdir()
                self._record_revision(
                    "webdav_collection_delete_rolled_back", actor, "collections",
                    hashlib.sha256(source_path.encode()).hexdigest(),
                    {"source": source_path, "at": utc_now(), "actor": actor},
                )
                raise
            for metadata in deleted_documents:
                details = {
                    "document_id": metadata["document_id"],
                    "from": metadata["deleted_from"],
                    "deleted_at": metadata["deleted_at"],
                    "actor": actor,
                    "recovery": metadata["recovery_path"],
                    "collection_recovery_id": deletion_id,
                }
                self._event("document_soft_deleted", details)
                self._record_revision(
                    "document_soft_deleted", actor, "documents", metadata["document_id"], metadata,
                )
            details = {
                "deletion_id": deletion_id,
                "path": manifest["source_relative"],
                "collections": len(manifest["directories"]),
                "documents": len(deleted_documents),
                "bytes": manifest["total_bytes"],
                "actor": actor,
                "at": utc_now(),
            }
            self._event("webdav_collection_soft_deleted", details)
            self._record_revision(
                "webdav_collection_soft_deleted", actor, "collections",
                hashlib.sha256(manifest["source_relative"].encode()).hexdigest(), details,
            )
            return {
                **details,
                "resources": deleted_documents,
                "directories_relative": manifest["directories"],
            }
