"""DocumentStore implementation part 4 of 5."""
from __future__ import annotations

from .document_store_core import *  # noqa: F401,F403


class _DocumentStorePart4:
    def soft_delete_document(self, reference: str, actor: str, *, expected_sha256: str = "") -> dict[str, Any]:
        """Move a document into a private recovery area and retain its metadata."""
        self._require_actor(actor)
        from .file_lock import exclusive_file_lock

        with exclusive_file_lock(self.control / ".document-content.lock"):
            metadata = self.get_document(reference)
            self._require_document_editable(metadata)
            try:
                source = resolve_file_under(self.root, metadata.get("last_path", ""))
            except (OSError, ValueError) as exc:
                raise ValueError("only an available regular document file can be deleted") from exc
            if expected_sha256 and not hmac.compare_digest(expected_sha256, sha256_file(source)):
                raise ValueError("document content changed since it was opened")
            previous_path = self.relative(source)
            deleted_at = utc_now()
            trash = self.control / "webdav-trash" / metadata["document_id"]
            trash.mkdir(parents=True, exist_ok=True)
            destination = trash / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}--{source.name}"
            source.replace(destination)
            metadata["deleted_at"] = deleted_at
            metadata["deleted_by"] = actor
            metadata["deleted_from"] = previous_path
            metadata["recovery_path"] = str(destination.relative_to(self.control))
            metadata["last_path"] = ""
            metadata["system_state"] = "webdav_deleted"
            metadata.setdefault("location_history", []).append({"from": previous_path, "to": "[webdav-trash]", "at": deleted_at, "actor": actor})
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
            details = {"document_id": metadata["document_id"], "from": previous_path, "deleted_at": deleted_at, "actor": actor, "recovery": str(destination.relative_to(self.control))}
            self._event("document_soft_deleted", details)
            self._record_revision("document_soft_deleted", actor, "documents", metadata["document_id"], metadata)
            return details

    @staticmethod
    def _recovery_owner(metadata: dict[str, Any]) -> str:
        actor = str(metadata.get("deleted_by", ""))
        if not actor:
            actor = next((str(item.get("actor", "")) for item in reversed(metadata.get("location_history", [])) if item.get("to") == "[webdav-trash]"), "")
        return actor.removeprefix("webdav:")

    def _recovery_file(self, metadata: dict[str, Any]) -> Path:
        collection_recovery_id = str(metadata.get("collection_recovery_id", ""))
        if collection_recovery_id:
            try:
                collection_recovery_id = str(uuid.UUID(collection_recovery_id))
            except ValueError:
                raise ValueError("collection recovery identity is invalid") from None
            operation = self.control / COLLECTION_TRASH_DIR / collection_recovery_id
            manifest = self._read_json(operation / "manifest.json", {})
            entry = manifest.get("entries", {}).get(metadata.get("document_id", ""))
            if (
                manifest.get("deletion_id") != collection_recovery_id
                or manifest.get("state") != "committed"
                or not isinstance(entry, dict)
                or entry.get("sha256") != metadata.get("sha256")
            ):
                raise ValueError("collection recovery manifest is unavailable")
            tree = operation / "tree"
            candidate = tree / str(entry.get("nested", ""))
            try:
                candidate.resolve().relative_to(tree.resolve())
            except (OSError, ValueError):
                raise ValueError("collection recovery path is invalid") from None
            expected_path = str(candidate.relative_to(self.control))
            if metadata.get("recovery_path") != expected_path:
                raise ValueError("collection recovery path does not match its manifest")
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
            raise ValueError("collection recovery payload is unavailable")
        trash_root = self.control / "webdav-trash" / metadata["document_id"]
        recovery_path = str(metadata.get("recovery_path", ""))
        candidates = [self.control / recovery_path] if recovery_path else sorted(trash_root.glob("*"), reverse=True)
        for candidate in candidates:
            try:
                candidate.resolve().relative_to(trash_root.resolve())
            except (OSError, ValueError):
                continue
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
        raise ValueError("recovery payload is unavailable")

    def recovery_items(self, actor: str) -> list[dict[str, Any]]:
        """List only soft-deleted documents owned by the authenticated user."""
        self._require_actor(actor)
        items = []
        for metadata in self._all_documents():
            if metadata.get("system_state") != "webdav_deleted" or self._recovery_owner(metadata) != actor:
                continue
            try:
                recovery = self._recovery_file(metadata)
                size = recovery.stat().st_size
                available = True
            except (OSError, ValueError):
                size = 0
                available = False
            items.append({
                "document_id": metadata["document_id"],
                "deleted_from": str(metadata.get("deleted_from", "")),
                "deleted_at": str(metadata.get("deleted_at", "")),
                "sha256": str(metadata.get("sha256", "")),
                "size": size,
                "available": available,
            })
        return sorted(items, key=lambda item: item["deleted_at"], reverse=True)

    def restore_soft_deleted(self, reference: str, destination_path: str, expected_sha256: str, actor: str) -> dict[str, Any]:
        """Atomically restore a user's verified WebDAV deletion without overwriting."""
        self._require_actor(actor)
        from .file_lock import exclusive_file_lock

        with exclusive_file_lock(self.control / ".document-content.lock"):
            metadata = self.get_document(reference)
            if metadata.get("system_state") != "webdav_deleted":
                raise ValueError("document is not in WebDAV recovery")
            if self._recovery_owner(metadata) != actor:
                raise PermissionError("document recovery belongs to another user")
            source = self._recovery_file(metadata)
            actual_sha256 = sha256_file(source)
            if not expected_sha256 or not hmac.compare_digest(expected_sha256, str(metadata.get("sha256", ""))):
                raise ValueError("recovery entry changed since the page was opened")
            if not hmac.compare_digest(actual_sha256, expected_sha256):
                raise ValueError("recovery payload failed integrity verification")
            relative = self._safe_managed_relative_path(destination_path or str(metadata.get("deleted_from", "")), require_name=True)
            destination = self.root / relative
            if not destination.parent.is_dir() or destination.parent.is_symlink():
                raise ValueError("destination collection does not exist")
            if destination.exists():
                raise FileExistsError("destination already exists; recovery never overwrites a file")
            self.ensure_folder_policy(destination.parent)
            source.replace(destination)
            restored_at = utc_now()
            previous_recovery = str(metadata.get("recovery_path", source.relative_to(self.control)))
            metadata["last_path"] = self.relative(destination)
            metadata["last_seen_at"] = restored_at
            metadata["system_state"] = "indexed"
            metadata["restored_at"] = restored_at
            metadata["restored_by"] = actor
            metadata.setdefault("location_history", []).append({"from": "[webdav-trash]", "to": metadata["last_path"], "at": restored_at, "actor": actor})
            metadata["location_history"] = metadata["location_history"][-200:]
            metadata.setdefault("recovery_history", []).append({
                "deleted_at": metadata.get("deleted_at", ""), "deleted_by": metadata.get("deleted_by", ""),
                "recovery": previous_recovery, "restored_at": restored_at, "restored_by": actor,
                "destination": metadata["last_path"], "sha256": actual_sha256,
                "collection_recovery_id": metadata.get("collection_recovery_id", ""),
                "deleted_collection_root": metadata.get("deleted_collection_root", ""),
            })
            metadata["recovery_history"] = metadata["recovery_history"][-200:]
            metadata.pop("recovery_path", None)
            metadata.pop("collection_recovery_id", None)
            metadata.pop("deleted_collection_root", None)
            self._write_xattrs(destination, metadata["document_id"], actual_sha256, metadata.get("tags", []))
            self._save_document(metadata)
            self._refresh_search_index(metadata)
            stat = destination.stat()
            with self._db() as db:
                db.execute(
                    """INSERT INTO scan_file(relative_path, document_id, sha256, size, modified_ns, device, inode, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(relative_path) DO UPDATE SET document_id=excluded.document_id, sha256=excluded.sha256,
                         size=excluded.size, modified_ns=excluded.modified_ns, device=excluded.device, inode=excluded.inode,
                         last_seen_at=excluded.last_seen_at""",
                    (metadata["last_path"], metadata["document_id"], actual_sha256, stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino, restored_at),
                )
            fingerprint_path = self.fingerprints / f"{actual_sha256}.json"
            fingerprint = self._read_json(fingerprint_path, {})
            fingerprint.update({
                "sha256": actual_sha256, "first_seen_at": fingerprint.get("first_seen_at", metadata.get("first_seen_at", restored_at)),
                "last_seen_at": restored_at, "paths": sorted({*fingerprint.get("paths", []), metadata["last_path"]}),
                "seen_count": int(fingerprint.get("seen_count", 0)) + 1,
            })
            atomic_json_write(fingerprint_path, fingerprint)
            details = {"document_id": metadata["document_id"], "to": metadata["last_path"], "restored_at": restored_at, "actor": actor, "sha256": actual_sha256}
            self._event("document_restored", details)
            self._record_revision("document_restored", actor, "documents", metadata["document_id"], metadata)
            return metadata

    def create_collection(self, relative_path: str, actor: str) -> Path:
        """Create exactly one collection; RFC 4918 requires its parent to exist."""
        self._require_actor(actor)
        relative = self._safe_managed_relative_path(relative_path, require_name=True)
        destination = self.root / relative
        if not destination.parent.is_dir() or destination.parent.is_symlink():
            raise ValueError("parent collection does not exist")
        if destination.exists():
            raise FileExistsError("destination collection already exists")
        destination.mkdir()
        self.ensure_folder_policy(destination, actor)
        details = {"path": self.relative(destination), "actor": actor, "at": utc_now()}
        self._event("webdav_collection_created", details)
        self._record_revision("webdav_collection_created", actor, "collections", hashlib.sha256(details["path"].encode()).hexdigest(), details)
        return destination

    def delete_empty_collection(self, relative_path: str, actor: str) -> None:
        """Delete an empty collection while leaving retention-managed contents alone."""
        self._require_actor(actor)
        relative = self._safe_managed_relative_path(relative_path, require_name=True)
        collection = self.root / relative
        if not collection.is_dir() or collection.is_symlink():
            raise ValueError("collection does not exist")
        visible = [item for item in collection.iterdir() if item.name not in {POLICY_FILE, CONTROL_DIR, PREVIEW_CACHE_DIR}]
        if visible:
            raise ValueError("collection is not empty")
        sidecars = collection / CONTROL_DIR
        if sidecars.exists():
            if not sidecars.is_dir() or sidecars.is_symlink():
                raise ValueError("collection contains unknown internal metadata")
            verified: list[Path] = []
            for item in sidecars.iterdir():
                try:
                    document_id = str(uuid.UUID(item.stem))
                except (ValueError, AttributeError):
                    raise ValueError("collection contains retained portable metadata") from None
                metadata = self._read_json(item, {}) if item.is_file() and not item.is_symlink() else {}
                if metadata.get("document_id") != document_id:
                    raise ValueError("collection contains unknown internal metadata")
                verified.append(item)
            for item in verified:
                item.unlink()
            sidecars.rmdir()
        (collection / POLICY_FILE).unlink(missing_ok=True)
        collection.rmdir()
        details = {"path": str(relative), "actor": actor, "at": utc_now()}
        self._event("webdav_collection_deleted", details)
        self._record_revision("webdav_collection_deleted", actor, "collections", hashlib.sha256(str(relative).encode()).hexdigest(), details)

    def _safe_managed_relative_path(self, value: str, *, require_name: bool = False) -> Path:
        requested = Path(value)
        if (require_name and value in {"", "."}) or requested.is_absolute() or ".." in requested.parts:
            raise ValueError("path must remain inside the document store")
        if any(part in {"", CONTROL_DIR, HISTORY_DIR, PREVIEW_CACHE_DIR, POLICY_FILE} or "\x00" in part for part in requested.parts):
            raise ValueError("path contains a reserved segment")
        candidate = self.root / requested
        if candidate.is_symlink():
            raise ValueError("symbolic links are not available over WebDAV")
        resolved_parent = candidate.parent.resolve()
        try:
            resolved_parent.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("path must remain inside the document store") from exc
        current = self.root
        for part in requested.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise ValueError("symbolic links are not available over WebDAV")
        return requested

    def versions(self, reference: str | Path) -> list[dict[str, Any]]:
        """Return an indexed version series without loading every sidecar."""
        document = self.get_document(reference)
        series_id = document.get("version_series_id", document["document_id"])
        self._refresh_listing_index(document)
        with self._db() as db:
            rows = db.execute(
                """SELECT document_id FROM document_listing
                    WHERE version_series_id = ?
                    ORDER BY version_number, last_seen_at, document_id""",
                (series_id,),
            ).fetchall()
        versions = []
        for (document_id,) in rows:
            metadata = document if document_id == document["document_id"] else self._read_json(self.documents / f"{document_id}.json", {})
            if metadata.get("document_id"):
                versions.append(metadata)
        return versions or [document]

    def relationship_targets(self, document: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Load only sidecars explicitly referenced by one document."""
        targets: dict[str, dict[str, Any]] = {}
        for relationship in document.get("relationships", []):
            document_id = str(relationship.get("target_document_id", ""))
            if not document_id or document_id in targets:
                continue
            metadata = self._read_json(self.documents / f"{document_id}.json", {})
            if metadata.get("document_id"):
                targets[document_id] = metadata
        return targets

    def offload_old_versions(self, reference: str | Path, archive_root: str | Path, actor: str) -> dict[str, Any]:
        """Move every non-current version to an external archive after hash verification."""
        self._require_actor(actor)
        versions = self.versions(reference)
        if len(versions) < 2:
            raise ValueError("the document has no older versions to offload")
        target_root = Path(archive_root).expanduser().resolve()
        if not target_root.is_dir() or target_root == self.root or self.root in target_root.parents:
            raise ValueError("choose a mounted archive directory outside the document store")
        archive = self.register_external_archive(target_root, target_root.name, ["version-archive"], actor)
        current = max(versions, key=lambda item: int(item.get("version_number", 1)))
        moved: list[str] = []
        for version in versions:
            if version["document_id"] == current["document_id"] or version.get("storage_state") == "external_archive":
                continue
            source = self.root / version.get("last_path", "")
            if not source.is_file() or source.is_symlink():
                continue
            directory = target_root / ".simpleoffice-documents" / version["version_series_id"] / f"v{version.get('version_number', 1)}"
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / source.name
            if destination.exists():
                destination = directory / f"{destination.stem}-{version['document_id'][:8]}{destination.suffix}"
            temporary = destination.with_suffix(destination.suffix + ".part")
            shutil.copy2(source, temporary)
            if sha256_file(temporary) != version.get("sha256"):
                temporary.unlink(missing_ok=True)
                raise RuntimeError(f"hash verification failed for {source.name}; local file was retained")
            temporary.replace(destination)
            version.setdefault("archive_locations", []).append({
                "archive_id": archive["archive_id"], "path": str(destination.relative_to(target_root)), "moved_at": utc_now(), "moved_by": actor,
            })
            version["storage_state"] = "external_archive"
            version["local_deleted_at"] = utc_now()
            self._save_document(version)
            self._refresh_search_index(version)
            with self._db() as db:
                db.execute("DELETE FROM scan_file WHERE relative_path = ?", (version.get("last_path", ""),))
            source.unlink()
            self._event("document_version_offloaded", {"document_id": version["document_id"], "archive_id": archive["archive_id"], "actor": actor})
            self._record_revision("document_version_offloaded", actor, "documents", version["document_id"], version)
            moved.append(version["document_id"])
        return {"archive": archive, "current_document_id": current["document_id"], "moved_document_ids": moved}

    def note_wiki(self) -> list[dict[str, Any]]:
        """Return all document notes as a single, newest-first wiki feed."""
        entries: list[dict[str, Any]] = []
        for document in self._all_documents():
            for note in document.get("notes", []):
                entries.append({**note, "document_id": document["document_id"], "path": document.get("last_path", ""), "version_number": document.get("version_number", 1)})
        return sorted(entries, key=lambda item: item.get("created_at", ""), reverse=True)

    def logbook(self, reference: str | Path | None = None) -> list[dict[str, Any]]:
        """Read the append-only activity trail, optionally for one document."""
        document_id = self.get_document(reference)["document_id"] if reference is not None else None
        entries: list[dict[str, Any]] = []
        events_dir = self.history.root / "events"
        if events_dir.exists():
            for path in events_dir.glob("*.json"):
                event = self._read_json(path, {})
                if event and (document_id is None or event.get("key") == document_id):
                    entries.append({**event, "source": "revision"})
        if self.events.exists():
            try:
                for line in self.events.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    if not isinstance(event, dict): continue
                    related = event.get("document_id") or event.get("source_document_id")
                    if document_id is None or related == document_id:
                        entries.append({**event, "source": "scanner"})
            except (OSError, json.JSONDecodeError):
                pass
        return sorted(entries, key=lambda item: item.get("at", ""), reverse=True)

    def logbook_page(self, *, page: int = 1, page_size: int = 50, query: str = "", actor: str = "", action: str = "", from_at: str = "", to_at: str = "") -> dict[str, Any]:
        """Return a bounded, filtered audit-log page instead of loading all events."""
        page = max(1, int(page)); page_size = min(100, max(10, int(page_size))); needed = page * page_size + 1
        query, actor, action = query.casefold().strip(), actor.casefold().strip(), action.casefold().strip()

        def matches(event: dict[str, Any]) -> bool:
            timestamp = str(event.get("at", ""))
            if from_at and timestamp < from_at: return False
            if to_at and timestamp > f"{to_at}T23:59:59.999999+00:00": return False
            if actor and actor not in str(event.get("actor", "")).casefold(): return False
            if action and action not in str(event.get("action", event.get("type", ""))).casefold(): return False
            return not query or query in json.dumps(event, ensure_ascii=False).casefold()

        def take(items):
            found = []
            for item in items:
                if matches(item):
                    found.append(item)
                    if len(found) >= needed: break
            return found

        history_dir = self.history.root / "events"
        history_events = take({**self._read_json(path, {}), "source": "revision"} for path in sorted(history_dir.glob("*.json"), reverse=True)) if history_dir.exists() else []
        if self.events.exists() and not any((query, actor, action, from_at, to_at)):
            with self.events.open(encoding="utf-8") as source:
                scanner_lines = list(deque(source, maxlen=needed))
        elif self.events.exists():
            with self.events.open(encoding="utf-8") as source:
                scanner_lines = list(source)
        else:
            scanner_lines = []
        scanner_events = take({**event, "source": "scanner"} for line in reversed(scanner_lines) for event in [json.loads(line)] if isinstance(event, dict))
        merged = sorted([*history_events, *scanner_events], key=lambda item: item.get("at", ""), reverse=True)
        start = (page - 1) * page_size
        return {"events": merged[start:start + page_size], "page": page, "has_next": len(merged) > start + page_size}

    def scan(self, progress: Callable[[ScanReport], None] | None = None, file_progress: Callable[[Path], None] | None = None, verify_hashes: bool = False, post_file: Callable[[Path], None] | None = None) -> ScanReport:
        self.initialize()
        files = new_files = updated_files = duplicates = symlinks = skipped_boundaries = errors = 0
        pending = [self.root]; visited_directories: set[tuple[int, int]] = set()
        while pending:
            current_path = pending.pop()
            try:
                current_stat = current_path.stat(); key = (current_stat.st_dev, current_stat.st_ino)
                if key in visited_directories:
                    self._event("directory_cycle_skipped", {"path": self.relative(current_path)}); continue
                visited_directories.add(key); options = self._scan_options(current_path); entries = sorted(current_path.iterdir(), key=lambda entry: entry.name.lower())
            except (OSError, ValueError) as exc:
                errors += 1; self._event("folder_policy_invalid", {"path": self.relative(current_path), "error": str(exc)}); continue
            for path in entries:
                if path.name in (POLICY_FILE, CONTROL_DIR, HISTORY_DIR, PREVIEW_CACHE_DIR): continue
                try:
                    if path.is_symlink():
                        symlinks += 1; target = path.resolve(strict=True); self._event("symlink_seen", {"path": self.relative(path), "target": str(target)})
                        if not options["follow_symlinks"]: continue
                        if target.is_dir(): pending.append(target)
                        elif target.is_file():
                            if file_progress: file_progress(target)
                            created, updated, duplicate = self._scan_file(target, force_hash=verify_hashes)
                            if post_file: post_file(target)
                            files += 1; new_files += int(created); updated_files += int(updated); duplicates += int(duplicate)
                        continue
                    entry_stat = path.stat(follow_symlinks=False)
                    if path.is_dir():
                        if entry_stat.st_dev != current_stat.st_dev and not options["allow_other_filesystems"]:
                            skipped_boundaries += 1; self._event("filesystem_boundary_skipped", {"path": self.relative(path), "device": entry_stat.st_dev}); continue
                        pending.append(path); continue
                    if not path.is_file(): continue
                    if file_progress: file_progress(path)
                    created, updated, duplicate = self._scan_file(path, force_hash=verify_hashes)
                    if post_file: post_file(path)
                    files += 1; new_files += int(created); updated_files += int(updated); duplicates += int(duplicate)
                except (OSError, ValueError) as exc:
                    errors += 1; self._event("scan_error", {"path": self.relative(path), "error": str(exc)})
                if progress: progress(ScanReport(files, new_files, updated_files, duplicates, symlinks, skipped_boundaries, errors))
        with self._db() as db:
            missing = [self.root / row[0] for row in db.execute("SELECT relative_path FROM scan_file").fetchall() if not (self.root / row[0]).exists()]
        if missing:
            removed = self.scan_changed_paths(missing); errors += removed.errors
        report = ScanReport(files, new_files, updated_files, duplicates, symlinks, skipped_boundaries, errors)
        if progress: progress(report)
        return report

    def scan_status(self) -> dict[str, Any]:
        self.initialize(); return self._read_json(self.scan_status_path, {"state": "idle", "updated_at": None})

    def scan_changed_paths(self, paths: Iterable[str | Path], post_file: Callable[[Path], None] | None = None) -> ScanReport:
        """Incrementally reconcile paths reported by a filesystem watcher."""
        self.initialize(); files = new_files = updated_files = duplicates = errors = 0
        for value in {Path(item) for item in paths}:
            try:
                path = value.resolve(strict=False)
                try: relative = str(path.relative_to(self.root))
                except ValueError: continue
                if not relative or relative.split(os.sep, 1)[0] in {CONTROL_DIR, HISTORY_DIR, PREVIEW_CACHE_DIR}: continue
                if path.is_file() and not path.is_symlink():
                    created, updated, duplicate = self._scan_file(path)
                    if post_file: post_file(path)
                    files += 1; new_files += int(created); updated_files += int(updated); duplicates += int(duplicate)
                elif not path.exists():
                    with self._db() as db:
                        row = db.execute("SELECT document_id,sha256 FROM scan_file WHERE relative_path=?", (relative,)).fetchone()
                        if not row: continue
                        document_id, digest = row[0], row[1]
                        db.execute("DELETE FROM scan_file WHERE relative_path=?", (relative,))
                        remaining = db.execute("SELECT 1 FROM scan_file WHERE document_id=? LIMIT 1", (document_id,)).fetchone()
                        if not remaining:
                            db.execute("DELETE FROM document_listing WHERE document_id=?", (document_id,)); db.execute("DELETE FROM document_search WHERE document_id=?", (document_id,)); db.execute("DELETE FROM document_relationship WHERE source_id=?", (document_id,))
                        fingerprint_path = self.fingerprints / f"{digest}.json"; fingerprint = self._read_json(fingerprint_path, {})
                        if fingerprint:
                            fingerprint["paths"] = [item for item in fingerprint.get("paths", []) if item != relative]; fingerprint["last_seen_at"] = utc_now(); atomic_json_write(fingerprint_path, fingerprint)
                        self._event("file_missing", {"path": relative, "document_id": document_id})
            except (OSError, ValueError) as exc:
                errors += 1; self._event("scan_error", {"path": str(value), "error": str(exc)})
        return ScanReport(files, new_files, updated_files, duplicates, errors=errors)

    def set_scan_status(self, status: dict[str, Any]) -> None:
        self.initialize(); atomic_json_write(self.scan_status_path, {**status, "updated_at": utc_now()})

    def set_preview_metadata(self, reference: str, preview: dict[str, Any]) -> None:
        metadata = self.get_document(reference); metadata["preview"] = preview; self._save_document(metadata)

    def relative(self, path: Path) -> str:
        resolved = path.resolve()
        if resolved == self.root: return "."
        try: return str(resolved.relative_to(self.root))
        except ValueError: return f"[external] {resolved}"

    def _scan_options(self, folder: Path) -> dict[str, bool]:
        try:
            self.ensure_folder_policy(folder); policy = self._read_json(folder / POLICY_FILE, {})
        except ValueError:
            policy = {}
        configured = policy.get("scan", {}) if isinstance(policy.get("scan", {}), dict) else {}
        return {"follow_symlinks": configured.get("follow_symlinks") is True, "allow_other_filesystems": configured.get("allow_other_filesystems") is True}

    def _scan_file(self, path: Path, force_hash: bool = False) -> tuple[bool, bool, bool]:
        stat = path.stat(); relative_path = self.relative(path); now = utc_now(); cached: tuple[str, str] | None = None; previous_same_path = None; previous_path = ""
        if not force_hash:
            with self._db() as db:
                previous_same_path = db.execute("SELECT document_id,sha256 FROM scan_file WHERE relative_path=?", (relative_path,)).fetchone()
                row = db.execute("SELECT document_id, sha256 FROM scan_file WHERE relative_path = ? AND size = ? AND modified_ns = ?", (relative_path, stat.st_size, stat.st_mtime_ns)).fetchone()
                if row:
                    metadata = self._read_json(self.documents / f"{row[0]}.json", {})
                    db.execute("UPDATE scan_file SET last_seen_at = ?, device = ?, inode = ? WHERE relative_path = ?", (now, stat.st_dev, stat.st_ino, relative_path))
                    if metadata.get("document_id"):
                        self._refresh_listing_index(metadata, db); return False, False, metadata.get("system_state") == "duplicate"
                    cached = (row[0], row[1])
                else:
                    moved = db.execute("SELECT relative_path, document_id, sha256 FROM scan_file WHERE device = ? AND inode = ? AND size = ? AND modified_ns = ? ORDER BY last_seen_at DESC LIMIT 1", (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)).fetchone()
                    if moved:
                        previous_path, document_id, digest = moved; cached = (document_id, digest)

        xattrs = self._read_xattrs(path); known_identity = cached is not None or bool(xattrs.get("document_id"))
        if cached: document_id, digest = cached
        else: digest = sha256_file(path); document_id = xattrs.get("document_id") or str(uuid.uuid4())
        metadata_path = self.documents / f"{document_id}.json"; metadata_exists = metadata_path.exists()
        if previous_same_path and previous_same_path[1] == digest and metadata_exists:
            metadata = self._read_json(metadata_path, {})
            with self._db() as db:
                db.execute("UPDATE scan_file SET size=?,modified_ns=?,device=?,inode=?,last_seen_at=? WHERE relative_path=?", (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino, now, relative_path))
                if metadata.get("document_id"): self._refresh_listing_index(metadata, db)
            return False, False, metadata.get("system_state") == "duplicate"
        created = not metadata_exists and not known_identity; updated = not created; metadata = self._read_json(metadata_path, {})
        original_sha256 = metadata.get("original_sha256", digest); integrity_changed = original_sha256 != digest; existing_tags = metadata.get("tags", xattrs.get("tags", [])); detected_tags = self._filename_tags(path.name); all_tags = sorted({*existing_tags, *detected_tags}, key=str.casefold)
        tagged_at = metadata.get("tagged_at", {})
        if not isinstance(tagged_at, dict): tagged_at = {}
        for tag in all_tags: tagged_at.setdefault(tag, metadata.get("first_seen_at", now))
        if previous_path and previous_path != relative_path:
            metadata["location_history"] = [*metadata.get("location_history", []), {"from": previous_path, "to": relative_path, "at": now, "reason": "filesystem_scan"}]
            with self._db() as db: db.execute("DELETE FROM scan_file WHERE relative_path = ?", (previous_path,))
            self._event("file_move_detected", {"document_id": document_id, "from": previous_path, "to": relative_path})
        metadata.update({"version": 1, "document_id": document_id, "sha256": digest, "first_seen_at": metadata.get("first_seen_at", now), "last_seen_at": now, "last_path": relative_path, "tags": all_tags, "tagged_at": tagged_at, "original_sha256": original_sha256, "content_sha256": digest, "notes": metadata.get("notes", []), "relationships": metadata.get("relationships", []), "state": metadata.get("state", "new"), "state_history": metadata.get("state_history", []), "version_series_id": metadata.get("version_series_id", document_id), "version_number": metadata.get("version_number", 1), "attributes": metadata.get("attributes", {}), "deadlines": metadata.get("deadlines", [])})
        atomic_json_write(metadata_path, metadata); self._write_xattrs(path, document_id, digest, metadata["tags"])
        fingerprint_path = self.fingerprints / f"{digest}.json"; fingerprint = self._read_json(fingerprint_path, {}); known_paths = set(fingerprint.get("paths", []))
        if previous_path: known_paths.discard(previous_path)
        duplicate = bool(known_paths and relative_path not in known_paths); known_paths.add(relative_path)
        fingerprint.update({"sha256": digest, "first_seen_at": fingerprint.get("first_seen_at", now), "last_seen_at": now, "paths": sorted(known_paths), "seen_count": int(fingerprint.get("seen_count", 0)) + 1})
        atomic_json_write(fingerprint_path, fingerprint)
        metadata["system_state"] = "integrity_changed" if integrity_changed else ("duplicate" if duplicate else "indexed")
        if self._is_image(path): self._apply_image_analysis(path, metadata)
        self._apply_document_text_extraction(path, metadata); self._save_document(metadata)
        if integrity_changed: self._event("integrity_changed", {"document_id": document_id, "expected_sha256": original_sha256, "observed_sha256": digest})
        self._refresh_search_index(metadata)
        with self._db() as db:
            db.execute("INSERT INTO scan_file(relative_path, document_id, sha256, size, modified_ns, device, inode, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(relative_path) DO UPDATE SET document_id=excluded.document_id, sha256=excluded.sha256, size=excluded.size, modified_ns=excluded.modified_ns, device=excluded.device, inode=excluded.inode, last_seen_at=excluded.last_seen_at", (relative_path, document_id, digest, stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino, now))
        self._event("file_seen", {"path": relative_path, "document_id": document_id, "sha256": digest, "first_seen": created, "duplicate": duplicate})
        return created, updated, duplicate

    @staticmethod
    def _filename_tags(filename: str) -> list[str]:
        stem = Path(filename).stem.strip()
        if not stem: return []
        words = [part.strip() for part in re.split(r"[\s_.-]+", stem) if len(part.strip()) > 1]
        return [stem, *words]

    @staticmethod
    def _is_image(path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}

    def _apply_document_text_extraction(self, path: Path, metadata: dict[str, Any], force: bool = False) -> bool:
        current = metadata.get("text_extraction", {})
        if not force and current.get("source_sha256") == metadata.get("sha256") and "extracted_text" in metadata: return False
        analysis: dict[str, Any] = {"source_sha256": metadata.get("sha256", ""), "extracted_at": utc_now(), "status": "completed"}; native_text = ""; image_text = ""
        try:
            if self._is_image(path): native_text = metadata.get("ocr_text", ""); analysis["kind"] = "image"
            elif path.suffix.lower() == ".pdf": native_text = self._pdf_text(path); image_text = self._pdf_image_ocr(path); analysis["kind"] = "pdf"
            else: native_text, kind = self._file_text(path); analysis["kind"] = kind
        except RuntimeError as exc:
            analysis["status"] = "partial"; analysis["error"] = str(exc)
        combined = "\n".join(part for part in (native_text, image_text) if part).strip(); metadata["extracted_text"] = combined; metadata["text_extraction"] = {**analysis, "native_characters": len(native_text), "image_ocr_characters": len(image_text), "characters": len(combined)}
        return True
