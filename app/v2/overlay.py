"""Crash-aware V2 overlay import journal built on the StoragePort boundary."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .contracts import ErrorCode, LogicalObjectId, StorageLocation, StoragePort, StoredObject


class OverlayImportState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMMITTED = "committed"
    DAMAGED = "damaged"
    RECOVERY_NEEDED = "recovery-needed"


@dataclass(frozen=True)
class OverlayImportRecord:
    import_id: str
    target: StorageLocation
    state: OverlayImportState
    size: int
    content_sha256: str
    object_id: LogicalObjectId | None = None
    version: str = ""
    last_error: str = ""
    created_at: int = 0
    updated_at: int = 0


class OverlayImportJournal:
    """Stage normal file writes before committing them through StoragePort.

    The journal deliberately treats an interrupted PROCESSING state as
    RECOVERY_NEEDED. It never assumes whether an external storage commit did or
    did not complete.
    """

    FORMAT_VERSION = 1

    def __init__(self, root: str | Path, storage: StoragePort, *, max_bytes: int = 512 * 1024 * 1024):
        self.root = Path(root).expanduser().resolve()
        self.storage = storage
        self.max_bytes = int(max_bytes)
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.control = self.root / ".simpleoffice-v2" / "overlay"
        self.staging = self.control / "staging"
        self.db_path = self.control / "journal.sqlite3"
        self.staging.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS overlay_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS overlay_import(
                    import_id TEXT PRIMARY KEY,
                    target TEXT NOT NULL,
                    state TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    object_id TEXT NOT NULL DEFAULT '',
                    version TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS overlay_import_state
                    ON overlay_import(state, updated_at);
                """
            )
            row = db.execute(
                "SELECT value FROM overlay_meta WHERE key='format_version'"
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO overlay_meta(key,value) VALUES('format_version',?)",
                    (str(self.FORMAT_VERSION),),
                )
            elif int(row["value"]) != self.FORMAT_VERSION:
                raise RuntimeError("unsupported overlay journal format")

    def _staging_path(self, import_id: str) -> Path:
        return self.staging / f"{import_id}.bin"

    @staticmethod
    def _digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _row(row: sqlite3.Row) -> OverlayImportRecord:
        raw_object_id = str(row["object_id"] or "")
        return OverlayImportRecord(
            import_id=str(row["import_id"]),
            target=StorageLocation(str(row["target"])),
            state=OverlayImportState(str(row["state"])),
            size=int(row["size"]),
            content_sha256=str(row["content_sha256"]),
            object_id=LogicalObjectId(raw_object_id) if raw_object_id else None,
            version=str(row["version"] or ""),
            last_error=str(row["last_error"] or ""),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    def get(self, import_id: str) -> OverlayImportRecord:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM overlay_import WHERE import_id=?",
                (str(import_id),),
            ).fetchone()
        if row is None:
            raise KeyError("unknown overlay import")
        return self._row(row)

    def list(self, state: OverlayImportState | None = None) -> tuple[OverlayImportRecord, ...]:
        with self._db() as db:
            if state is None:
                rows = db.execute(
                    "SELECT * FROM overlay_import ORDER BY created_at, import_id"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM overlay_import WHERE state=? ORDER BY created_at, import_id",
                    (state.value,),
                ).fetchall()
        return tuple(self._row(row) for row in rows)

    def stage_bytes(self, target: StorageLocation, content: bytes) -> OverlayImportRecord:
        payload = bytes(content)
        if len(payload) > self.max_bytes:
            raise ValueError("overlay import exceeds configured size limit")
        import_id = str(uuid.uuid4())
        final_path = self._staging_path(import_id)
        temporary = final_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, final_path)
        finally:
            temporary.unlink(missing_ok=True)
        now = int(time.time())
        try:
            with self._db() as db:
                db.execute(
                    """INSERT INTO overlay_import(
                        import_id,target,state,size,content_sha256,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        import_id,
                        target.relative_path,
                        OverlayImportState.PENDING.value,
                        len(payload),
                        self._digest(payload),
                        now,
                        now,
                    ),
                )
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
        return self.get(import_id)

    def _set_state(
        self,
        import_id: str,
        state: OverlayImportState,
        *,
        last_error: str = "",
        stored: StoredObject | None = None,
    ) -> OverlayImportRecord:
        now = int(time.time())
        with self._db() as db:
            changed = db.execute(
                """UPDATE overlay_import
                   SET state=?, object_id=?, version=?, last_error=?, updated_at=?
                   WHERE import_id=?""",
                (
                    state.value,
                    stored.object_id.value if stored else "",
                    stored.version if stored else "",
                    str(last_error or ""),
                    now,
                    str(import_id),
                ),
            ).rowcount
        if not changed:
            raise KeyError("unknown overlay import")
        return self.get(import_id)

    def _verified_staging_bytes(self, record: OverlayImportRecord) -> bytes:
        path = self._staging_path(record.import_id)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise RuntimeError("staged overlay content is unavailable") from exc
        if len(content) != record.size or self._digest(content) != record.content_sha256:
            raise RuntimeError("staged overlay content failed integrity verification")
        return content

    def process(self, import_id: str) -> OverlayImportRecord:
        record = self.get(import_id)
        if record.state is not OverlayImportState.PENDING:
            raise ValueError("only pending overlay imports can be processed")
        self._set_state(import_id, OverlayImportState.PROCESSING)
        try:
            content = self._verified_staging_bytes(record)
        except RuntimeError as exc:
            return self._set_state(
                import_id,
                OverlayImportState.DAMAGED,
                last_error=str(exc),
            )

        try:
            result = self.storage.create_bytes(record.target, content)
        except Exception as exc:
            return self._set_state(
                import_id,
                OverlayImportState.RECOVERY_NEEDED,
                last_error=f"storage outcome unknown: {type(exc).__name__}",
            )

        if result.ok:
            committed = self._set_state(
                import_id,
                OverlayImportState.COMMITTED,
                stored=result.value,
            )
            self._staging_path(import_id).unlink(missing_ok=True)
            return committed

        error = result.error
        if error.retryable or error.code in {ErrorCode.STORAGE_UNAVAILABLE, ErrorCode.RETRYABLE}:
            return self._set_state(
                import_id,
                OverlayImportState.PENDING,
                last_error=error.message,
            )
        return self._set_state(
            import_id,
            OverlayImportState.RECOVERY_NEEDED,
            last_error=error.message,
        )

    def recover_incomplete(self) -> tuple[OverlayImportRecord, ...]:
        """Mark interrupted processing as ambiguous instead of guessing."""
        now = int(time.time())
        with self._db() as db:
            rows = db.execute(
                "SELECT import_id FROM overlay_import WHERE state=?",
                (OverlayImportState.PROCESSING.value,),
            ).fetchall()
            for row in rows:
                db.execute(
                    """UPDATE overlay_import
                       SET state=?, last_error=?, updated_at=?
                       WHERE import_id=?""",
                    (
                        OverlayImportState.RECOVERY_NEEDED.value,
                        "processing was interrupted; storage outcome must be reconciled",
                        now,
                        str(row["import_id"]),
                    ),
                )
        return tuple(self.get(str(row["import_id"])) for row in rows)

    def retry_after_reconciliation(
        self,
        import_id: str,
        *,
        confirmed_not_committed: bool,
    ) -> OverlayImportRecord:
        record = self.get(import_id)
        if record.state is not OverlayImportState.RECOVERY_NEEDED:
            raise ValueError("overlay import is not waiting for recovery")
        if not confirmed_not_committed:
            raise ValueError("retry requires explicit confirmation that no storage commit exists")
        self._verified_staging_bytes(record)
        return self._set_state(import_id, OverlayImportState.PENDING)

    def acknowledge_committed(self, import_id: str, stored: StoredObject) -> OverlayImportRecord:
        record = self.get(import_id)
        if record.state is not OverlayImportState.RECOVERY_NEEDED:
            raise ValueError("overlay import is not waiting for recovery")
        if stored.location != record.target:
            raise ValueError("reconciled object location does not match overlay target")
        if stored.size != record.size:
            raise ValueError("reconciled object size does not match staged content")
        committed = self._set_state(
            import_id,
            OverlayImportState.COMMITTED,
            stored=stored,
        )
        self._staging_path(import_id).unlink(missing_ok=True)
        return committed

    def rollback_staged(self, import_id: str) -> None:
        record = self.get(import_id)
        if record.state not in {OverlayImportState.PENDING, OverlayImportState.DAMAGED}:
            raise ValueError("only safely uncommitted overlay imports can be rolled back")
        self._staging_path(import_id).unlink(missing_ok=True)
        with self._db() as db:
            db.execute(
                "DELETE FROM overlay_import WHERE import_id=?",
                (str(import_id),),
            )
