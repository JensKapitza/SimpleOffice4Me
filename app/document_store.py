"""File based document storage and repairable scan index.

The filesystem is the source of truth.  SQLite only caches scan results and
can therefore be deleted and rebuilt at any time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import fnmatch
import shutil
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

import click
from flask import current_app
from flask.cli import with_appcontext

from .retention import evaluate_deadlines, parse_deadline
from .revision_history import RevisionHistory
from .search_query import compile_query


CONTROL_DIR = ".simpleoffice-meta"
PREVIEW_CACHE_DIR = ".webcache"
POLICY_FILE = ".simpleoffice-folder.json"
EVENT_FILE = "events.ndjson"
HISTORY_DIR = ".simpleoffice-history"
ARCHIVES_FILE = "archives.json"
ARCHIVE_MARKER = ".simpleoffice-archive.json"
SHARES_FILE = "shares.json"
SSH_SOURCES_FILE = "ssh-sources.json"
MAX_WEBDAV_COLLECTION_MEMBERS = 2_000
MAX_WEBDAV_COLLECTION_DEPTH = 64
COLLECTION_TRASH_DIR = "webdav-collection-trash"
_STORE_INITIALIZATION_LOCK = threading.Lock()
_INITIALIZED_INDEXES: set[Path] = set()
_WAL_CONFIGURATION_LOCK = threading.Lock()
_WAL_CONFIGURED_INDEXES: set[Path] = set()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def ocr_subprocess_environment() -> dict[str, str]:
    """Return a bounded OpenMP environment for one Tesseract process."""
    environment = os.environ.copy()
    requested = environment.get(
        "SIMPLEOFFICE_OCR_THREADS",
        environment.get("OMP_THREAD_LIMIT", "1"),
    ).strip()
    try:
        threads = int(requested)
    except ValueError:
        threads = 1
    environment["OMP_THREAD_LIMIT"] = str(max(1, min(threads, 8)))
    return environment


@dataclass(frozen=True)
class ScanReport:
    files: int = 0
    new_files: int = 0
    updated_files: int = 0
    duplicates: int = 0
    symlinks: int = 0
    skipped_boundaries: int = 0
    errors: int = 0


class DocumentStore:
    """Filesystem store with xattrs when available and JSON sidecars otherwise."""

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
        # Recovery is intentionally not cached: an interrupted WebDAV DELETE
        # can leave a new recovery journal at any time during this process.
        self._recover_interrupted_collection_deletions()

    def _initialize_once(self) -> None:
        """Create or migrate the disposable index once per worker process."""
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
            # Additive migration for indexes created before move detection was
            # introduced. The filesystem remains the source of truth.
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
                # FTS5 is normally present in SQLite. A plain table keeps the
                # project usable on stripped-down platform builds.
                db.execute(
                    """CREATE TABLE IF NOT EXISTS document_search (
                    document_id TEXT PRIMARY KEY, path TEXT, state TEXT, tags TEXT,
                    notes TEXT, attributes TEXT, content TEXT)"""
                )
    def _recover_interrupted_collection_deletions(self) -> None:
        """Roll back a collection DELETE that stopped before it was committed."""
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
                    restored_file = self.root / str(snapshot.get("last_path", ""))
                    if restored_file.is_file() and not restored_file.is_symlink():
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
                # Do not overwrite an invalid policy. It must be repaired visibly.
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
        """Copy one local file into inbox without interpreting its filename as a command."""
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
        """Copy an existing directory into the managed archive without modifying it.

        The source tree remains untouched.  Its relative directory structure is
        retained below ``imports/<label>`` and name collisions create an
        additional file instead of replacing an earlier import.
        """
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
        """Store an uploaded file safely; archive placement is content-hash sorted."""
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
        """Describe the always-connected primary document archive separately."""
        self.initialize()
        policy = self._read_json(self.root / POLICY_FILE, {})
        return {"label": "Hauptarchiv (lokal)", "archive_id": policy.get("folder_id", "local"), "path": str(self.root), "available": True}

    def create_share(self, reference: str | Path, password: str, expires_days: int, actor: str, note_id: str = "") -> dict[str, Any]:
        """Create a password-protected, expiring share for a file or one note."""
        self._require_actor(actor)
        password = password.strip()
        if len(password) < 8:
            raise ValueError("share password must contain at least 8 characters")
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
        """Reactivate the same persistent URL, only with a replacement password."""
        self._require_actor(actor)
        if len(password.strip()) < 8: raise ValueError("share password must contain at least 8 characters")
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
        """Validate a share password and return its document metadata."""
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
        path = self.root / document.get("last_path", "")
        if not path.is_file() or path.is_symlink():
            raise ValueError("the shared original is currently unavailable")
        return {**result, "path": path}

    def record_share_view(self, share_id: str, remote_addr: str = "") -> None:
        payload = self._read_json(self.shares_path, {"shares": []})
        share = next((item for item in payload["shares"] if item.get("share_id") == share_id), None)
        if share: self._record_share_access(payload, share, "link_viewed", remote_addr)

    def record_access(self, reference: str | Path, actor: str, access_type: str) -> dict[str, Any]:
        """Audit access without rewriting a potentially large document sidecar."""
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
        """Register an SSH source without storing a password or private key."""
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
        """One-way import from an SSH source using rsync and the local SSH agent."""
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
        """Remove only the local SSH source configuration, never remote files."""
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
        """Mark a mounted archive volume so it remains identifiable while absent."""
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
        """Inspect mounted volume roots for our small portable archive marker."""
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
        """Return metadata by document ID or by a path inside the managed tree."""
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
        """Append an immutable note to a document's file-based metadata."""
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
        """Return the immutable PDF snapshot that was created with a note."""
        document = self.get_document(reference)
        note = next((item for item in document.get("notes", []) if item.get("id") == note_id), None)
        if note is None:
            raise ValueError("note does not exist on this document")
        path = self.note_snapshots / f"{note_id}.pdf"
        if not path.exists():
            self._write_note_snapshot(document, note)
        return path

    def set_state(self, reference: str | Path, state: str, author: str = "") -> dict[str, Any]:
        """Set a human workflow state and preserve the complete state history."""
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
        """Set a domain-specific metadata value without schema migration."""
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

    def set_malware_scan(self, reference: str | Path, value: dict[str, Any], author: str) -> None:
        """Persist an immutable-content security verdict even during retention lock."""
        self._require_actor(author)
        metadata = self.get_document(reference)
        metadata.setdefault("attributes", {})["malware_scan"] = dict(value)
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event("document_malware_scan_set", {"document_id": metadata["document_id"], "author": author, "verdict": value.get("verdict", "")})
        self._record_revision("document_malware_scan_set", author, "documents", metadata["document_id"], {"scan_id": value.get("scan_id", ""), "verdict": value.get("verdict", ""), "scanned_at": value.get("scanned_at", "")})

    def set_tags(self, reference: str | Path, tags: list[str], author: str = "") -> dict[str, Any]:
        """Replace document tags while retaining filesystem and revision metadata."""
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
        """Write an interoperable sidecar without renaming or modifying the file."""
        self._require_actor(actor)
        metadata = self.get_document(reference)
        path = self.root / str(metadata.get("last_path", ""))
        if not path.is_file() or path.is_symlink():
            raise ValueError("document file is unavailable")
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
        """Append an audited document deadline without replacing older rules."""
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
                rows = db.execute(
                    f"SELECT document_id, path, state FROM document_search WHERE {compiled.where} LIMIT ? OFFSET ?",
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

    def inbox_page(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        """Load an inbox page from the SQLite projection, never all sidecars.

        The projection is disposable and is populated incrementally by the
        index worker.  During an initial scan the page therefore stays fast
        and simply grows as documents become available.
        """
        self.initialize()
        page = max(1, page)
        page_size = max(1, min(500, page_size))
        where = "state = 'new' AND has_notes = 0 AND has_relationships = 0"
        with self._db() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM document_listing WHERE {where}").fetchone()[0])
            rows = db.execute(
                f"""SELECT document_id FROM document_listing WHERE {where}
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
        source = self.root / str(document.get("last_path", ""))
        if not source.is_file() or source.is_symlink():
            raise ValueError("only an available regular document file can be moved")
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
        """Replace one destination from a MOVE source with recovery and rollback.

        The destination keeps its stable identity, grants, tags and properties.
        Its old payload enters immutable content history, while the consumed
        source remains recoverable in the WebDAV trash. A source-side conflict
        after the destination write restores the previous destination bytes.
        """
        self._require_actor(actor)
        source = self.get_document(source_reference)
        destination = self.get_document(destination_reference)
        if source["document_id"] == destination["document_id"]:
            raise ValueError("source and destination are the same document")
        self._require_document_editable(source)
        self._require_document_editable(destination)
        source_path = self.root / str(source.get("last_path", ""))
        destination_path = self.root / str(destination.get("last_path", ""))
        if (
            not source_path.is_file() or source_path.is_symlink()
            or not destination_path.is_file() or destination_path.is_symlink()
        ):
            raise ValueError("MOVE replacement requires two available regular document files")
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
        """Copy source bytes over a destination while preserving its identity.

        COPY must leave the source untouched. The destination keeps its stable
        ID, grants, tags, retention state and WebDAV properties; replace_content
        archives the previous payload and performs the atomic write. Both
        resource validators are checked again after HTTP precondition handling
        so a late filesystem change cannot silently win.
        """
        self._require_actor(actor)
        source = self.get_document(source_reference)
        destination = self.get_document(destination_reference)
        if source["document_id"] == destination["document_id"]:
            raise ValueError("source and destination are the same document")
        self._require_document_editable(source)
        self._require_document_editable(destination)
        source_path = self.root / str(source.get("last_path", ""))
        destination_path = self.root / str(destination.get("last_path", ""))
        if (
            not source_path.is_file() or source_path.is_symlink()
            or not destination_path.is_file() or destination_path.is_symlink()
        ):
            raise ValueError("COPY replacement requires two available regular document files")
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
        source = self.root / str(source_metadata.get("last_path", ""))
        if not source.is_file() or source.is_symlink():
            raise ValueError("only an available regular document file can be copied")
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
                        portable_path = self.root / str(portable.get("last_path", ""))
                        try:
                            portable_path.resolve().relative_to(source.resolve())
                        except (OSError, ValueError):
                            raise ValueError("collection contains out-of-scope internal metadata") from None
                        registered = self.get_document(document_id)
                        active_match = (
                            registered.get("last_path") == portable.get("last_path")
                            and portable_path.is_file() and not portable_path.is_symlink()
                        )
                        deleted_match = (
                            registered.get("system_state") == "webdav_deleted"
                            and registered.get("deleted_from") == portable.get("last_path")
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
                    old_path = self.root / str(snapshot.get("last_path", ""))
                    if old_path.is_file() and not old_path.is_symlink():
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
                    original = self.root / str(snapshot.get("last_path", ""))
                    if original.is_file() and not original.is_symlink():
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

    def soft_delete_document(self, reference: str, actor: str, *, expected_sha256: str = "") -> dict[str, Any]:
        """Move a document into a private recovery area and retain its metadata."""
        self._require_actor(actor)
        from .file_lock import exclusive_file_lock

        with exclusive_file_lock(self.control / ".document-content.lock"):
            metadata = self.get_document(reference)
            self._require_document_editable(metadata)
            source = self.root / str(metadata.get("last_path", ""))
            if not source.is_file() or source.is_symlink():
                raise ValueError("only an available regular document file can be deleted")
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
            metadata = document if document_id == document["document_id"] else self._read_json(
                self.documents / f"{document_id}.json", {}
            )
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
                entries.append({
                    **note,
                    "document_id": document["document_id"],
                    "path": document.get("last_path", ""),
                    "version_number": document.get("version_number", 1),
                })
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
                    if not isinstance(event, dict):
                        continue
                    related = event.get("document_id") or event.get("source_document_id")
                    if document_id is None or related == document_id:
                        entries.append({**event, "source": "scanner"})
            except (OSError, json.JSONDecodeError):
                pass
        return sorted(entries, key=lambda item: item.get("at", ""), reverse=True)

    def logbook_page(self, *, page: int = 1, page_size: int = 50, query: str = "", actor: str = "", action: str = "", from_at: str = "", to_at: str = "") -> dict[str, Any]:
        """Return a bounded, filtered audit-log page instead of loading all events."""
        page = max(1, int(page))
        page_size = min(100, max(10, int(page_size)))
        needed = page * page_size + 1
        query, actor, action = query.casefold().strip(), actor.casefold().strip(), action.casefold().strip()

        def matches(event: dict[str, Any]) -> bool:
            timestamp = str(event.get("at", ""))
            if from_at and timestamp < from_at:
                return False
            if to_at and timestamp > f"{to_at}T23:59:59.999999+00:00":
                return False
            if actor and actor not in str(event.get("actor", "")).casefold():
                return False
            if action and action not in str(event.get("action", event.get("type", ""))).casefold():
                return False
            return not query or query in json.dumps(event, ensure_ascii=False).casefold()

        def take(items):
            found = []
            for item in items:
                if matches(item):
                    found.append(item)
                    if len(found) >= needed:
                        break
            return found

        history_dir = self.history.root / "events"
        history_events = take(
            {**self._read_json(path, {}), "source": "revision"}
            for path in sorted(history_dir.glob("*.json"), reverse=True)
        ) if history_dir.exists() else []
        if self.events.exists() and not any((query, actor, action, from_at, to_at)):
            with self.events.open(encoding="utf-8") as source:
                scanner_lines = list(deque(source, maxlen=needed))
        elif self.events.exists():
            with self.events.open(encoding="utf-8") as source:
                scanner_lines = list(source)
        else:
            scanner_lines = []
        scanner_events = take(
            {**event, "source": "scanner"}
            for line in reversed(scanner_lines)
            for event in [json.loads(line)]
            if isinstance(event, dict)
        )
        merged = sorted([*history_events, *scanner_events], key=lambda item: item.get("at", ""), reverse=True)
        start = (page - 1) * page_size
        return {"events": merged[start:start + page_size], "page": page, "has_next": len(merged) > start + page_size}

    def scan(
        self,
        progress: Callable[[ScanReport], None] | None = None,
        file_progress: Callable[[Path], None] | None = None,
        verify_hashes: bool = False,
        post_file: Callable[[Path], None] | None = None,
    ) -> ScanReport:
        self.initialize()
        files = new_files = updated_files = duplicates = symlinks = skipped_boundaries = errors = 0
        # Device/inode tracking prevents a deliberately allowed symlink from
        # walking back into an already visited directory tree.
        pending = [self.root]
        visited_directories: set[tuple[int, int]] = set()
        while pending:
            current_path = pending.pop()
            try:
                current_stat = current_path.stat()
                key = (current_stat.st_dev, current_stat.st_ino)
                if key in visited_directories:
                    self._event("directory_cycle_skipped", {"path": self.relative(current_path)})
                    continue
                visited_directories.add(key)
                options = self._scan_options(current_path)
                entries = sorted(current_path.iterdir(), key=lambda entry: entry.name.lower())
            except (OSError, ValueError) as exc:
                errors += 1
                self._event("folder_policy_invalid", {"path": self.relative(current_path), "error": str(exc)})
                continue
            for path in entries:
                if path.name in (POLICY_FILE, CONTROL_DIR, HISTORY_DIR, PREVIEW_CACHE_DIR):
                    continue
                try:
                    if path.is_symlink():
                        symlinks += 1
                        target = path.resolve(strict=True)
                        self._event("symlink_seen", {"path": self.relative(path), "target": str(target)})
                        if not options["follow_symlinks"]:
                            continue
                        if target.is_dir():
                            pending.append(target)
                        elif target.is_file():
                            if file_progress: file_progress(target)
                            created, updated, duplicate = self._scan_file(target, force_hash=verify_hashes)
                            if post_file: post_file(target)
                            files += 1
                            new_files += int(created)
                            updated_files += int(updated)
                            duplicates += int(duplicate)
                        continue
                    entry_stat = path.stat(follow_symlinks=False)
                    if path.is_dir():
                        if entry_stat.st_dev != current_stat.st_dev and not options["allow_other_filesystems"]:
                            skipped_boundaries += 1
                            self._event(
                                "filesystem_boundary_skipped",
                                {"path": self.relative(path), "device": entry_stat.st_dev},
                            )
                            continue
                        pending.append(path)
                        continue
                    if not path.is_file():
                        continue
                    if file_progress: file_progress(path)
                    created, updated, duplicate = self._scan_file(path, force_hash=verify_hashes)
                    if post_file: post_file(path)
                    files += 1
                    new_files += int(created)
                    updated_files += int(updated)
                    duplicates += int(duplicate)
                except (OSError, ValueError) as exc:
                    errors += 1
                    self._event("scan_error", {"path": self.relative(path), "error": str(exc)})
                if progress:
                    progress(ScanReport(files, new_files, updated_files, duplicates, symlinks, skipped_boundaries, errors))
        report = ScanReport(files, new_files, updated_files, duplicates, symlinks, skipped_boundaries, errors)
        if progress: progress(report)
        return report

    def scan_status(self) -> dict[str, Any]:
        self.initialize()
        return self._read_json(self.scan_status_path, {"state": "idle", "updated_at": None})

    def set_scan_status(self, status: dict[str, Any]) -> None:
        self.initialize()
        atomic_json_write(self.scan_status_path, {**status, "updated_at": utc_now()})

    def set_preview_metadata(self, reference: str, preview: dict[str, Any]) -> None:
        """Persist index-worker preview state without touching document content."""
        metadata = self.get_document(reference)
        metadata["preview"] = preview
        self._save_document(metadata)

    def relative(self, path: Path) -> str:
        resolved = path.resolve()
        if resolved == self.root:
            return "."
        try:
            return str(resolved.relative_to(self.root))
        except ValueError:
            return f"[external] {resolved}"

    def _scan_options(self, folder: Path) -> dict[str, bool]:
        """Read the policy of a folder inside the managed tree.

        A symlink target outside the tree has no implicit permission to escape
        further boundaries. The link itself must have been explicitly allowed
        by the policy of its parent directory.
        """
        try:
            self.ensure_folder_policy(folder)
            policy = self._read_json(folder / POLICY_FILE, {})
        except ValueError:
            policy = {}
        configured = policy.get("scan", {}) if isinstance(policy.get("scan", {}), dict) else {}
        return {
            "follow_symlinks": configured.get("follow_symlinks") is True,
            "allow_other_filesystems": configured.get("allow_other_filesystems") is True,
        }

    def _scan_file(self, path: Path, force_hash: bool = False) -> tuple[bool, bool, bool]:
        stat = path.stat()
        relative_path = self.relative(path)
        now = utc_now()
        cached: tuple[str, str] | None = None
        previous_path = ""
        if not force_hash:
            with self._db() as db:
                row = db.execute(
                    """SELECT document_id, sha256 FROM scan_file
                       WHERE relative_path = ? AND size = ? AND modified_ns = ?""",
                    (relative_path, stat.st_size, stat.st_mtime_ns),
                ).fetchone()
                if row:
                    metadata = self._read_json(self.documents / f"{row[0]}.json", {})
                    db.execute(
                        """UPDATE scan_file SET last_seen_at = ?, device = ?, inode = ?
                           WHERE relative_path = ?""",
                        (now, stat.st_dev, stat.st_ino, relative_path),
                    )
                    if metadata.get("document_id"):
                        # Additive projections are rebuilt during an ordinary
                        # scan without rehashing or extracting the file.
                        self._refresh_listing_index(metadata, db)
                        return False, False, metadata.get("system_state") == "duplicate"
                    # The SQLite index is disposable. Rebuild a missing sidecar
                    # from its cached identity instead of hiding the damage.
                    cached = (row[0], row[1])
                else:
                    moved = db.execute(
                        """SELECT relative_path, document_id, sha256 FROM scan_file
                           WHERE device = ? AND inode = ? AND size = ? AND modified_ns = ?
                           ORDER BY last_seen_at DESC LIMIT 1""",
                        (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns),
                    ).fetchone()
                    if moved:
                        previous_path, document_id, digest = moved
                        cached = (document_id, digest)

        xattrs = self._read_xattrs(path)
        known_identity = cached is not None or bool(xattrs.get("document_id"))
        if cached:
            document_id, digest = cached
        else:
            digest = sha256_file(path)
            document_id = xattrs.get("document_id") or str(uuid.uuid4())
        metadata_path = self.documents / f"{document_id}.json"
        metadata_exists = metadata_path.exists()
        created = not metadata_exists and not known_identity
        updated = not created
        metadata = self._read_json(metadata_path, {})
        original_sha256 = metadata.get("original_sha256", digest)
        integrity_changed = original_sha256 != digest
        existing_tags = metadata.get("tags", xattrs.get("tags", []))
        detected_tags = self._filename_tags(path.name)
        all_tags = sorted({*existing_tags, *detected_tags}, key=str.casefold)
        tagged_at = metadata.get("tagged_at", {})
        if not isinstance(tagged_at, dict):
            tagged_at = {}
        for tag in all_tags:
            tagged_at.setdefault(tag, metadata.get("first_seen_at", now))
        if previous_path and previous_path != relative_path:
            metadata["location_history"] = [
                *metadata.get("location_history", []),
                {"from": previous_path, "to": relative_path, "at": now, "reason": "filesystem_scan"},
            ]
            with self._db() as db:
                db.execute("DELETE FROM scan_file WHERE relative_path = ?", (previous_path,))
            self._event(
                "file_move_detected",
                {"document_id": document_id, "from": previous_path, "to": relative_path},
            )
        metadata.update(
            {
                "version": 1,
                "document_id": document_id,
                "sha256": digest,
                "first_seen_at": metadata.get("first_seen_at", now),
                "last_seen_at": now,
                "last_path": relative_path,
                "tags": all_tags,
                "tagged_at": tagged_at,
                "original_sha256": original_sha256,
                "content_sha256": digest,
                "notes": metadata.get("notes", []),
                "relationships": metadata.get("relationships", []),
                "state": metadata.get("state", "new"),
                "state_history": metadata.get("state_history", []),
                "version_series_id": metadata.get("version_series_id", document_id),
                "version_number": metadata.get("version_number", 1),
                "attributes": metadata.get("attributes", {}),
                "deadlines": metadata.get("deadlines", []),
            }
        )
        atomic_json_write(metadata_path, metadata)
        self._write_xattrs(path, document_id, digest, metadata["tags"])

        fingerprint_path = self.fingerprints / f"{digest}.json"
        fingerprint = self._read_json(fingerprint_path, {})
        known_paths = set(fingerprint.get("paths", []))
        if previous_path:
            known_paths.discard(previous_path)
        duplicate = bool(known_paths and relative_path not in known_paths)
        known_paths.add(relative_path)
        fingerprint.update(
            {
                "sha256": digest,
                "first_seen_at": fingerprint.get("first_seen_at", now),
                "last_seen_at": now,
                "paths": sorted(known_paths),
                "seen_count": int(fingerprint.get("seen_count", 0)) + 1,
            }
        )
        atomic_json_write(fingerprint_path, fingerprint)
        metadata["system_state"] = "integrity_changed" if integrity_changed else ("duplicate" if duplicate else "indexed")
        if self._is_image(path):
            self._apply_image_analysis(path, metadata)
        self._apply_document_text_extraction(path, metadata)
        self._save_document(metadata)
        if integrity_changed:
            self._event(
                "integrity_changed",
                {"document_id": document_id, "expected_sha256": original_sha256, "observed_sha256": digest},
            )
        self._refresh_search_index(metadata)
        with self._db() as db:
            db.execute(
                """INSERT INTO scan_file(
                       relative_path, document_id, sha256, size, modified_ns, device, inode, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(relative_path) DO UPDATE SET document_id=excluded.document_id,
                     sha256=excluded.sha256, size=excluded.size, modified_ns=excluded.modified_ns,
                     device=excluded.device, inode=excluded.inode, last_seen_at=excluded.last_seen_at""",
                (
                    relative_path, document_id, digest, stat.st_size, stat.st_mtime_ns,
                    stat.st_dev, stat.st_ino, now,
                ),
            )
        self._event(
            "file_seen",
            {"path": relative_path, "document_id": document_id, "sha256": digest, "first_seen": created, "duplicate": duplicate},
        )
        return created, updated, duplicate

    @staticmethod
    def _filename_tags(filename: str) -> list[str]:
        """Keep the complete filename stem and useful words as non-destructive tags."""
        stem = Path(filename).stem.strip()
        if not stem:
            return []
        words = [part.strip() for part in re.split(r"[\s_.-]+", stem) if len(part.strip()) > 1]
        return [stem, *words]

    @staticmethod
    def _is_image(path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}

    def _apply_document_text_extraction(self, path: Path, metadata: dict[str, Any], force: bool = False) -> bool:
        """Store extracted text beside a document and make it part of full-text search."""
        current = metadata.get("text_extraction", {})
        if not force and current.get("source_sha256") == metadata.get("sha256") and "extracted_text" in metadata:
            return False
        analysis: dict[str, Any] = {"source_sha256": metadata.get("sha256", ""), "extracted_at": utc_now(), "status": "completed"}
        native_text = ""
        image_text = ""
        try:
            if self._is_image(path):
                native_text = metadata.get("ocr_text", "")
                analysis["kind"] = "image"
            elif path.suffix.lower() == ".pdf":
                native_text = self._pdf_text(path)
                image_text = self._pdf_image_ocr(path)
                analysis["kind"] = "pdf"
            else:
                native_text, kind = self._file_text(path)
                analysis["kind"] = kind
        except RuntimeError as exc:
            analysis["status"] = "partial"
            analysis["error"] = str(exc)
        combined = "\n".join(part for part in (native_text, image_text) if part).strip()
        metadata["extracted_text"] = combined
        metadata["text_extraction"] = {**analysis, "native_characters": len(native_text), "image_ocr_characters": len(image_text), "characters": len(combined)}
        return True

    @staticmethod
    def _pdf_text(path: Path) -> str:
        executable = shutil.which("pdftotext")
        if executable:
            try:
                result = subprocess.run([executable, "-layout", str(path), "-"], capture_output=True, text=True, timeout=90, check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("PDF text extraction timed out after 90 seconds") from exc
            if result.returncode == 0:
                return "\n".join(line.rstrip() for line in result.stdout.splitlines()).strip()

        # GitHub Actions and portable installations do not necessarily provide
        # Poppler's pdftotext binary. Keep a pure-Python fallback available.
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception as exc:
            if executable:
                raise RuntimeError(result.stderr.strip() or "PDF text extraction failed") from exc
            raise RuntimeError("PDF text extraction requires pypdf when pdftotext is unavailable") from exc

    def _pdf_image_ocr(self, path: Path) -> str:
        executable = shutil.which("pdfimages")
        if not executable:
            return ""
        with tempfile.TemporaryDirectory(prefix="simpleoffice-pdf-images-") as temp:
            output_prefix = Path(temp) / "image"
            try:
                result = subprocess.run([executable, "-png", str(path), str(output_prefix)], capture_output=True, text=True, timeout=120, check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("PDF image extraction timed out after 120 seconds") from exc
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "PDF image extraction failed")
            texts: list[str] = []
            for image_path in sorted(Path(temp).glob("image-*.png"))[:100]:
                try:
                    text = self._image_ocr(image_path)
                    if text:
                        texts.append(text)
                except RuntimeError:
                    continue
            return "\n".join(texts)

    @staticmethod
    def _file_text(path: Path) -> tuple[str, str]:
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".eml", ".ics", ".vcf", ".py", ".java", ".js", ".css", ".sql", ".yml", ".yaml"}:
            return path.read_text(encoding="utf-8", errors="replace"), "plain_text"
        if suffix in {".docx", ".odt", ".xlsx", ".ods"}:
            try:
                with zipfile.ZipFile(path) as archive:
                    text_parts = []
                    for name in archive.namelist():
                        if not name.endswith(".xml") or name.startswith("docProps/"):
                            continue
                        try:
                            root = ElementTree.fromstring(archive.read(name))
                            text_parts.extend(value.strip() for value in root.itertext() if value.strip())
                        except ElementTree.ParseError:
                            continue
                return "\n".join(text_parts), "office_zip"
            except (OSError, zipfile.BadZipFile) as exc:
                raise RuntimeError(f"office document extraction failed: {exc}") from exc
        return "", "unsupported"

    def _apply_image_analysis(self, path: Path, metadata: dict[str, Any], force: bool = False) -> None:
        """Keep analysis local; failures are recorded with the document, not hidden."""
        current = metadata.get("image_analysis", {})
        if not force and current.get("source_sha256") == metadata.get("sha256"):
            return
        analysis: dict[str, Any] = {"source_sha256": metadata.get("sha256", ""), "analyzed_at": utc_now(), "exif": {}, "ocr_status": "not_run"}
        generated_tags = {"bild", f"format-{path.suffix.lower().lstrip('.')}"}
        try:
            from PIL import ExifTags, Image
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                analysis["format"] = image.format or path.suffix.lstrip(".").upper()
                analysis["width"], analysis["height"] = image.size
                exif: dict[str, Any] = {}
                raw_exif = image.getexif()
                for key, value in raw_exif.items():
                    label = ExifTags.TAGS.get(key, str(key))
                    if label in {"Make", "Model", "Software", "DateTime", "DateTimeOriginal", "DateTimeDigitized", "Orientation"}:
                        exif[label] = str(value)
                    elif label == "GPSInfo" and value:
                        gps = self._gps_data(value, ExifTags.GPSTAGS)
                        if gps:
                            exif["GPS"] = gps
                analysis["exif"] = exif
                camera = " ".join(part for part in (exif.get("Make", ""), exif.get("Model", "")) if part).strip()
                if camera:
                    generated_tags.add(f"kamera-{self._tag_token(camera)}")
                date_value = exif.get("DateTimeOriginal") or exif.get("DateTime")
                if date_value and len(date_value) >= 4 and date_value[:4].isdigit():
                    generated_tags.add(f"jahr-{date_value[:4]}")
        except ImportError:
            analysis["metadata_error"] = "Pillow is not installed"
        except (OSError, ValueError, SyntaxError) as exc:
            analysis["metadata_error"] = str(exc)

        try:
            ocr_text = self._image_ocr(path)
            metadata["ocr_text"] = ocr_text
            analysis["ocr_status"] = "completed"
            analysis["ocr_characters"] = len(ocr_text)
            generated_tags.update(self._ocr_tags(ocr_text))
        except RuntimeError as exc:
            analysis["ocr_status"] = "unavailable"
            analysis["ocr_error"] = str(exc)
        metadata["image_analysis"] = analysis
        metadata["tags"] = sorted({*metadata.get("tags", []), *generated_tags}, key=str.casefold)

    @staticmethod
    def _gps_data(value: Any, labels: dict[int, str]) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"present": True}
        data = {labels.get(key, str(key)): str(item) for key, item in value.items()}
        return {"present": True, **data}

    @staticmethod
    def _tag_token(value: str) -> str:
        token = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
        return token[:64] or "unbekannt"

    @classmethod
    def _ocr_tags(cls, text: str) -> set[str]:
        ignored = {"aber", "alle", "auch", "dass", "der", "die", "das", "den", "dem", "des", "eine", "einer", "einem", "einen", "für", "mit", "nach", "oder", "und", "von", "zum", "zur", "this", "that", "with", "from", "your"}
        words = [cls._tag_token(word) for word in re.findall(r"[A-Za-zÄÖÜäöüß0-9]{4,}", text)]
        unique = list(dict.fromkeys(word for word in words if word not in ignored and word != "unbekannt" and not word.isdigit() and len(word) >= 4))
        return set(unique[:12])

    @staticmethod
    def _image_ocr(path: Path) -> str:
        executable = shutil.which("tesseract")
        if not executable:
            raise RuntimeError("Tesseract OCR is not installed")
        environment = ocr_subprocess_environment()
        try:
            result = subprocess.run([executable, str(path), "stdout", "-l", "deu+eng"], capture_output=True, text=True, timeout=90, check=False, env=environment)
            if result.returncode != 0 and "deu" in result.stderr.lower():
                result = subprocess.run([executable, str(path), "stdout", "-l", "eng"], capture_output=True, text=True, timeout=90, check=False, env=environment)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("OCR timed out after 90 seconds") from exc
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Tesseract OCR failed")
        return " ".join(result.stdout.split())

    def _save_document(self, metadata: dict[str, Any]) -> None:
        atomic_json_write(self.documents / f"{metadata['document_id']}.json", metadata)
        relative_path = str(metadata.get("last_path", ""))
        if relative_path and not relative_path.startswith("[external]"):
            document_path = self.root / relative_path
            if document_path.is_file() and not document_path.is_symlink():
                atomic_json_write(document_path.parent / CONTROL_DIR / f"{metadata['document_id']}.json", metadata)
                self._write_context_xattrs(document_path, metadata)

    def _write_note_snapshot(self, document: dict[str, Any], note: dict[str, Any]) -> Path:
        """Create once; a note itself is immutable, so its PDF is a stable snapshot."""
        path = self.note_snapshots / f"{note['id']}.pdf"
        if path.exists():
            return path
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.pdfgen.canvas import Canvas
        except ImportError as exc:
            raise RuntimeError("reportlab is required for note PDF snapshots") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        canvas = Canvas(str(temporary), pagesize=A4, pageCompression=1)
        width, height = A4
        x, y = 20 * mm, height - 20 * mm
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(x, y, "SimpleOffice4Me - Notiz-Snapshot")
        y -= 10 * mm
        canvas.setFont("Helvetica", 9)
        for line in (f"Dokument: {document.get('last_path', '')}", f"Dokument-ID: {document['document_id']}", f"Notiz-ID: {note['id']}", f"Autor: {note.get('author', '')}", f"Erstellt: {note.get('created_at', '')}"):
            canvas.drawString(x, y, line[:150])
            y -= 5 * mm
        y -= 4 * mm
        canvas.setFont("Helvetica", 11)
        words = note.get("text", "").split()
        line = ""
        for word in words or [""]:
            candidate = f"{line} {word}".strip()
            if canvas.stringWidth(candidate, "Helvetica", 11) > width - 40 * mm:
                canvas.drawString(x, y, line)
                y -= 6 * mm
                if y < 20 * mm:
                    canvas.showPage(); y = height - 20 * mm; canvas.setFont("Helvetica", 11)
                line = word
            else:
                line = candidate
        canvas.drawString(x, y, line)
        canvas.save()
        temporary.replace(path)
        return path

    @staticmethod
    def _write_context_xattrs(path: Path, metadata: dict[str, Any]) -> None:
        if not hasattr(os, "setxattr"):
            return
        try:
            os.setxattr(path, "user.simpleoffice.state", str(metadata.get("state", "new")).encode("utf-8"))
            notes = json.dumps(metadata.get("notes", []), ensure_ascii=False).encode("utf-8")
            if len(notes) <= 2048:
                os.setxattr(path, "user.simpleoffice.notes", notes)
            else:
                os.setxattr(path, "user.simpleoffice.notes_ref", f"{CONTROL_DIR}/{metadata['document_id']}.json".encode("utf-8"))
        except OSError:
            return

    def _record_revision(self, action: str, actor: str, category: str, key: str, snapshot: dict[str, Any]) -> None:
        commit = self.history.record(action, actor, category, key, snapshot)
        self._event("revision_recorded", {"action": action, "actor": actor, "commit": commit, "key": key})

    @staticmethod
    def _require_actor(actor: str) -> None:
        if not actor.strip():
            raise ValueError("a named user is required for every write action")

    def _deadline_rules(
        self, metadata: dict[str, Any]
    ) -> list[tuple[dict[str, Any], str, str]]:
        rules: list[tuple[dict[str, Any], str, str]] = [
            (rule, "document", metadata["document_id"])
            for rule in metadata.get("deadlines", [])
            if isinstance(rule, dict)
        ]
        relative_path = Path(str(metadata.get("last_path", "")))
        if relative_path.is_absolute() or str(relative_path).startswith("[external]"):
            return rules
        document_folder = (self.root / relative_path).parent.resolve()
        try:
            document_folder.relative_to(self.root)
        except ValueError:
            return rules
        folders = [self.root]
        current = self.root
        for part in document_folder.relative_to(self.root).parts:
            current = current / part
            folders.append(current)
        for folder in folders:
            policy = self._read_json(folder / POLICY_FILE, {})
            retention = policy.get("retention", {})
            configured = retention.get("rules", []) if isinstance(retention, dict) else []
            source = self.relative(folder)
            rules.extend(
                (rule, "folder", source) for rule in configured if isinstance(rule, dict)
            )
        return rules

    def _require_document_editable(self, metadata: dict[str, Any]) -> None:
        if metadata.get("cleanup_state") == "staged":
            raise ValueError("document is staged for manual deletion and cannot be edited")
        status = self.retention_status(metadata["document_id"])
        if status["work_locked"]:
            raise ValueError(
                f"document is locked since {status['work_until']}; only deadline and cleanup actions remain allowed"
            )

    def _refresh_search_index(self, metadata: dict[str, Any]) -> None:
        row = (
            metadata["document_id"],
            metadata.get("last_path", ""),
            metadata.get("state", ""),
            " ".join(metadata.get("tags", [])),
            "\n".join(note.get("text", "") for note in metadata.get("notes", [])),
            json.dumps(metadata.get("attributes", {}), ensure_ascii=False),
            "\n".join(part for part in (metadata.get("extracted_text", ""), metadata.get("ocr_text", "")) if part),
        )
        with self._db() as db:
            db.execute("DELETE FROM document_search WHERE document_id = ?", (metadata["document_id"],))
            db.execute(
                "INSERT INTO document_search(document_id, path, state, tags, notes, attributes, content) VALUES (?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        self._refresh_listing_index(metadata)

    def _refresh_listing_index(
        self,
        metadata: dict[str, Any],
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Update the small projection used by login, dashboard and inbox."""
        def update(db: sqlite3.Connection) -> None:
            db.execute(
                """INSERT INTO document_listing(
                       document_id, path, state, has_notes, has_relationships, last_seen_at,
                       version_series_id, version_number
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(document_id) DO UPDATE SET
                     path=excluded.path, state=excluded.state,
                     has_notes=excluded.has_notes,
                     has_relationships=excluded.has_relationships,
                     last_seen_at=excluded.last_seen_at,
                     version_series_id=excluded.version_series_id,
                     version_number=excluded.version_number""",
                (
                    metadata["document_id"],
                    str(metadata.get("last_path", "")),
                    str(metadata.get("state", "new") or "new"),
                    int(bool(metadata.get("notes"))),
                    int(bool(metadata.get("relationships"))),
                    str(metadata.get("last_seen_at", "")),
                    str(metadata.get("version_series_id", metadata["document_id"])),
                    int(metadata.get("version_number", 1)),
                ),
            )
            db.execute("DELETE FROM document_relationship WHERE source_id = ?", (metadata["document_id"],))
            db.executemany(
                """INSERT OR REPLACE INTO document_relationship(
                       source_id, target_id, propagates_retention
                   ) VALUES (?, ?, ?)""",
                [
                    (
                        metadata["document_id"],
                        str(link["target_document_id"]),
                        int(link.get("propagates_retention") is True),
                    )
                    for link in metadata.get("relationships", [])
                    if isinstance(link, dict) and link.get("target_document_id")
                ],
            )
        if connection is not None:
            update(connection)
            return
        with self._db() as db:
            update(db)

    def _all_documents(self) -> list[dict[str, Any]]:
        self.initialize()
        return [
            metadata
            for path in self.documents.glob("*.json")
            if (metadata := self._read_json(path, {})).get("document_id")
        ]

    @staticmethod
    def _mounted_roots() -> list[Path]:
        """Return mounted volume roots without recursively scanning drives."""
        roots: set[Path] = set()
        if sys.platform.startswith("win"):
            try:
                import ctypes
                mask = ctypes.windll.kernel32.GetLogicalDrives()
                for index in range(26):
                    if mask & (1 << index):
                        roots.add(Path(f"{chr(65 + index)}:/"))
            except (AttributeError, OSError):
                pass
        elif sys.platform == "darwin":
            roots.add(Path("/Volumes"))
            if Path("/Volumes").is_dir():
                roots.update(path for path in Path("/Volumes").iterdir() if path.is_dir())
        else:
            try:
                for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
                    fields = line.split()
                    if len(fields) > 1:
                        roots.add(Path(fields[1].replace("\\040", " ")))
            except OSError:
                roots.update(path for path in (Path("/media"), Path("/mnt")) if path.is_dir())
        return sorted((path for path in roots if path.is_dir()), key=str)

    def _db(self) -> sqlite3.Connection:
        self.control.mkdir(parents=True, exist_ok=True)
        # The scanner and web requests use short independent connections. WAL
        # permits readers while the scanner updates the index; the timeout also
        # prevents transient writer contention from becoming an HTTP 500.
        connection = sqlite3.connect(self.index_path, timeout=15)
        connection.execute("PRAGMA busy_timeout = 15000")
        if self.index_path not in _WAL_CONFIGURED_INDEXES:
            with _WAL_CONFIGURATION_LOCK:
                if self.index_path not in _WAL_CONFIGURED_INDEXES:
                    connection.execute("PRAGMA journal_mode = WAL")
                    _WAL_CONFIGURED_INDEXES.add(self.index_path)
        return connection

    def _event(self, event_type: str, data: dict[str, Any]) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        record = {"at": utc_now(), "type": event_type, **data}
        with self.events.open("a", encoding="utf-8") as destination:
            destination.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else default
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _read_xattrs(path: Path) -> dict[str, Any]:
        if not hasattr(os, "getxattr"):
            return {}
        try:
            result: dict[str, Any] = {}
            for key, name in (("document_id", "user.simpleoffice.id"), ("sha256", "user.simpleoffice.sha256"), ("tags", "user.simpleoffice.tags")):
                try:
                    value = os.getxattr(path, name).decode("utf-8")
                    result[key] = json.loads(value) if key == "tags" else value
                except OSError:
                    pass
            return result
        except OSError:
            return {}

    @staticmethod
    def _write_xattrs(path: Path, document_id: str, digest: str, tags: list[str]) -> None:
        if not hasattr(os, "setxattr"):
            return
        try:
            os.setxattr(path, "user.simpleoffice.id", document_id.encode("utf-8"))
            os.setxattr(path, "user.simpleoffice.sha256", digest.encode("utf-8"))
            os.setxattr(path, "user.simpleoffice.tags", json.dumps(tags).encode("utf-8"))
        except OSError:
            # FAT, SMB and backup media often do not support xattrs. Sidecars are enough.
            return


@click.command("init-document-store")
@click.argument("root", type=click.Path(path_type=Path))
def init_document_store_command(root: Path) -> None:
    """Create the control files and a rebuildable index for ROOT."""
    DocumentStore(root).initialize()
    click.echo(f"Document store initialized: {root}")


@click.command("scan-documents")
@click.option("--root", type=click.Path(path_type=Path), default=None, help="Document root; defaults to SIMPLEOFFICE_DOCUMENT_ROOT.")
@click.option(
    "--verify-hashes",
    is_flag=True,
    help="Recalculate every SHA-256 checksum even when size and modification time are unchanged.",
)
@with_appcontext
def scan_documents_command(root: Path | None, verify_hashes: bool) -> None:
    """Scan documents and update the repairable index."""
    store = DocumentStore(root or current_app.config["DOCUMENT_ROOT"])
    report = store.scan(verify_hashes=verify_hashes)
    click.echo(
        f"files={report.files} new={report.new_files} updated={report.updated_files} duplicates={report.duplicates} "
        f"symlinks={report.symlinks} boundaries={report.skipped_boundaries} errors={report.errors}"
    )


@click.command("document-note")
@click.argument("document")
@click.argument("text")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_note_command(document: str, text: str, actor: str) -> None:
    """Add TEXT as a note to DOCUMENT (ID or relative file path)."""
    note = DocumentStore(current_app.config["DOCUMENT_ROOT"]).add_note(document, text, actor)
    click.echo(note["id"])


@click.command("document-state")
@click.argument("document")
@click.argument("state")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_state_command(document: str, state: str, actor: str) -> None:
    """Set the human workflow STATE of DOCUMENT."""
    changed = DocumentStore(current_app.config["DOCUMENT_ROOT"]).set_state(document, state, actor)
    click.echo(json.dumps(changed, ensure_ascii=False))


@click.command("document-link")
@click.argument("source")
@click.argument("target")
@click.option("--type", "relation_type", default="related", show_default=True)
@click.option("--label", default="")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_link_command(source: str, target: str, relation_type: str, label: str, actor: str) -> None:
    """Link SOURCE to TARGET for the document mindmap."""
    link = DocumentStore(current_app.config["DOCUMENT_ROOT"]).add_link(source, target, relation_type, label, actor)
    click.echo(link["id"])


@click.command("document-graph")
@click.argument("document")
@with_appcontext
def document_graph_command(document: str) -> None:
    """Print graph data for DOCUMENT as JSON."""
    graph = DocumentStore(current_app.config["DOCUMENT_ROOT"]).graph(document)
    click.echo(json.dumps(graph, ensure_ascii=False, indent=2))


@click.command("document-attribute")
@click.argument("document")
@click.argument("key")
@click.argument("value")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_attribute_command(document: str, key: str, value: str, actor: str) -> None:
    """Set a freely modelled KEY/VALUE attribute on DOCUMENT."""
    DocumentStore(current_app.config["DOCUMENT_ROOT"]).set_attribute(document, key, value, actor)
    click.echo(key)


@click.command("document-deadline")
@click.argument("document")
@click.argument("expires_at")
@click.option("--kind", type=click.Choice(["retention", "work"]), default="retention")
@click.option("--label", default="")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_deadline_command(
    document: str, expires_at: str, kind: str, label: str, actor: str
) -> None:
    """Append one retention or work deadline to DOCUMENT."""
    deadline = DocumentStore(current_app.config["DOCUMENT_ROOT"]).add_deadline(
        document, kind, expires_at, label, actor
    )
    click.echo(json.dumps(deadline, ensure_ascii=False))


@click.command("retention-status")
@click.argument("document")
@with_appcontext
def retention_status_command(document: str) -> None:
    """Explain every direct, inherited and transitive deadline."""
    status = DocumentStore(current_app.config["DOCUMENT_ROOT"]).retention_status(document)
    click.echo(json.dumps(status, ensure_ascii=False, indent=2))


@click.command("retention-cleanup")
@click.option("--destination", default="Aussonderung", show_default=True)
@click.option("--apply", is_flag=True, help="Move eligible files after an explicit confirmation.")
@click.option("--confirm", default="", help="Required value for --apply: AUSSONDERN")
@click.option("--user", "actor", required=True)
@with_appcontext
def retention_cleanup_command(destination: str, apply: bool, confirm: str, actor: str) -> None:
    """Preview cleanup candidates or move them; never delete document files."""
    if apply and confirm != "AUSSONDERN":
        raise click.UsageError("--apply requires --confirm AUSSONDERN")
    result = DocumentStore(current_app.config["DOCUMENT_ROOT"]).cleanup_expired(
        destination, actor, apply=apply
    )
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@click.command("search-documents")
@click.argument("query")
@click.option("--limit", default=50, show_default=True)
@with_appcontext
def search_documents_command(query: str, limit: int) -> None:
    """Search document paths, states, tags, notes and domain attributes."""
    results = DocumentStore(current_app.config["DOCUMENT_ROOT"]).search(query, limit)
    click.echo(json.dumps(results, ensure_ascii=False, indent=2))


def init_app(app: Any) -> None:
    app.cli.add_command(init_document_store_command)
    app.cli.add_command(scan_documents_command)
    app.cli.add_command(document_note_command)
    app.cli.add_command(document_state_command)
    app.cli.add_command(document_link_command)
    app.cli.add_command(document_graph_command)
    app.cli.add_command(document_attribute_command)
    app.cli.add_command(document_deadline_command)
    app.cli.add_command(retention_status_command)
    app.cli.add_command(retention_cleanup_command)
    app.cli.add_command(search_documents_command)
