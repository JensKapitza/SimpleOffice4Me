"""Persistent V2 logical object catalog separated from physical blob storage."""
from __future__ import annotations

import os
import sqlite3
from ..sqlite_utils import connect as sqlite_connect
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator

from .contracts import ErrorCode, LogicalObjectId, OperationResult, StorageLocation


FORMAT_FAMILY = "simpleoffice-v2-object-catalog"
SCHEMA_VERSION = 1


class CatalogState(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class CatalogEntry:
    object_id: LogicalObjectId
    location: StorageLocation
    version_id: str
    size: int
    content_sha256: str
    state: CatalogState
    created_at: int
    updated_at: int
    deleted_at: int = 0

    def __post_init__(self) -> None:
        version = str(self.version_id or "").strip()
        digest = str(self.content_sha256 or "").strip().casefold()
        if not version or len(version) > 200:
            raise ValueError("invalid catalog blob version")
        if isinstance(self.size, bool) or int(self.size) < 0:
            raise ValueError("invalid catalog object size")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid catalog content sha256")
        if self.state is CatalogState.DELETED and int(self.deleted_at) <= 0:
            raise ValueError("deleted catalog entries require a deletion timestamp")
        if self.state is not CatalogState.DELETED and int(self.deleted_at) != 0:
            raise ValueError("non-deleted catalog entries cannot carry a deletion timestamp")
        object.__setattr__(self, "version_id", version)
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "size", int(self.size))


class ObjectCatalog:
    """Transactional namespace/catalog for V2 logical objects.

    The catalog owns logical location and current-version references. It never
    stores blob bytes and never derives object identity from a path.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / ".simpleoffice-v2"
        self.path = self.control / "catalog.sqlite3"
        self.control.mkdir(parents=True, exist_ok=True)
        self.initialize()
        if os.name == "posix":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite_connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS object_catalog(
                    object_id TEXT PRIMARY KEY,
                    location TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    deleted_at INTEGER NOT NULL DEFAULT 0,
                    CHECK(size >= 0),
                    CHECK(state IN ('active','deleted','recovery'))
                );
                CREATE UNIQUE INDEX IF NOT EXISTS catalog_live_location
                    ON object_catalog(location) WHERE state <> 'deleted';
                CREATE INDEX IF NOT EXISTS catalog_state_updated
                    ON object_catalog(state, updated_at);
                """
            )
            family = db.execute(
                "SELECT value FROM catalog_meta WHERE key='format_family'"
            ).fetchone()
            if family is not None and str(family["value"]) != FORMAT_FAMILY:
                raise RuntimeError("unsupported V2 object catalog format family")
            row = db.execute(
                "SELECT value FROM catalog_meta WHERE key='schema_version'"
            ).fetchone()
            if row is not None and str(row["value"]) != str(SCHEMA_VERSION):
                raise RuntimeError("unsupported V2 object catalog schema version")
            db.execute(
                "INSERT OR IGNORE INTO catalog_meta(key,value) VALUES('format_family',?)",
                (FORMAT_FAMILY,),
            )
            db.execute(
                "INSERT OR IGNORE INTO catalog_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _logical(value: LogicalObjectId | str) -> LogicalObjectId:
        return value if isinstance(value, LogicalObjectId) else LogicalObjectId(str(value))

    @staticmethod
    def _location(value: StorageLocation | str) -> StorageLocation:
        return value if isinstance(value, StorageLocation) else StorageLocation(str(value))

    @staticmethod
    def _content(version_id: str, size: int, content_sha256: str) -> tuple[str, int, str]:
        version = str(version_id or "").strip()
        digest = str(content_sha256 or "").strip().casefold()
        if not version or len(version) > 200:
            raise ValueError("invalid catalog blob version")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("invalid catalog object size")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid catalog content sha256")
        return version, size, digest

    @staticmethod
    def _entry(row: sqlite3.Row) -> CatalogEntry:
        return CatalogEntry(
            object_id=LogicalObjectId(str(row["object_id"])),
            location=StorageLocation(str(row["location"])),
            version_id=str(row["version_id"]),
            size=int(row["size"]),
            content_sha256=str(row["content_sha256"]),
            state=CatalogState(str(row["state"])),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            deleted_at=int(row["deleted_at"]),
        )

    @staticmethod
    def _conflict(message: str) -> OperationResult[CatalogEntry]:
        return OperationResult.failure(ErrorCode.CONFLICT, message)

    def register(
        self,
        object_id: LogicalObjectId | str,
        location: StorageLocation | str,
        *,
        version_id: str,
        size: int,
        content_sha256: str,
    ) -> OperationResult[CatalogEntry]:
        logical = self._logical(object_id)
        target = self._location(location)
        version, length, digest = self._content(version_id, size, content_sha256)
        now = int(time.time())
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT * FROM object_catalog WHERE object_id=?",
                    (logical.value,),
                ).fetchone()
                if existing is not None:
                    entry = self._entry(existing)
                    if (
                        entry.state is CatalogState.ACTIVE
                        and entry.location == target
                        and entry.version_id == version
                        and entry.size == length
                        and entry.content_sha256 == digest
                    ):
                        return OperationResult.success(entry)
                    return self._conflict("logical object already exists with different catalog state")
                db.execute(
                    """INSERT INTO object_catalog(
                        object_id,location,version_id,size,content_sha256,state,
                        created_at,updated_at,deleted_at
                    ) VALUES(?,?,?,?,?,'active',?,?,0)""",
                    (logical.value, target.relative_path, version, length, digest, now, now),
                )
        except sqlite3.IntegrityError:
            return self._conflict("storage location is already in use")
        return self.get(logical)

    def get(
        self,
        object_id: LogicalObjectId | str,
        *,
        include_deleted: bool = False,
    ) -> OperationResult[CatalogEntry]:
        logical = self._logical(object_id)
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM object_catalog WHERE object_id=?",
                (logical.value,),
            ).fetchone()
        if row is None or (row["state"] == CatalogState.DELETED.value and not include_deleted):
            return OperationResult.failure(ErrorCode.NOT_FOUND, "catalog object not found")
        return OperationResult.success(self._entry(row))

    def get_by_location(
        self,
        location: StorageLocation | str,
    ) -> OperationResult[CatalogEntry]:
        target = self._location(location)
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM object_catalog WHERE location=? AND state <> 'deleted'",
                (target.relative_path,),
            ).fetchone()
        if row is None:
            return OperationResult.failure(ErrorCode.NOT_FOUND, "catalog location not found")
        return OperationResult.success(self._entry(row))

    def list(self, *, include_deleted: bool = False) -> list[CatalogEntry]:
        query = (
            "SELECT * FROM object_catalog ORDER BY location, object_id"
            if include_deleted
            else "SELECT * FROM object_catalog WHERE state <> 'deleted' ORDER BY location, object_id"
        )
        with self._db() as db:
            rows = db.execute(query).fetchall()
        return [self._entry(row) for row in rows]

    def move(
        self,
        object_id: LogicalObjectId | str,
        destination: StorageLocation | str,
        *,
        expected_version_id: str | None = None,
    ) -> OperationResult[CatalogEntry]:
        logical = self._logical(object_id)
        target = self._location(destination)
        now = int(time.time())
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM object_catalog WHERE object_id=?",
                    (logical.value,),
                ).fetchone()
                if row is None or row["state"] == CatalogState.DELETED.value:
                    return OperationResult.failure(ErrorCode.NOT_FOUND, "catalog object not found")
                if row["state"] != CatalogState.ACTIVE.value:
                    return self._conflict("catalog object requires recovery before move")
                if expected_version_id is not None and str(row["version_id"]) != str(expected_version_id):
                    return self._conflict("catalog object version changed")
                db.execute(
                    "UPDATE object_catalog SET location=?,updated_at=? WHERE object_id=?",
                    (target.relative_path, now, logical.value),
                )
        except sqlite3.IntegrityError:
            return self._conflict("storage location is already in use")
        return self.get(logical)

    def update_content(
        self,
        object_id: LogicalObjectId | str,
        *,
        version_id: str,
        size: int,
        content_sha256: str,
        expected_version_id: str | None = None,
    ) -> OperationResult[CatalogEntry]:
        logical = self._logical(object_id)
        version, length, digest = self._content(version_id, size, content_sha256)
        now = int(time.time())
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM object_catalog WHERE object_id=?",
                (logical.value,),
            ).fetchone()
            if row is None or row["state"] == CatalogState.DELETED.value:
                return OperationResult.failure(ErrorCode.NOT_FOUND, "catalog object not found")
            if row["state"] != CatalogState.ACTIVE.value:
                return self._conflict("catalog object requires recovery before content update")
            if expected_version_id is not None and str(row["version_id"]) != str(expected_version_id):
                return self._conflict("catalog object version changed")
            db.execute(
                """UPDATE object_catalog
                   SET version_id=?,size=?,content_sha256=?,updated_at=?
                   WHERE object_id=?""",
                (version, length, digest, now, logical.value),
            )
        return self.get(logical)

    def reconcile_external(
        self,
        object_id: LogicalObjectId | str,
        location: StorageLocation | str,
        *,
        version_id: str,
        size: int,
        content_sha256: str,
        expected_version_id: str | None = None,
    ) -> OperationResult[CatalogEntry]:
        """Atomically adopt one verified out-of-band filesystem observation.

        This is intentionally narrower than normal application mutations. It is
        used by the filesystem watcher after DocumentStore has identified the
        stable logical object ID. Existing recovery-needed objects are never
        silently reactivated.
        """
        logical = self._logical(object_id)
        target = self._location(location)
        version, length, digest = self._content(version_id, size, content_sha256)
        now = int(time.time())
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM object_catalog WHERE object_id=?",
                    (logical.value,),
                ).fetchone()
                if row is None:
                    db.execute(
                        """INSERT INTO object_catalog(
                            object_id,location,version_id,size,content_sha256,state,
                            created_at,updated_at,deleted_at
                        ) VALUES(?,?,?,?,?,'active',?,?,0)""",
                        (
                            logical.value,
                            target.relative_path,
                            version,
                            length,
                            digest,
                            now,
                            now,
                        ),
                    )
                else:
                    entry = self._entry(row)
                    if entry.state is CatalogState.RECOVERY:
                        return self._conflict(
                            "catalog object requires recovery before external reconciliation"
                        )
                    if (
                        expected_version_id is not None
                        and entry.version_id != str(expected_version_id)
                    ):
                        return self._conflict(
                            "catalog object version changed during external reconciliation"
                        )
                    db.execute(
                        """UPDATE object_catalog
                           SET location=?,version_id=?,size=?,content_sha256=?,
                               state='active',deleted_at=0,updated_at=?
                           WHERE object_id=?""",
                        (
                            target.relative_path,
                            version,
                            length,
                            digest,
                            now,
                            logical.value,
                        ),
                    )
        except sqlite3.IntegrityError:
            return self._conflict("storage location is already in use")
        return self.get(logical)

    def mark_deleted(
        self,
        object_id: LogicalObjectId | str,
        *,
        expected_version_id: str | None = None,
    ) -> OperationResult[CatalogEntry]:
        logical = self._logical(object_id)
        now = int(time.time())
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM object_catalog WHERE object_id=?",
                (logical.value,),
            ).fetchone()
            if row is None:
                return OperationResult.failure(ErrorCode.NOT_FOUND, "catalog object not found")
            if row["state"] == CatalogState.DELETED.value:
                return OperationResult.success(self._entry(row))
            if expected_version_id is not None and str(row["version_id"]) != str(expected_version_id):
                return self._conflict("catalog object version changed")
            db.execute(
                """UPDATE object_catalog
                   SET state='deleted',deleted_at=?,updated_at=?
                   WHERE object_id=?""",
                (now, now, logical.value),
            )
        return self.get(logical, include_deleted=True)

    def mark_recovery(
        self,
        object_id: LogicalObjectId | str,
    ) -> OperationResult[CatalogEntry]:
        logical = self._logical(object_id)
        now = int(time.time())
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM object_catalog WHERE object_id=?",
                (logical.value,),
            ).fetchone()
            if row is None or row["state"] == CatalogState.DELETED.value:
                return OperationResult.failure(ErrorCode.NOT_FOUND, "catalog object not found")
            db.execute(
                "UPDATE object_catalog SET state='recovery',updated_at=? WHERE object_id=?",
                (now, logical.value),
            )
        return self.get(logical)

    def mark_active(
        self,
        object_id: LogicalObjectId | str,
    ) -> OperationResult[CatalogEntry]:
        logical = self._logical(object_id)
        now = int(time.time())
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM object_catalog WHERE object_id=?",
                (logical.value,),
            ).fetchone()
            if row is None or row["state"] == CatalogState.DELETED.value:
                return OperationResult.failure(ErrorCode.NOT_FOUND, "catalog object not found")
            db.execute(
                "UPDATE object_catalog SET state='active',updated_at=? WHERE object_id=?",
                (now, logical.value),
            )
        return self.get(logical)

    def restore(
        self,
        object_id: LogicalObjectId | str,
        *,
        location: StorageLocation | str | None = None,
    ) -> OperationResult[CatalogEntry]:
        logical = self._logical(object_id)
        now = int(time.time())
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM object_catalog WHERE object_id=?",
                    (logical.value,),
                ).fetchone()
                if row is None:
                    return OperationResult.failure(ErrorCode.NOT_FOUND, "catalog object not found")
                if row["state"] != CatalogState.DELETED.value:
                    return self._conflict("catalog object is not deleted")
                target = self._location(location or str(row["location"]))
                db.execute(
                    """UPDATE object_catalog
                       SET location=?,state='active',deleted_at=0,updated_at=?
                       WHERE object_id=?""",
                    (target.relative_path, now, logical.value),
                )
        except sqlite3.IntegrityError:
            return self._conflict("storage location is already in use")
        return self.get(logical)
