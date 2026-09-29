"""DocumentStore implementation part 1 of 5."""
from __future__ import annotations

from .document_store_core import *  # noqa: F401,F403
from .safe_paths import resolve_file_under


class _DocumentStorePart1:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.documents = self.control / "documents"
        self.fingerprints = self.control / "fingerprints"
        self.events = self.control / EVENT_FILE
        self.index_path = self.control / "index.sqlite3"
        self.archives_path = self.control / ARCHIVES_FILE
        self.shares_path = self.control / SHARES_FILE
        self.ssh_sources_path = self.control / SSH_SOURCES_FILE
        self.scan_status_path = self.control / "scan-status.json"
        self.note_snapshots = self.control / "note-snapshots"
        self.document_access = self.control / "document-access"
        self.history = RevisionHistory(self.root)

    def initialize(self) -> None:
        if self.index_path not in _INITIALIZED_INDEXES or not self.index_path.is_file():
            with _STORE_INITIALIZATION_LOCK:
                if self.index_path not in _INITIALIZED_INDEXES or not self.index_path.is_file():
                    self._initialize_once()
                    _INITIALIZED_INDEXES.add(self.index_path)
        self._recover_interrupted_collection_deletions()

    def _initialize_once(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.documents.mkdir(parents=True, exist_ok=True)
        self.fingerprints.mkdir(parents=True, exist_ok=True)
        self.document_access.mkdir(parents=True, exist_ok=True)
        self.ensure_folder_policy(self.root)
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS scan_file (
                    relative_path TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    device INTEGER,
                    inode INTEGER,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scan_file_sha256 ON scan_file(sha256);
                CREATE TABLE IF NOT EXISTS document_listing (
                    document_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    has_notes INTEGER NOT NULL,
                    has_relationships INTEGER NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    version_series_id TEXT NOT NULL DEFAULT '',
                    version_number INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS document_listing_inbox
                    ON document_listing(state, has_notes, has_relationships, last_seen_at DESC);
                CREATE TABLE IF NOT EXISTS document_relationship (
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    propagates_retention INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(source_id, target_id)
                );
                CREATE INDEX IF NOT EXISTS document_relationship_target
                    ON document_relationship(target_id, propagates_retention);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(scan_file)")}
            if "device" not in columns:
                db.execute("ALTER TABLE scan_file ADD COLUMN device INTEGER")
            if "inode" not in columns:
                db.execute("ALTER TABLE scan_file ADD COLUMN inode INTEGER")
            db.execute("CREATE INDEX IF NOT EXISTS scan_file_inode ON scan_file(device, inode)")
            listing_columns = {row[1] for row in db.execute("PRAGMA table_info(document_listing)")}
            if "version_series_id" not in listing_columns:
                db.execute("ALTER TABLE document_listing ADD COLUMN version_series_id TEXT NOT NULL DEFAULT ''")
            if "version_number" not in listing_columns:
                db.execute("ALTER TABLE document_listing ADD COLUMN version_number INTEGER NOT NULL DEFAULT 1")
            db.execute("CREATE INDEX IF NOT EXISTS document_listing_versions ON document_listing(version_series_id, version_number)")
            try:
                db.execute(
                    """CREATE VIRTUAL TABLE IF NOT EXISTS document_search
                    USING fts5(document_id UNINDEXED, path, state, tags, notes, attributes, content)"""
                )
            except sqlite3.OperationalError:
                db.execute(
                    """CREATE TABLE IF NOT EXISTS document_search (
                    document_id TEXT PRIMARY KEY, path TEXT, state TEXT, tags TEXT,
                    notes TEXT, attributes TEXT, content TEXT)"""
                )

    def _recover_interrupted_collection_deletions(self) -> None:
        recovery_root = self.control / COLLECTION_TRASH_DIR
        if not recovery_root.is_dir() or recovery_root.is_symlink():
            return
        for operation in sorted(recovery_root.iterdir()):
            manifest_path = operation / "manifest.json"
            manifest = self._read_json(manifest_path, {})
            if (
                not operation.is_dir() or operation.is_symlink()
                or manifest.get("state") in {"committed", "restored"}
            ):
                continue
            try:
                deletion_id = str(uuid.UUID(operation.name))
                if manifest.get("deletion_id") != deletion_id:
                    raise ValueError("collection recovery manifest has an invalid identity")
                relative = self._safe_managed_relative_path(
                    str(manifest.get("source", "")), require_name=True,
                )
                source = self.root / relative
                staged = operation / "tree"
                if staged.exists():
                    if staged.is_symlink() or not staged.is_dir() or source.exists():
                        raise ValueError("collection recovery cannot safely restore its namespace")
                    if not source.parent.is_dir() or source.parent.is_symlink():
                        raise ValueError("collection recovery parent is unavailable")
                    staged.replace(source)
                elif not source.is_dir() or source.is_symlink():
                    raise ValueError("collection recovery payload is unavailable")
                snapshots = manifest.get("document_snapshots", {})
                if not isinstance(snapshots, dict):
                    raise ValueError("collection recovery snapshots are invalid")
                for document_id, snapshot in snapshots.items():
                    if not isinstance(snapshot, dict) or snapshot.get("document_id") != document_id:
                        raise ValueError("collection recovery document identity is invalid")
                    self._save_document(snapshot)
                    self._refresh_search_index(snapshot)
                    try:
                        restored_file = resolve_file_under(self.root, str(snapshot.get("last_path", "")))
                    except (OSError, ValueError):
                        continue
                    self._scan_file(restored_file, force_hash=True)
                manifest_path.unlink(missing_ok=True)
                operation.rmdir()
                details = {
                    "deletion_id": deletion_id, "path": str(relative),
                    "documents": len(snapshots), "at": utc_now(), "actor": "system",
                }
                self._event("webdav_collection_delete_recovered", details)
                self._record_revision(
                    "webdav_collection_delete_recovered", "system", "collections",
                    hashlib.sha256(str(relative).encode()).hexdigest(), details,
                )
            except (OSError, ValueError) as exc:
                if isinstance(manifest, dict):
                    manifest["state"] = "recovery_blocked"
                    manifest["recovery_error"] = str(exc)
                    manifest["recovery_checked_at"] = utc_now()
                    atomic_json_write(manifest_path, manifest)

    def ensure_folder_policy(self, folder: str | Path, actor: str = "system") -> Path:
        folder_path = Path(folder).resolve()
        if self.root not in (folder_path, *folder_path.parents):
            raise ValueError("folder is outside the document root")
        policy = folder_path / POLICY_FILE
        if policy.exists():
            try:
                loaded = json.loads(policy.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and loaded.get("folder_id"):
                    return policy
            except (OSError, json.JSONDecodeError):
                raise ValueError(f"invalid folder policy: {policy}")
        created_at = utc_now()
        atomic_json_write(
            policy,
            {
                "version": 2,
                "folder_id": str(uuid.uuid4()),
                "created_at": created_at,
                "created_by": actor,
                "inherit": True,
                "grants": [],
                "scan": {
                    "follow_symlinks": False,
                    "allow_other_filesystems": False,
                },
                "retention": {"rules": []},
            },
        )
        self._event(
            "folder_policy_created",
            {"path": self.relative(folder_path), "actor": actor, "created_at": created_at},
        )
        self._record_revision(
            "folder_policy_created",
            actor,
            "policies",
            hashlib.sha256(self.relative(folder_path).encode("utf-8")).hexdigest(),
            self._read_json(policy, {}),
        )
        return policy

    def import_file(self, source: str | Path, actor: str = "system") -> Path:
        self.initialize()
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file() or source_path.is_symlink():
            raise ValueError("source must be a regular file")
        inbox = self.root / "inbox"
        self.ensure_folder_policy(inbox)
        safe_name = source_path.name.replace("/", "_").replace("\\", "_")
        target = inbox / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}-{safe_name}"
        shutil.copy2(source_path, target)
        self._event("file_imported", {"source": str(source_path), "path": self.relative(target), "actor": actor})
        self.scan()
        metadata = self.get_document(target)
        self._record_revision("document_imported", actor, "documents", metadata["document_id"], metadata)
        return target

    def import_directory(self, source: str | Path, label: str, actor: str = "system") -> dict[str, int | str]:
        self._require_actor(actor)
        self.initialize()
        source_root = Path(source).expanduser().resolve()
        if not source_root.is_dir() or source_root.is_symlink():
            raise ValueError("source directory must be a regular directory")
        source_in_staging = self.control == source_root or self.control in source_root.parents
        if source_root == self.root or (self.root in source_root.parents and not source_in_staging) or source_root in self.root.parents:
            raise ValueError("source directory must be outside the main archive")
        safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label.strip()).strip(".-") or "storage"
        destination_root = self.root / "imports" / safe_label
        copied = unchanged = skipped = errors = 0
        for source_path in sorted(source_root.rglob("*"), key=lambda item: str(item).casefold()):
            try:
                if not source_path.is_file() or source_path.is_symlink():
                    skipped += 1
                    continue
                relative = source_path.relative_to(source_root)
                destination = destination_root / relative
                source_hash = sha256_file(source_path)
                if destination.is_file() and sha256_file(destination) == source_hash:
                    unchanged += 1
                    continue
                if destination.exists():
                    destination = destination.with_name(f"{destination.stem}-{source_hash[:12]}{destination.suffix}")
                    if destination.is_file() and sha256_file(destination) == source_hash:
                        unchanged += 1
                        continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                self.ensure_folder_policy(destination.parent)
                shutil.copy2(source_path, destination)
                copied += 1
            except OSError as exc:
                errors += 1
                self._event("directory_import_file_skipped", {"source": str(source_path), "error": str(exc), "actor": actor})
        self.scan()
        result: dict[str, int | str] = {"source": str(source_root), "destination": self.relative(destination_root), "copied": copied, "unchanged": unchanged, "skipped": skipped, "errors": errors}
        self._event("directory_imported", {**result, "actor": actor})
        self._record_revision("directory_imported", actor, "documents", safe_label, result)
        return result

    def import_upload(
        self,
        upload: Any,
        filename: str,
        actor: str,
        archive: bool = False,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> dict[str, Any]:
        self._require_actor(actor)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("upload size limit must be a positive number of bytes")
        self.initialize()
        safe_name = Path(filename or "upload").name.replace("/", "_").replace("\\", "_")
        if safe_name in ("", "."):
            safe_name = "upload"
        staging = self.control / "staging" / f"{uuid.uuid4().hex}-{safe_name}"
        staging.parent.mkdir(parents=True, exist_ok=True)
        source = getattr(upload, "stream", upload)
        try:
            written = 0
            with staging.open("wb") as destination:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    written += len(chunk)
                    if written > max_bytes:
                        limit_mib = max_bytes / (1024 * 1024)
                        raise ValueError(f"upload exceeds the {limit_mib:g} MiB size limit")
                    destination.write(chunk)
            digest = sha256_file(staging)
            if archive:
                destination_dir = self.root / "archive" / digest[:2] / digest
            else:
                destination_dir = self.root / "inbox"
            self.ensure_folder_policy(destination_dir)
            target = destination_dir / safe_name
            if target.exists():
                target = destination_dir / f"{target.stem}-{uuid.uuid4().hex[:8]}{target.suffix}"
            staging.replace(target)
        except Exception:
            staging.unlink(missing_ok=True)
            raise
        self.scan()
        metadata = self.get_document(target)
        self._event("file_uploaded", {"document_id": metadata["document_id"], "path": self.relative(target), "actor": actor, "sha256": digest, "archive": archive})
        self._record_revision("document_uploaded", actor, "documents", metadata["document_id"], metadata)
        return metadata

    def archives(self) -> list[dict[str, Any]]:
        self.initialize()
        return sorted(self._read_json(self.archives_path, {"archives": []}).get("archives", []), key=lambda item: item.get("label", ""))

    def main_archive(self) -> dict[str, Any]:
        self.initialize()
        policy = self._read_json(self.root / POLICY_FILE, {})
        return {"label": "Hauptarchiv (lokal)", "archive_id": policy.get("folder_id", "local"), "path": str(self.root), "available": True}

    def create_share(self, reference: str | Path, password: str, expires_days: int, actor: str, note_id: str = "") -> dict[str, Any]:
        self._require_actor(actor)
        password = password.strip()
        if len(password) < 12:
            raise ValueError("share password must contain at least 12 characters")
        if not 1 <= expires_days <= 365:
            raise ValueError("share expiry must be between 1 and 365 days")
        document = self.get_document(reference)
        self._require_document_editable(document)
        note = next((item for item in document.get("notes", []) if item.get("id") == note_id), None) if note_id else None
        if note_id and note is None:
            raise ValueError("note does not exist on this document")
        share_id = uuid.uuid4().hex
        salt = os.urandom(16)
        expires_at = datetime.now(timezone.utc).replace(microsecond=0).timestamp() + expires_days * 86400
        share = {
            "share_id": share_id,
            "document_id": document["document_id"],
            "resource_type": "note" if note else "file",
            "note_id": note_id or None,
            "created_by": actor,
            "created_at": utc_now(),
            "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
            "password_salt": salt.hex(),
            "password_hash": hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex(),
            "access_log": [],
        }
        payload = self._read_json(self.shares_path, {"shares": []})
        payload["shares"] = [*payload.get("shares", []), share]
        atomic_json_write(self.shares_path, payload)
        self._event("share_created", {"share_id": share_id, "document_id": document["document_id"], "actor": actor})
        self._record_revision("share_created", actor, "shares", share_id, {key: value for key, value in share.items() if not key.startswith("password_")})
        return {key: value for key, value in share.items() if not key.startswith("password_")}

    def document_shares(self, reference: str | Path) -> list[dict[str, Any]]:
        document_id = self.get_document(reference)["document_id"]
        now = datetime.now(timezone.utc)
        shares = [item for item in self._read_json(self.shares_path, {"shares": []}).get("shares", []) if item.get("document_id") == document_id]
        return [self._public_share(item, now) for item in sorted(shares, key=lambda item: item.get("created_at", ""), reverse=True)]

    def share_status(self, share_id: str) -> dict[str, Any] | None:
        share = next((item for item in self._read_json(self.shares_path, {"shares": []}).get("shares", []) if item.get("share_id") == share_id), None)
        return self._public_share(share, datetime.now(timezone.utc)) if share else None

    def renew_share(self, reference: str | Path, share_id: str, password: str, expires_days: int, actor: str) -> dict[str, Any]:
        self._require_actor(actor)
        if len(password.strip()) < 12: raise ValueError("share password must contain at least 12 characters")
        if not 1 <= expires_days <= 365: raise ValueError("share expiry must be between 1 and 365 days")
        document_id = self.get_document(reference)["document_id"]
        payload = self._read_json(self.shares_path, {"shares": []})
        share = next((item for item in payload["shares"] if item.get("share_id") == share_id and item.get("document_id") == document_id), None)
        if share is None: raise ValueError("share does not belong to this document")
        salt = os.urandom(16)
        share.update({"password_salt": salt.hex(), "password_hash": hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex(), "expires_at": datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + expires_days * 86400, timezone.utc).isoformat(), "reactivated_at": utc_now(), "reactivated_by": actor})
        atomic_json_write(self.shares_path, payload)
        self._event("share_reactivated", {"share_id": share_id, "document_id": document_id, "actor": actor})
        return self._public_share(share, datetime.now(timezone.utc))

    def open_share(self, share_id: str, password: str, remote_addr: str = "") -> dict[str, Any]:
        payload = self._read_json(self.shares_path, {"shares": []})
        share = next((item for item in payload["shares"] if item.get("share_id") == share_id), None)
        if not share: raise ValueError("Freigabelink ist nicht verfügbar")
        if self._share_expired(share):
            self._record_share_access(payload, share, "expired_access", remote_addr)
            raise ValueError("Freigabelink ist abgelaufen")
        expected = bytes.fromhex(share["password_hash"])
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(share["password_salt"]), n=2**14, r=8, p=1)
        if not hmac.compare_digest(expected, actual):
            self._record_share_access(payload, share, "password_rejected", remote_addr)
            raise ValueError("Passwort ist nicht korrekt")
        document = self.get_document(share["document_id"])
        self._record_share_access(payload, share, "opened", remote_addr)
        result = {"document": document, "share": {key: value for key, value in share.items() if not key.startswith("password_")}}
        if share.get("resource_type") == "note":
            note = next((item for item in document.get("notes", []) if item.get("id") == share.get("note_id")), None)
            if note is None:
                raise ValueError("the shared note is no longer available")
            return {**result, "note": note}
        try:
            path = resolve_file_under(self.root, str(document.get("last_path", "")))
        except (OSError, ValueError) as exc:
            raise ValueError("the shared original is currently unavailable") from exc
        return {**result, "path": path}

    def record_share_view(self, share_id: str, remote_addr: str = "") -> None:
        payload = self._read_json(self.shares_path, {"shares": []})
        share = next((item for item in payload["shares"] if item.get("share_id") == share_id), None)
        if share: self._record_share_access(payload, share, "link_viewed", remote_addr)

    def record_access(self, reference: str | Path, actor: str, access_type: str) -> dict[str, Any]:
        self._require_actor(actor)
        if access_type not in {"seen", "found"}: raise ValueError("unsupported document access type")
        document = self.get_document(reference)
        access = {"type": access_type, "actor": actor, "at": utc_now()}
        access_path = self.document_access / f"{document['document_id']}.json"
        from .file_lock import exclusive_file_lock
        with exclusive_file_lock(access_path.with_suffix(".lock")):
            access_metadata = self._read_json(access_path, {})
            access_metadata.setdefault("access_log", list(document.get("access_log", [])))
            access_metadata.setdefault("seen_by", dict(document.get("seen_by", {})))
            access_metadata.setdefault("found_by", dict(document.get("found_by", {})))
            access_metadata["access_log"].append(access)
            access_metadata["access_log"] = access_metadata["access_log"][-200:]
            access_metadata[f"{access_type}_by"][actor] = access["at"]
            atomic_json_write(access_path, access_metadata)
        document.update(access_metadata)
        self._event(f"document_{access_type}", {"document_id": document["document_id"], **access})
        return document

    @staticmethod
    def _share_expired(share: dict[str, Any]) -> bool:
        return datetime.fromisoformat(share["expires_at"]).astimezone(timezone.utc) < datetime.now(timezone.utc)

    def _public_share(self, share: dict[str, Any], now: datetime) -> dict[str, Any]:
        result = {key: value for key, value in share.items() if not key.startswith("password_")}
        result["status"] = "abgelaufen" if self._share_expired(share) else "aktiv"
        return result

    def _record_share_access(self, payload: dict[str, Any], share: dict[str, Any], action: str, remote_addr: str) -> None:
        entry = {"action": action, "at": utc_now(), "ip": remote_addr or "unbekannt", "share_id": share["share_id"]}
        share.setdefault("access_log", []).append(entry)
        atomic_json_write(self.shares_path, payload)
        self._event("share_access", {"share_id": share["share_id"], "document_id": share["document_id"], **entry})

    def ssh_sources(self) -> list[dict[str, Any]]:
        self.initialize()
        return self._read_json(self.ssh_sources_path, {"sources": []}).get("sources", [])

    def register_ssh_source(self, name: str, host: str, username: str, remote_path: str, key_path: str, actor: str) -> dict[str, Any]:
        self._require_actor(actor)
        values = {"name": name.strip(), "host": host.strip(), "username": username.strip(), "remote_path": remote_path.strip(), "key_path": key_path.strip()}
        if not all(values[key] for key in ("name", "host", "username", "remote_path")):
            raise ValueError("name, host, SSH user and remote path are required")
        if any("\n" in value or "\x00" in value for value in values.values()):
            raise ValueError("SSH source values must not contain control characters")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", values["host"]) or not re.fullmatch(r"[A-Za-z0-9_.-]+", values["username"]):
            raise ValueError("SSH host or user contains unsupported characters")
        if not values["remote_path"].startswith("/"):
            raise ValueError("remote SSH path must be absolute")
        record = {"source_id": str(uuid.uuid4()), **values, "created_by": actor, "created_at": utc_now(), "last_sync_at": None}
        payload = self._read_json(self.ssh_sources_path, {"sources": []})
        payload["sources"] = [*payload.get("sources", []), record]
        atomic_json_write(self.ssh_sources_path, payload)
        self._event("ssh_source_registered", {"source_id": record["source_id"], "actor": actor, "host": values["host"]})
        self._record_revision("ssh_source_registered", actor, "ssh-sources", record["source_id"], record)
        return record

    def sync_ssh_source(self, source_id: str, actor: str) -> int:
        self._require_actor(actor)
        source = next((item for item in self.ssh_sources() if item.get("source_id") == source_id), None)
        if source is None:
            raise ValueError("unknown SSH source")
        if shutil.which("rsync") is None:
            raise RuntimeError("rsync is required for SSH import; on Windows use WSL or install rsync")
        staging = self.control / "ssh-staging" / source_id
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        remote = f"{source['username']}@{source['host']}:{source['remote_path'].rstrip('/')}/"
        command = ["rsync", "-a", "--no-links", "--safe-links"]
        if source.get("key_path"):
            key_path = Path(source["key_path"]).expanduser()
            if not key_path.is_file():
                raise ValueError("configured SSH key file is unavailable")
            command.extend(["-e", shlex.join(["ssh", "-i", str(key_path)])])
        command.extend(["--", remote, f"{staging}/"])
        result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "SSH import failed")
        imported = int(self.import_directory(staging, f"SSH-{source['name']}", actor)["copied"])
        payload = self._read_json(self.ssh_sources_path, {"sources": []})
        for item in payload.get("sources", []):
            if item.get("source_id") == source_id:
                item["last_sync_at"] = utc_now()
                item["last_sync_by"] = actor
        atomic_json_write(self.ssh_sources_path, payload)
        self._event("ssh_source_synced", {"source_id": source_id, "actor": actor, "imported": imported})
        self._record_revision("ssh_source_synced", actor, "ssh-sources", source_id, next(item for item in payload["sources"] if item.get("source_id") == source_id))
        return imported

    def remove_ssh_source(self, source_id: str, actor: str) -> None:
        self._require_actor(actor)
        payload = self._read_json(self.ssh_sources_path, {"sources": []})
        source = next((item for item in payload.get("sources", []) if item.get("source_id") == source_id), None)
        if source is None:
            raise ValueError("unknown SSH source")
        payload["sources"] = [item for item in payload["sources"] if item.get("source_id") != source_id]
        atomic_json_write(self.ssh_sources_path, payload)
        self._event("ssh_source_removed", {"source_id": source_id, "actor": actor, "host": source.get("host", "")})
        self._record_revision("ssh_source_removed", actor, "ssh-sources", source_id, source)

    def register_external_archive(self, root: str | Path, label: str, tags: list[str], actor: str) -> dict[str, Any]:
        self._require_actor(actor)
        path = Path(root).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("archive root must be an existing mounted directory")
        label = label.strip() or path.name
        marker_path = path / ARCHIVE_MARKER
        marker = self._read_json(marker_path, {})
        archive_id = marker.get("archive_id", str(uuid.uuid4()))
        marker = {"version": 1, "archive_id": archive_id, "label": label, "tags": sorted(set(tags)), "registered_at": marker.get("registered_at", utc_now())}
        atomic_json_write(marker_path, marker)
        all_archives = self.archives()
        record = {**marker, "last_known_path": str(path), "available": True, "last_seen_at": utc_now()}
        all_archives = [item for item in all_archives if item.get("archive_id") != archive_id] + [record]
        atomic_json_write(self.archives_path, {"archives": all_archives})
        self._event("external_archive_registered", {"archive_id": archive_id, "actor": actor, "path": str(path)})
        self._record_revision("external_archive_registered", actor, "archives", archive_id, record)
        return record

    def discover_archives(self, actor: str) -> list[dict[str, Any]]:
        self._require_actor(actor)
        found: dict[str, Path] = {}
        for mount in self._mounted_roots():
            marker = self._read_json(mount / ARCHIVE_MARKER, {})
            if marker.get("archive_id"):
                found[marker["archive_id"]] = mount
        archives = self.archives()
        updated: list[dict[str, Any]] = []
        for archive in archives:
            mounted = found.get(archive.get("archive_id"))
            updated.append({**archive, "available": mounted is not None, **({"last_known_path": str(mounted), "last_seen_at": utc_now()} if mounted else {})})
        atomic_json_write(self.archives_path, {"archives": updated})
        self._event("external_archives_discovered", {"actor": actor, "found": len(found)})
        self._record_revision("external_archives_discovered", actor, "archives", "registry", {"archives": updated})
        return updated

    def get_document(self, reference: str | Path) -> dict[str, Any]:
        self.initialize()
        reference_text = str(reference)
        candidate = Path(reference).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        if candidate.exists() and candidate.is_file() and not candidate.is_symlink():
            self._scan_file(candidate.resolve())
            with self._db() as db:
                row = db.execute(
                    "SELECT document_id FROM scan_file WHERE relative_path = ?",
                    (self.relative(candidate),),
                ).fetchone()
            if row:
                reference_text = str(row[0])
        metadata = self._read_json(self.documents / f"{reference_text}.json", {})
        if not metadata.get("document_id"):
            raise ValueError(f"unknown document: {reference}")
        access_metadata = self._read_json(self.document_access / f"{metadata['document_id']}.json", {})
        for key in ("access_log", "seen_by", "found_by"):
            if key in access_metadata:
                metadata[key] = access_metadata[key]
        return metadata

    def add_note(self, reference: str | Path, text: str, author: str = "") -> dict[str, Any]:
        self._require_actor(author)
        text = text.strip()
        if not text:
            raise ValueError("note must not be empty")
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        note = {"id": str(uuid.uuid4()), "text": text, "author": author, "created_at": utc_now()}
        metadata.setdefault("notes", []).append(note)
        self._save_document(metadata)
        self._write_note_snapshot(metadata, note)
        self._refresh_search_index(metadata)
        self._event("document_note_added", {"document_id": metadata["document_id"], "note_id": note["id"]})
        self._record_revision("document_note_added", author, "documents", metadata["document_id"], metadata)
        return note

    def note_snapshot(self, reference: str | Path, note_id: str) -> Path:
        document = self.get_document(reference)
        note = next((item for item in document.get("notes", []) if item.get("id") == note_id), None)
        if note is None:
            raise ValueError("note does not exist on this document")
        path = self.note_snapshots / f"{note_id}.pdf"
        if not path.exists():
            self._write_note_snapshot(document, note)
        return path

    def set_state(self, reference: str | Path, state: str, author: str = "") -> dict[str, Any]:
        self._require_actor(author)
        state = state.strip()
        if not state:
            raise ValueError("state must not be empty")
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        previous = metadata.get("state", "new")
        event = {"from": previous, "to": state, "author": author, "changed_at": utc_now()}
        metadata["state"] = state
        metadata.setdefault("state_history", []).append(event)
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event("document_state_changed", {"document_id": metadata["document_id"], **event})
        self._record_revision("document_state_changed", author, "documents", metadata["document_id"], metadata)
        return event

    def set_attribute(self, reference: str | Path, key: str, value: str, author: str = "") -> None:
        self._require_actor(author)
        key = key.strip()
        if not key:
            raise ValueError("attribute key must not be empty")
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        metadata.setdefault("attributes", {})[key] = value
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event(
            "document_attribute_set",
            {"document_id": metadata["document_id"], "key": key, "author": author},
        )
        self._record_revision("document_attribute_set", author, "documents", metadata["document_id"], metadata)

    def update_metadata(
        self,
        reference: str | Path,
        *,
        attributes: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        author: str = "",
    ) -> dict[str, Any]:
        self._require_actor(author)
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        changed_attributes = {
            str(key).strip(): value for key, value in (attributes or {}).items()
            if str(key).strip()
        }
        metadata.setdefault("attributes", {}).update(changed_attributes)
        if tags is not None:
            previous = set(metadata.get("tags", []))
            updated = sorted({tag.strip() for tag in tags if tag.strip()}, key=str.casefold)
            tagged_at = metadata.setdefault("tagged_at", {})
            now = utc_now()
            for tag in updated:
                if tag not in previous:
                    tagged_at[tag] = now
            metadata["tags"] = updated
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        details = {
            "document_id": metadata["document_id"], "author": author,
            "attribute_keys": sorted(changed_attributes),
            "tags_changed": tags is not None,
        }
        self._event("document_metadata_updated", details)
        self._record_revision("document_metadata_updated", author, "documents", metadata["document_id"], details)
        return metadata

    def set_malware_scan(self, reference: str | Path, value: dict[str, Any], author: str) -> None:
        self._require_actor(author)
        metadata = self.get_document(reference)
        metadata.setdefault("attributes", {})["malware_scan"] = dict(value)
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event("document_malware_scan_set", {"document_id": metadata["document_id"], "author": author, "verdict": value.get("verdict", "")})
        self._record_revision("document_malware_scan_set", author, "documents", metadata["document_id"], {"scan_id": value.get("scan_id", ""), "verdict": value.get("verdict", ""), "scanned_at": value.get("scanned_at", "")})

    def set_tags(self, reference: str | Path, tags: list[str], author: str = "") -> dict[str, Any]:
        self._require_actor(author)
        metadata = self.get_document(reference)
        self._require_document_editable(metadata)
        previous = set(metadata.get("tags", []))
        updated = sorted({tag.strip() for tag in tags if tag.strip()}, key=str.casefold)
        tagged_at = metadata.setdefault("tagged_at", {})
        now = utc_now()
        for tag in updated:
            if tag not in previous:
                tagged_at[tag] = now
        metadata["tags"] = updated
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event("document_tags_set", {"document_id": metadata["document_id"], "actor": author, "tags": metadata["tags"]})
        self._record_revision("document_tags_set", author, "documents", metadata["document_id"], metadata)
        return metadata

    def export_portable_metadata(self, reference: str | Path, actor: str) -> Path:
        self._require_actor(actor)
        metadata = self.get_document(reference)
        try:
            path = resolve_file_under(self.root, str(metadata.get("last_path", "")))
        except (OSError, ValueError) as exc:
            raise ValueError("document file is unavailable") from exc
        sidecar_dir = path.parent / CONTROL_DIR
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar = sidecar_dir / f"{path.name}.simpleoffice.json"
        payload = {
            "schema": "https://simpleoffice.local/schemas/portable-file-metadata/v1",
            "version": 1,
            "document_id": metadata["document_id"], "file_name": path.name,
            "sha256": sha256_file(path), "state": metadata.get("state", "new"),
            "tags": sorted(set(metadata.get("tags", [])), key=str.casefold),
            "description": str(metadata.get("attributes", {}).get("description", "")),
            "origin": metadata.get("attributes", {}).get("attachment_origin", {}),
            "exported_at": utc_now(),
        }
        atomic_json_write(sidecar, payload)
        self._event("portable_metadata_exported", {"document_id": metadata["document_id"], "actor": actor, "sidecar": self.relative(sidecar)})
        self._record_revision("portable_metadata_exported", actor, "documents", metadata["document_id"], payload)
        return sidecar

    def export_all_portable_metadata(self, actor: str) -> dict[str, int]:
        result = {"exported": 0, "errors": 0}
        for metadata in self._all_documents():
            try:
                self.export_portable_metadata(metadata["document_id"], actor)
                result["exported"] += 1
            except (OSError, ValueError):
                result["errors"] += 1
        self._record_revision("portable_metadata_bulk_exported", actor, "documents", "portable-metadata", result)
        return result

    def add_deadline(
        self,
        reference: str | Path,
        kind: str,
        expires_at: str,
        label: str,
        author: str,
    ) -> dict[str, Any]:
        self._require_actor(author)
        kind = kind.strip().casefold()
        if kind not in {"retention", "work"}:
            raise ValueError("deadline kind must be retention or work")
        parsed = parse_deadline(expires_at)
        deadline = {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "expires_at": parsed.isoformat(),
            "label": label.strip() or ("Aufbewahrung" if kind == "retention" else "Bearbeiten bis"),
            "created_at": utc_now(),
            "created_by": author,
        }
        metadata = self.get_document(reference)
        metadata.setdefault("deadlines", []).append(deadline)
        self._save_document(metadata)
        self._event(
            "document_deadline_added",
            {"document_id": metadata["document_id"], "actor": author, **deadline},
        )
        self._record_revision(
            "document_deadline_added", author, "documents", metadata["document_id"], metadata
        )
        return deadline
