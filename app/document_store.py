"""File based document storage and repairable scan index.

The filesystem is the source of truth.  SQLite only caches scan results and
can therefore be deleted and rebuilt at any time.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
from flask import current_app
from flask.cli import with_appcontext


CONTROL_DIR = ".simpleoffice-meta"
POLICY_FILE = ".simpleoffice-folder.json"
EVENT_FILE = "events.ndjson"


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


@dataclass(frozen=True)
class ScanReport:
    files: int = 0
    new_files: int = 0
    duplicates: int = 0
    symlinks: int = 0
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

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.documents.mkdir(parents=True, exist_ok=True)
        self.fingerprints.mkdir(parents=True, exist_ok=True)
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
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scan_file_sha256 ON scan_file(sha256);
                """
            )

    def ensure_folder_policy(self, folder: str | Path) -> Path:
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
        atomic_json_write(
            policy,
            {
                "version": 1,
                "folder_id": str(uuid.uuid4()),
                "inherit": True,
                "grants": [],
            },
        )
        self._event("folder_policy_created", {"path": self.relative(folder_path)})
        return policy

    def import_file(self, source: str | Path) -> Path:
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
        self._event("file_imported", {"source": str(source_path), "path": self.relative(target)})
        return target

    def scan(self) -> ScanReport:
        self.initialize()
        files = new_files = duplicates = symlinks = errors = 0
        for current, directories, names in os.walk(self.root, followlinks=False):
            current_path = Path(current)
            directories[:] = [name for name in directories if name != CONTROL_DIR]
            try:
                self.ensure_folder_policy(current_path)
            except ValueError as exc:
                errors += 1
                self._event("folder_policy_invalid", {"path": self.relative(current_path), "error": str(exc)})
                continue
            for name in names:
                if name == POLICY_FILE:
                    continue
                path = current_path / name
                try:
                    if path.is_symlink():
                        symlinks += 1
                        self._event("symlink_seen", {"path": self.relative(path), "target": os.readlink(path)})
                        continue
                    if not path.is_file():
                        continue
                    created, duplicate = self._scan_file(path)
                    files += 1
                    new_files += int(created)
                    duplicates += int(duplicate)
                except (OSError, ValueError) as exc:
                    errors += 1
                    self._event("scan_error", {"path": self.relative(path), "error": str(exc)})
        return ScanReport(files, new_files, duplicates, symlinks, errors)

    def relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root)) if path != self.root else "."

    def _scan_file(self, path: Path) -> tuple[bool, bool]:
        stat = path.stat()
        relative_path = self.relative(path)
        digest = sha256_file(path)
        xattrs = self._read_xattrs(path)
        document_id = xattrs.get("document_id") or str(uuid.uuid4())
        metadata_path = self.documents / f"{document_id}.json"
        created = not metadata_path.exists()
        now = utc_now()
        metadata = self._read_json(metadata_path, {})
        metadata.update(
            {
                "version": 1,
                "document_id": document_id,
                "sha256": digest,
                "first_seen_at": metadata.get("first_seen_at", now),
                "last_seen_at": now,
                "last_path": relative_path,
                "tags": metadata.get("tags", xattrs.get("tags", [])),
            }
        )
        atomic_json_write(metadata_path, metadata)
        self._write_xattrs(path, document_id, digest, metadata["tags"])

        fingerprint_path = self.fingerprints / f"{digest}.json"
        fingerprint = self._read_json(fingerprint_path, {})
        known_paths = set(fingerprint.get("paths", []))
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
        with self._db() as db:
            db.execute(
                """INSERT INTO scan_file(relative_path, document_id, sha256, size, modified_ns, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(relative_path) DO UPDATE SET document_id=excluded.document_id,
                     sha256=excluded.sha256, size=excluded.size, modified_ns=excluded.modified_ns,
                     last_seen_at=excluded.last_seen_at""",
                (relative_path, document_id, digest, stat.st_size, stat.st_mtime_ns, now),
            )
        self._event(
            "file_seen",
            {"path": relative_path, "document_id": document_id, "sha256": digest, "first_seen": created, "duplicate": duplicate},
        )
        return created, duplicate

    def _db(self) -> sqlite3.Connection:
        self.control.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.index_path)

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
@with_appcontext
def scan_documents_command(root: Path | None) -> None:
    """Scan documents, calculate hashes and update the repairable index."""
    store = DocumentStore(root or current_app.config["DOCUMENT_ROOT"])
    report = store.scan()
    click.echo(
        f"files={report.files} new={report.new_files} duplicates={report.duplicates} "
        f"symlinks={report.symlinks} errors={report.errors}"
    )


def init_app(app: Any) -> None:
    app.cli.add_command(init_document_store_command)
    app.cli.add_command(scan_documents_command)
