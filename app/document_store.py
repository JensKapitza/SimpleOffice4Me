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

from .revision_history import RevisionHistory


CONTROL_DIR = ".simpleoffice-meta"
POLICY_FILE = ".simpleoffice-folder.json"
EVENT_FILE = "events.ndjson"
HISTORY_DIR = ".simpleoffice-history"


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
        self.history = RevisionHistory(self.root)

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
                "scan": {
                    "follow_symlinks": False,
                    "allow_other_filesystems": False,
                },
            },
        )
        self._event("folder_policy_created", {"path": self.relative(folder_path)})
        self._record_revision(
            "folder_policy_created",
            "system",
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
        return metadata

    def add_note(self, reference: str | Path, text: str, author: str = "") -> dict[str, Any]:
        """Append an immutable note to a document's file-based metadata."""
        self._require_actor(author)
        text = text.strip()
        if not text:
            raise ValueError("note must not be empty")
        metadata = self.get_document(reference)
        note = {"id": str(uuid.uuid4()), "text": text, "author": author, "created_at": utc_now()}
        metadata.setdefault("notes", []).append(note)
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event("document_note_added", {"document_id": metadata["document_id"], "note_id": note["id"]})
        self._record_revision("document_note_added", author, "documents", metadata["document_id"], metadata)
        return note

    def set_state(self, reference: str | Path, state: str, author: str = "") -> dict[str, Any]:
        """Set a human workflow state and preserve the complete state history."""
        self._require_actor(author)
        state = state.strip()
        if not state:
            raise ValueError("state must not be empty")
        metadata = self.get_document(reference)
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
        metadata.setdefault("attributes", {})[key] = value
        self._save_document(metadata)
        self._refresh_search_index(metadata)
        self._event(
            "document_attribute_set",
            {"document_id": metadata["document_id"], "key": key, "author": author},
        )
        self._record_revision("document_attribute_set", author, "documents", metadata["document_id"], metadata)

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search indexed metadata and later OCR content without scanning files again."""
        query = query.strip()
        if not query:
            return []
        self.initialize()
        with self._db() as db:
            try:
                rows = db.execute(
                    "SELECT document_id, path, state FROM document_search WHERE document_search MATCH ? LIMIT ?",
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                pattern = f"%{query}%"
                rows = db.execute(
                    """SELECT document_id, path, state FROM document_search
                    WHERE path LIKE ? OR state LIKE ? OR tags LIKE ? OR notes LIKE ?
                       OR attributes LIKE ? OR content LIKE ? LIMIT ?""",
                    (pattern, pattern, pattern, pattern, pattern, pattern, limit),
                ).fetchall()
        return [{"document_id": row[0], "path": row[1], "state": row[2]} for row in rows]

    def add_link(
        self,
        source: str | Path,
        target: str | Path,
        relation_type: str = "related",
        label: str = "",
        author: str = "",
    ) -> dict[str, Any]:
        """Create a directed, labelled document relationship for graph views."""
        self._require_actor(author)
        source_metadata = self.get_document(source)
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
            if needle not in haystack:
                continue
            score = 100 if needle in (metadata.get("document_id", "").casefold(), path.casefold()) else 10
            if Path(path).name.casefold() == needle:
                score = 90
            elif needle in Path(path).name.casefold():
                score = 60
            matches.append({"document_id": metadata["document_id"], "path": path, "state": metadata.get("state"), "version": metadata.get("version_number", 1), "score": score})
        return sorted(matches, key=lambda item: (-item["score"], item["path"]))[:limit]

    def graph(self, reference: str | Path) -> dict[str, Any]:
        """Return one document, its versions and all inbound/outbound graph edges."""
        document = self.get_document(reference)
        document_id = document["document_id"]
        documents = self._all_documents()
        visible_ids = {document_id}
        edges: list[dict[str, Any]] = []
        for item in documents:
            for link in item.get("relationships", []):
                if item.get("document_id") == document_id or link.get("target_document_id") == document_id:
                    visible_ids.add(item["document_id"])
                    visible_ids.add(link["target_document_id"])
                    edges.append({"source": item["document_id"], "target": link["target_document_id"], **link})
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
        return {"focus_document_id": document_id, "nodes": nodes, "edges": edges}

    def scan(self) -> ScanReport:
        self.initialize()
        files = new_files = duplicates = symlinks = skipped_boundaries = errors = 0
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
                if path.name in (POLICY_FILE, CONTROL_DIR, HISTORY_DIR):
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
                            created, duplicate = self._scan_file(target)
                            files += 1
                            new_files += int(created)
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
                    created, duplicate = self._scan_file(path)
                    files += 1
                    new_files += int(created)
                    duplicates += int(duplicate)
                except (OSError, ValueError) as exc:
                    errors += 1
                    self._event("scan_error", {"path": self.relative(path), "error": str(exc)})
        return ScanReport(files, new_files, duplicates, symlinks, skipped_boundaries, errors)

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
        original_sha256 = metadata.get("original_sha256", digest)
        integrity_changed = original_sha256 != digest
        metadata.update(
            {
                "version": 1,
                "document_id": document_id,
                "sha256": digest,
                "first_seen_at": metadata.get("first_seen_at", now),
                "last_seen_at": now,
                "last_path": relative_path,
                "tags": metadata.get("tags", xattrs.get("tags", [])),
                "original_sha256": original_sha256,
                "content_sha256": digest,
                "notes": metadata.get("notes", []),
                "relationships": metadata.get("relationships", []),
                "state": metadata.get("state", "new"),
                "state_history": metadata.get("state_history", []),
                "version_series_id": metadata.get("version_series_id", document_id),
                "version_number": metadata.get("version_number", 1),
                "attributes": metadata.get("attributes", {}),
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
        metadata["system_state"] = "integrity_changed" if integrity_changed else ("duplicate" if duplicate else "indexed")
        self._save_document(metadata)
        if integrity_changed:
            self._event(
                "integrity_changed",
                {"document_id": document_id, "expected_sha256": original_sha256, "observed_sha256": digest},
            )
        self._refresh_search_index(metadata)
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

    def _save_document(self, metadata: dict[str, Any]) -> None:
        atomic_json_write(self.documents / f"{metadata['document_id']}.json", metadata)
        relative_path = str(metadata.get("last_path", ""))
        if relative_path and not relative_path.startswith("[external]"):
            document_path = self.root / relative_path
            if document_path.is_file() and not document_path.is_symlink():
                atomic_json_write(document_path.parent / CONTROL_DIR / f"{metadata['document_id']}.json", metadata)
                self._write_context_xattrs(document_path, metadata)

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

    def _refresh_search_index(self, metadata: dict[str, Any]) -> None:
        row = (
            metadata["document_id"],
            metadata.get("last_path", ""),
            metadata.get("state", ""),
            " ".join(metadata.get("tags", [])),
            "\n".join(note.get("text", "") for note in metadata.get("notes", [])),
            json.dumps(metadata.get("attributes", {}), ensure_ascii=False),
            metadata.get("ocr_text", ""),
        )
        with self._db() as db:
            db.execute("DELETE FROM document_search WHERE document_id = ?", (metadata["document_id"],))
            db.execute(
                "INSERT INTO document_search(document_id, path, state, tags, notes, attributes, content) VALUES (?, ?, ?, ?, ?, ?, ?)",
                row,
            )

    def _all_documents(self) -> list[dict[str, Any]]:
        self.initialize()
        return [
            metadata
            for path in self.documents.glob("*.json")
            if (metadata := self._read_json(path, {})).get("document_id")
        ]

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
    app.cli.add_command(search_documents_command)
