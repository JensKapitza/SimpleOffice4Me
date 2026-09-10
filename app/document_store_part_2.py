"""DocumentStore implementation part 2 of 5."""
from __future__ import annotations

from .document_store_core import *  # noqa: F401,F403


class _DocumentStorePart2:
    def folder_retention_rules(self) -> list[dict[str, Any]]:
        """Return configured folder rules without following links or leaving the archive."""
        configured: list[dict[str, Any]] = []
        root_device = self.root.stat().st_dev
        policy_paths: list[Path] = []
        for current, directories, files in os.walk(self.root, followlinks=False):
            folder = Path(current)
            retained_directories: list[str] = []
            for name in directories:
                candidate = folder / name
                try:
                    if (
                        name not in {CONTROL_DIR, HISTORY_DIR, PREVIEW_CACHE_DIR}
                        and not candidate.is_symlink()
                        and candidate.stat().st_dev == root_device
                    ):
                        retained_directories.append(name)
                except OSError:
                    continue
            directories[:] = retained_directories
            if POLICY_FILE in files:
                policy_paths.append(folder / POLICY_FILE)
        for policy_path in sorted(policy_paths, key=lambda item: str(item).casefold()):
            folder = policy_path.parent
            policy = self._read_json(policy_path, {})
            retention = policy.get("retention", {})
            rules = retention.get("rules", []) if isinstance(retention, dict) else []
            for rule in rules:
                if isinstance(rule, dict):
                    configured.append({**rule, "folder": self.relative(folder)})
        return configured

    def add_folder_retention_rule(
        self,
        folder: str,
        kind: str,
        label: str,
        actor: str,
        *,
        tag: str = "",
        expires_at: str = "",
        years: int | str | None = None,
    ) -> dict[str, Any]:
        """Append one validated, audited rule to an existing archive folder."""
        self._require_actor(actor)
        target = (self.root / folder.strip()).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("folder is outside the document root") from exc
        if not target.is_dir() or target.is_symlink():
            raise ValueError("folder must be an existing regular directory")
        normalized_kind = kind.strip().casefold()
        if normalized_kind not in {"retention", "work"}:
            raise ValueError("deadline kind must be retention or work")
        normalized_label = label.strip()
        if not normalized_label:
            raise ValueError("rule label is required")
        rule: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "kind": normalized_kind,
            "label": normalized_label,
        }
        normalized_tag = tag.strip()
        if normalized_tag:
            rule["tag"] = normalized_tag
        has_date = bool(expires_at.strip())
        has_years = bool(str(years or "").strip())
        if has_date and has_years:
            raise ValueError("provide either a fixed date or years, not both")
        if has_date:
            rule["expires_at"] = parse_deadline(expires_at).isoformat()
        else:
            try:
                normalized_years = int(years or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError("years must be a whole number") from exc
            if not 1 <= normalized_years <= 100:
                raise ValueError("years must be between 1 and 100")
            rule["years"] = normalized_years

        policy_path = self.ensure_folder_policy(target)
        policy = self._read_json(policy_path, {})
        retention = policy.setdefault("retention", {})
        if not isinstance(retention, dict):
            raise ValueError("folder retention configuration is invalid")
        rules = retention.setdefault("rules", [])
        if not isinstance(rules, list):
            raise ValueError("folder retention rules are invalid")
        rules.append(rule)
        atomic_json_write(policy_path, policy)
        event = {"folder": self.relative(target), "actor": actor, "rule": rule}
        self._event("folder_retention_rule_added", event)
        self._record_revision(
            "folder_retention_rule_added", actor, "policies", policy["folder_id"], policy
        )
        return {**rule, "folder": self.relative(target)}

    def remove_folder_retention_rule(self, folder: str, rule_id: str, actor: str) -> None:
        """Remove exactly one selected rule; never alter document deadlines."""
        self._require_actor(actor)
        target = (self.root / folder.strip()).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("folder is outside the document root") from exc
        policy_path = target / POLICY_FILE
        if not target.is_dir() or target.is_symlink() or not policy_path.is_file():
            raise ValueError("folder policy does not exist")
        policy = self._read_json(policy_path, {})
        retention = policy.get("retention", {})
        rules = retention.get("rules", []) if isinstance(retention, dict) else []
        remaining = [rule for rule in rules if not isinstance(rule, dict) or rule.get("id") != rule_id]
        if len(remaining) == len(rules):
            raise ValueError("retention rule does not exist")
        retention["rules"] = remaining
        atomic_json_write(policy_path, policy)
        self._event(
            "folder_retention_rule_removed",
            {"folder": self.relative(target), "actor": actor, "rule_id": rule_id},
        )
        self._record_revision(
            "folder_retention_rule_removed", actor, "policies", policy["folder_id"], policy
        )

    def retention_status(
        self,
        reference: str | Path,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Evaluate only the indexed retention component of one document."""
        focus = self.get_document(reference)
        # Keep the requested document current even while a background index is
        # still filling the disposable projection.
        self._refresh_listing_index(focus)
        with self._db() as db:
            rows = db.execute(
                """WITH RECURSIVE connected(document_id) AS (
                       VALUES (?)
                       UNION
                       SELECT relation.target_id
                         FROM document_relationship AS relation
                         JOIN connected ON relation.source_id = connected.document_id
                        WHERE relation.propagates_retention = 1
                       UNION
                       SELECT relation.source_id
                         FROM document_relationship AS relation
                         JOIN connected ON relation.target_id = connected.document_id
                        WHERE relation.propagates_retention = 1
                   )
                   SELECT document_id FROM connected""",
                (focus["document_id"],),
            ).fetchall()
        documents: dict[str, dict[str, Any]] = {focus["document_id"]: focus}
        for (document_id,) in rows:
            if document_id in documents:
                continue
            metadata = self._read_json(self.documents / f"{document_id}.json", {})
            if metadata.get("document_id"):
                documents[document_id] = metadata
        return self._retention_statuses(documents, now=now)[focus["document_id"]]

    def retention_statuses(
        self, *, now: datetime | None = None
    ) -> dict[str, dict[str, Any]]:
        """Evaluate the complete archive in one graph pass for large stores."""
        documents = {item["document_id"]: item for item in self._all_documents()}
        return self._retention_statuses(documents, now=now)

    def _retention_statuses(
        self,
        documents: dict[str, dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Evaluate supplied documents without discovering any other sidecars."""
        evaluated = {
            document_id: evaluate_deadlines(document, self._deadline_rules(document), now=now)
            for document_id, document in documents.items()
        }
        adjacency: dict[str, set[str]] = {document_id: set() for document_id in documents}
        for document in documents.values():
            source_id = document["document_id"]
            for link in document.get("relationships", []):
                target_id = str(link.get("target_document_id", ""))
                if link.get("propagates_retention") is True and target_id in documents:
                    adjacency[source_id].add(target_id)
                    adjacency[target_id].add(source_id)

        statuses: dict[str, dict[str, Any]] = {}
        unseen = set(documents)
        while unseen:
            first = next(iter(unseen))
            component: set[str] = set()
            pending = [first]
            while pending:
                document_id = pending.pop()
                if document_id in component:
                    continue
                component.add(document_id)
                pending.extend(adjacency.get(document_id, ()))
            unseen.difference_update(component)

            retention_findings: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            missing_deadlines: list[str] = []
            for document_id in sorted(component):
                document = documents[document_id]
                result = evaluated[document_id]
                document_retention = [
                    {
                        **finding,
                        "document_id": document_id,
                        "document_path": document.get("last_path", ""),
                    }
                    for finding in result["findings"]
                    if finding["kind"] == "retention"
                ]
                if not document_retention:
                    missing_deadlines.append(document_id)
                retention_findings.extend(document_retention)
                errors.extend({**error, "document_id": document_id} for error in result["errors"])

            retention_until = max(
                (parse_deadline(item["expires_at"]) for item in retention_findings),
                default=None,
            )
            all_expired = bool(retention_findings) and all(
                item["expired"] for item in retention_findings
            )
            cleanup_eligible = all_expired and not errors and not missing_deadlines
            for document_id in component:
                own = evaluated[document_id]
                if own["work_locked"]:
                    state = "locked"
                elif cleanup_eligible:
                    state = "cleanup_ready"
                elif missing_deadlines or errors:
                    state = "deadline_missing"
                else:
                    state = "active"
                statuses[document_id] = {
                    **own,
                    "status": state,
                    "component_document_ids": sorted(component),
                    "missing_retention_document_ids": missing_deadlines,
                    "retention_findings": sorted(
                        retention_findings,
                        key=lambda item: (item["expires_at"], item["document_id"]),
                    ),
                    "retention_until": retention_until.isoformat() if retention_until else None,
                    "all_retention_expired": all_expired,
                    "cleanup_eligible": cleanup_eligible,
                    "errors": errors,
                }
        return statuses

    def cleanup_candidates(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """List files eligible for a manually started cleanup; never move them."""
        candidates: list[dict[str, Any]] = []
        documents = {item["document_id"]: item for item in self._all_documents()}
        for document_id, status in self.retention_statuses(now=now).items():
            document = documents[document_id]
            if status["cleanup_eligible"] and document.get("cleanup_state") != "staged":
                candidates.append(
                    {
                        "document_id": document_id,
                        "path": document.get("last_path", ""),
                        "retention_until": status["retention_until"],
                    }
                )
        return sorted(candidates, key=lambda item: (item["retention_until"], item["path"]))

    def cleanup_expired(
        self,
        destination_folder: str,
        actor: str,
        *,
        apply: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Preview or manually move eligible files; physical deletion is never performed."""
        self._require_actor(actor)
        candidates = self.cleanup_candidates(now=now)
        if not apply:
            return {"applied": False, "candidates": candidates, "moved": []}
        moved: list[dict[str, Any]] = []
        for candidate in candidates:
            relative_parent = Path(candidate["path"]).parent
            target_folder = Path(destination_folder) / (
                relative_parent if str(relative_parent) != "." else Path()
            )
            document = self.move_document(
                candidate["document_id"], str(target_folder), actor, allow_locked=True
            )
            document["cleanup_state"] = "staged"
            document["cleanup_staged_at"] = utc_now()
            document["cleanup_staged_by"] = actor
            document["cleanup_original_path"] = candidate["path"]
            self._save_document(document)
            moved.append(
                {
                    "document_id": document["document_id"],
                    "from": candidate["path"],
                    "to": document["last_path"],
                    "sha256": document.get("sha256", ""),
                    "retention_until": candidate["retention_until"],
                }
            )
        self._event(
            "retention_cleanup_completed",
            {"actor": actor, "destination": destination_folder, "moved": moved},
        )
        self._record_revision(
            "retention_cleanup_completed",
            actor,
            "retention",
            "cleanup",
            {"at": utc_now(), "destination": destination_folder, "moved": moved},
        )
        return {"applied": True, "candidates": candidates, "moved": moved}

    def analyze_image(self, reference: str | Path, author: str) -> dict[str, Any]:
        """Extract EXIF and OCR text from one managed image and add safe tags."""
        self._require_actor(author)
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        path = self.root / metadata.get("last_path", "")
        if not self._is_image(path):
            raise ValueError("document is not a supported image")
        self._apply_image_analysis(path, metadata, force=True)
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event("image_analyzed", {"document_id": metadata["document_id"], "actor": author})
        self._record_revision("image_analyzed", author, "documents", metadata["document_id"], metadata)
        return metadata

    def refresh_missing_text(self, actor: str, force: bool = False) -> int:
        """Backfill searchable text for existing files without reprocessing it later."""
        self._require_actor(actor)
        pending = {
            item["document_id"] for item in self._all_documents()
            if force or item.get("text_extraction", {}).get("source_sha256") != item.get("sha256") or "extracted_text" not in item
        }
        self.scan()
        updated = 0
        for metadata in self._all_documents():
            path = self.root / metadata.get("last_path", "")
            if not path.is_file() or path.is_symlink():
                continue
            if self._is_image(path):
                self._apply_image_analysis(path, metadata, force=force)
            changed = self._apply_document_text_extraction(path, metadata, force=force)
            if changed or metadata["document_id"] in pending:
                metadata.setdefault("text_extraction", {})["updated_by"] = actor
                self._save_document(metadata)
                self._refresh_search_index(metadata)
                updated += 1
        self._event("document_text_backfill", {"actor": actor, "updated": updated, "force": force})
        self._record_revision("document_text_backfill", actor, "search", "text-backfill", {"updated": updated, "force": force, "at": utc_now()})
        return updated

    def search_page(self, query: str, page: int = 1, page_size: int = 25) -> dict[str, Any]:
        """Return one fast, indexed result page without touching document files."""
        query = query.strip()
        if not query:
            return {"results": [], "page": 1, "page_size": page_size, "has_next": False}
        compiled = compile_query(query)
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        limit = page_size + 1
        offset = (page - 1) * page_size
        self.initialize()
        with self._db() as db:
            try:
                if compiled.requires_sql:
                    raise sqlite3.OperationalError("advanced boolean query requires SQL evaluation")
                rows = db.execute(
                    "SELECT document_id, path, state FROM document_search WHERE document_search MATCH ? LIMIT ? OFFSET ?",
                    (compiled.fts, limit, offset),
                ).fetchall()
            except sqlite3.OperationalError:
                where_fragment = _validated_search_where(compiled.where)
                statement = "".join((
                    "SELECT document_id, path, state FROM document_search WHERE ",
                    where_fragment,
                    " LIMIT ? OFFSET ?",
                ))
                rows = db.execute(
                    statement,
                    (*compiled.parameters, limit, offset),
                ).fetchall()
        results = [{"document_id": row[0], "path": row[1], "state": row[2]} for row in rows]
        return {
            "results": results[:page_size],
            "page": page,
            "page_size": page_size,
            "has_next": len(results) > page_size,
        }

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Compatibility helper returning the first indexed search page."""
        return self.search_page(query, page_size=limit)["results"]

    def add_link(
        self,
        source: str | Path,
        target: str | Path,
        relation_type: str = "related",
        label: str = "",
        author: str = "",
        propagates_retention: bool = False,
    ) -> dict[str, Any]:
        """Create a directed, labelled document relationship for graph views."""
        self._require_actor(author)
        source_metadata = self.get_document(source)
        self._require_document_editable(source_metadata)
        target_metadata = self.get_document(target)
        if source_metadata["document_id"] == target_metadata["document_id"]:
            raise ValueError("a document cannot be linked to itself")
        relation_type = relation_type.strip() or "related"
        for link in source_metadata.setdefault("relationships", []):
            if link.get("target_document_id") == target_metadata["document_id"] and link.get("type") == relation_type:
                return link
        link = {
            "id": str(uuid.uuid4()),
            "target_document_id": target_metadata["document_id"],
            "type": relation_type,
            "label": label.strip(),
            "propagates_retention": propagates_retention is True,
            "author": author,
            "created_at": utc_now(),
        }
        source_metadata["relationships"].append(link)
        self._save_document(source_metadata)
        self._refresh_search_index(source_metadata)
        self._event(
            "document_link_added",
            {"source_document_id": source_metadata["document_id"], "target_document_id": target_metadata["document_id"], "type": relation_type},
        )
        self._record_revision("document_link_added", author, "documents", source_metadata["document_id"], source_metadata)
        return link

    def add_text_link(self, source: str | Path, target_text: str, relation_type: str = "related", label: str = "", author: str = "") -> dict[str, Any]:
        """Link a document to a durable free-text reference such as an URL or case number."""
        self._require_actor(author)
        target_text = target_text.strip()
        if not target_text:
            raise ValueError("free-text reference must not be empty")
        source_metadata = self.get_document(source)
        self._require_document_editable(source_metadata)
        relation_type = relation_type.strip() or "related"
        link = {"id": str(uuid.uuid4()), "target_text": target_text, "type": relation_type, "label": label.strip(), "author": author, "created_at": utc_now()}
        source_metadata.setdefault("relationships", []).append(link)
        self._save_document(source_metadata)
        self._refresh_search_index(source_metadata)
        self._event("document_text_link_added", {"source_document_id": source_metadata["document_id"], "target_text": target_text, "type": relation_type})
        self._record_revision("document_text_link_added", author, "documents", source_metadata["document_id"], source_metadata)
        return link

    def import_version(self, source: str | Path, version_of: str | Path, author: str = "") -> dict[str, Any]:
        """Import SOURCE as the next version of an existing document."""
        self._require_actor(author)
        parent = self.get_document(version_of)
        target = self.import_file(source, author)
        self.scan()
        version = self.get_document(target)
        series_id = parent.get("version_series_id", parent["document_id"])
        version_numbers = [
            int(item.get("version_number", 1))
            for item in self._all_documents()
            if item.get("version_series_id", item.get("document_id")) == series_id
        ]
        version.update(
            {
                "version_series_id": series_id,
                "version_number": max(version_numbers, default=1) + 1,
                "version_of": parent["document_id"],
                "state": "new_version",
            }
        )
        version.setdefault("state_history", []).append(
            {"from": "new", "to": "new_version", "author": author, "changed_at": utc_now()}
        )
        self._save_document(version)
        self._refresh_search_index(version)
        self.add_link(version["document_id"], parent["document_id"], "version_of", "Vorgängerversion", author)
        self._event(
            "document_version_imported",
            {"document_id": version["document_id"], "version_of": parent["document_id"], "version_number": version["version_number"]},
        )
        self._record_revision("document_version_imported", author, "documents", version["document_id"], version)
        return version

    def replace_content(
        self,
        reference: str | Path,
        content: bytes,
        author: str,
        *,
        expected_sha256: str = "",
        source: str = "webdav",
        max_bytes: int = 512 * 1024 * 1024,
        restored_from_sha256: str = "",
    ) -> dict[str, Any]:
        """Atomically replace a managed file and retain the previous payload.

        The precondition is checked while holding the same filesystem lock as
        the write. This makes an HTTP ETag useful even when two WebDAV workers
        receive concurrent saves.
        """
        self._require_actor(author)
        if len(content) > max_bytes:
            raise ValueError("document exceeds the configured upload size limit")
        self.initialize()
        from .file_lock import exclusive_file_lock

        with exclusive_file_lock(self.control / ".document-content.lock"):
            metadata = self.get_document(reference)
            self._require_document_editable(metadata)
            path = self.root / str(metadata.get("last_path", ""))
            if not path.is_file() or path.is_symlink():
                raise ValueError("document file is unavailable")
            current_sha256 = sha256_file(path)
            if expected_sha256 and not hmac.compare_digest(expected_sha256, current_sha256):
                raise ValueError("document content changed since it was opened")
            new_sha256 = hashlib.sha256(content).hexdigest()
            if hmac.compare_digest(current_sha256, new_sha256):
                return metadata

            now = utc_now()
            archive = self.control / "content-versions" / metadata["document_id"] / current_sha256
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not archive.exists():
                temporary_archive = archive.with_suffix(".partial")
                shutil.copy2(path, temporary_archive)
                if sha256_file(temporary_archive) != current_sha256:
                    temporary_archive.unlink(missing_ok=True)
                    raise RuntimeError("previous document version could not be verified")
                temporary_archive.replace(archive)

            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
            try:
                with temporary.open("xb") as destination:
                    destination.write(content)
                    destination.flush()
                    os.fsync(destination.fileno())
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)

            revision = {
                "number": int(metadata.get("content_revision", 0)) + 1,
                "at": now,
                "actor": author,
                "source": source,
                "previous_sha256": current_sha256,
                "sha256": new_sha256,
                "previous_size": archive.stat().st_size,
                "size": len(content),
                "archive": str(archive.relative_to(self.control)),
            }
            metadata["sha256"] = new_sha256
            metadata["content_sha256"] = new_sha256
            metadata["original_sha256"] = new_sha256
            metadata["content_revision"] = revision["number"]
            metadata["last_seen_at"] = now
            metadata["system_state"] = "indexed"
            metadata.setdefault("content_history", []).append(revision)
            metadata["content_history"] = metadata["content_history"][-200:]
            restoration = None
            if restored_from_sha256:
                restoration = {
                    "at": now,
                    "actor": author,
                    "from_sha256": current_sha256,
                    "restored_sha256": restored_from_sha256,
                    "content_revision": revision["number"],
                }
                metadata.setdefault("content_recovery_history", []).append(restoration)
                metadata["content_recovery_history"] = metadata["content_recovery_history"][-200:]
            self._write_xattrs(path, metadata["document_id"], new_sha256, metadata.get("tags", []))
            self._apply_document_text_extraction(path, metadata, force=True)
            self._save_document(metadata)
            self._refresh_search_index(metadata)
            stat = path.stat()
            with self._db() as db:
                db.execute(
                    """UPDATE scan_file SET sha256 = ?, size = ?, modified_ns = ?,
                       device = ?, inode = ?, last_seen_at = ? WHERE relative_path = ?""",
                    (new_sha256, stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino, now, self.relative(path)),
                )
            relative_path = self.relative(path)
            old_fingerprint_path = self.fingerprints / f"{current_sha256}.json"
            old_fingerprint = self._read_json(old_fingerprint_path, {})
            if old_fingerprint:
                old_fingerprint["paths"] = sorted(set(old_fingerprint.get("paths", [])) - {relative_path})
                old_fingerprint["last_seen_at"] = now
                atomic_json_write(old_fingerprint_path, old_fingerprint)
            new_fingerprint_path = self.fingerprints / f"{new_sha256}.json"
            new_fingerprint = self._read_json(new_fingerprint_path, {})
            new_fingerprint.update({
                "sha256": new_sha256,
                "first_seen_at": new_fingerprint.get("first_seen_at", now),
                "last_seen_at": now,
                "paths": sorted({*new_fingerprint.get("paths", []), relative_path}),
                "seen_count": int(new_fingerprint.get("seen_count", 0)) + 1,
            })
            atomic_json_write(new_fingerprint_path, new_fingerprint)
            self._event("document_content_replaced", {"document_id": metadata["document_id"], **revision})
            self._record_revision("document_content_replaced", author, "documents", metadata["document_id"], metadata)
            if restoration:
                self._event("document_content_restored", {"document_id": metadata["document_id"], **restoration})
                self._record_revision("document_content_restored", author, "documents", metadata["document_id"], metadata)
            return metadata

    def content_recovery_versions(self, reference: str | Path) -> list[dict[str, Any]]:
        """List immutable archived payloads that can replace the current content."""
        metadata = self.get_document(reference)
        versions: dict[str, dict[str, Any]] = {}
        for change in metadata.get("content_history", []):
            digest = str(change.get("previous_sha256", ""))
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            archive = self.control / "content-versions" / metadata["document_id"] / digest
            versions[digest] = {
                "sha256": digest,
                "created_at": str(change.get("at", "")),
                "replaced_by": str(change.get("actor", "")),
                "size": int(change.get("previous_size", 0)),
                "available": archive.is_file() and not archive.is_symlink(),
            }
        return sorted(versions.values(), key=lambda item: item["created_at"], reverse=True)

    def restore_content_version(
        self,
        reference: str | Path,
        archived_sha256: str,
        expected_current_sha256: str,
        actor: str,
        *,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Restore one verified archived payload with a current-version precondition."""
        self._require_actor(actor)
        if not re.fullmatch(r"[0-9a-f]{64}", archived_sha256):
            raise ValueError("unknown archived content version")
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        path = self.root / str(metadata.get("last_path", ""))
        if not path.is_file() or path.is_symlink():
            raise ValueError("document file is unavailable")
        current_sha256 = sha256_file(path)
        if not expected_current_sha256 or not hmac.compare_digest(expected_current_sha256, current_sha256):
            raise ValueError("document content changed since the recovery page was opened")
        if hmac.compare_digest(archived_sha256, current_sha256):
            raise ValueError("the selected version is already current")
        known = {item["sha256"] for item in self.content_recovery_versions(reference) if item["available"]}
        if archived_sha256 not in known:
            raise ValueError("archived content version is unavailable")
        archive = self.control / "content-versions" / metadata["document_id"] / archived_sha256
        if archive.is_symlink() or not archive.is_file() or sha256_file(archive) != archived_sha256:
            raise ValueError("archived content version failed integrity verification")
        if archive.stat().st_size > max_bytes:
            raise ValueError("archived content version exceeds the configured upload size limit")
        return self.replace_content(
            reference,
            archive.read_bytes(),
            actor,
            expected_sha256=current_sha256,
            source="recovery",
            max_bytes=max_bytes,
            restored_from_sha256=archived_sha256,
        )

    def find_matches(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Find possible version parents by ID, path, name and all human metadata."""
        needle = query.strip().casefold()
        if not needle:
            return []
        matches: list[dict[str, Any]] = []
        for metadata in self._all_documents():
            path = metadata.get("last_path", "")
            haystack = " ".join(
                [metadata.get("document_id", ""), path, metadata.get("state", ""), " ".join(metadata.get("tags", [])),
                 " ".join(note.get("text", "") for note in metadata.get("notes", [])), json.dumps(metadata.get("attributes", {}), ensure_ascii=False)]
            ).casefold()
            tag_hit = any(self.tag_matches(needle, tag) for tag in metadata.get("tags", []))
            if needle not in haystack and not tag_hit:
                continue
            score = 100 if needle in (metadata.get("document_id", "").casefold(), path.casefold()) else 10
            if Path(path).name.casefold() == needle:
                score = 90
            elif needle in Path(path).name.casefold():
                score = 60
            matches.append({"document_id": metadata["document_id"], "path": path, "state": metadata.get("state"), "version": metadata.get("version_number", 1), "score": score})
        return sorted(matches, key=lambda item: (-item["score"], item["path"]))[:limit]

    @staticmethod
    def tag_matches(pattern: str, tag: str) -> bool:
        """Case-insensitive tag matching with optional ``*`` wildcard support."""
        pattern = pattern.strip().casefold()
        tag = tag.strip().casefold()
        if not pattern or not tag:
            return False
        return fnmatch.fnmatchcase(tag, pattern) if "*" in pattern else tag.startswith(pattern)

    def graph(self, reference: str | Path) -> dict[str, Any]:
        """Return one document, its versions and all inbound/outbound graph edges."""
        document = self.get_document(reference)
        document_id = document["document_id"]
        documents = self._all_documents()
        visible_ids = {document_id}
        edges: list[dict[str, Any]] = []
        for item in documents:
            for link in item.get("relationships", []):
                target_id = link.get("target_document_id")
                if item.get("document_id") == document_id or target_id == document_id:
                    visible_ids.add(item["document_id"])
                    if target_id:
                        visible_ids.add(target_id)
                    edges.append({"source": item["document_id"], "target": target_id or f"text:{link['id']}", **link})
        series_id = document.get("version_series_id", document_id)
        for item in documents:
            if item.get("version_series_id", item.get("document_id")) == series_id:
                visible_ids.add(item["document_id"])
        nodes = [
            {
                "id": item["document_id"],
                "path": item.get("last_path"),
                "state": item.get("state"),
                "version_number": item.get("version_number", 1),
                "notes": len(item.get("notes", [])),
            }
            for item in documents
            if item.get("document_id") in visible_ids
        ]
        nodes.extend({"id": edge["target"], "path": edge.get("target_text", "Freitext"), "state": "reference", "version_number": 0, "notes": 0} for edge in edges if edge["target"].startswith("text:"))
        return {"focus_document_id": document_id, "nodes": nodes, "edges": edges}

    def list_documents(self) -> list[dict[str, Any]]:
        """List the known documents without treating the SQLite cache as truth."""
        return sorted(
            self._all_documents(),
            key=lambda item: (item.get("last_seen_at", ""), item.get("last_path", "")),
            reverse=True,
        )

    def document_page(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        """Load only one document page from the scan index for large archives."""
        self.initialize()
        page = max(1, page); page_size = max(1, min(500, page_size))
        with self._db() as db:
            total = int(db.execute("SELECT COUNT(DISTINCT document_id) FROM scan_file").fetchone()[0])
            rows = db.execute("SELECT document_id FROM scan_file GROUP BY document_id ORDER BY MAX(last_seen_at) DESC, MAX(relative_path) LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size)).fetchall()
        documents = []
        for (document_id,) in rows:
            metadata = self._read_json(self.documents / f"{document_id}.json", {})
            if metadata.get("document_id"):
                documents.append(metadata)
        return {"documents": documents, "page": page, "page_size": page_size, "total": total, "has_next": page * page_size < total}

