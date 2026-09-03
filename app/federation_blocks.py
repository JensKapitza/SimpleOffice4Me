"""Content-defined SHA-512 blocks for cross-file SOFP deduplication.

The transport keeps its normal resumable chunks, but this layer identifies reusable
content independently of file offsets.  A rolling buzhash over a fixed window makes
block boundaries stable again shortly after insertions/deletions, so an updated file
can reuse blocks from older revisions or completely different local files.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .document_store import CONTROL_DIR, DocumentStore


SCHEMA = "sofp-content-blocks/v1"
HASH_ALGORITHM = "sha512"
DEFAULT_MIN_BLOCK = 256 * 1024
DEFAULT_AVG_BLOCK = 1024 * 1024
DEFAULT_MAX_BLOCK = 4 * 1024 * 1024
ROLLING_WINDOW = 64
_MASK64 = (1 << 64) - 1
_GEAR = tuple(
    int.from_bytes(hashlib.sha256(f"SOFP-BUZHASH-{index}".encode()).digest()[:8], "big")
    for index in range(256)
)


def normalize_sha512(value: str) -> str:
    value = str(value or "").removeprefix("sha512:").strip().casefold()
    if len(value) != 128 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("invalid sha512")
    return value


def sha512_bytes(data: bytes) -> str:
    return hashlib.sha512(data).hexdigest()


def _rotl(value: int, amount: int) -> int:
    amount %= 64
    if not amount:
        return value & _MASK64
    return ((value << amount) | (value >> (64 - amount))) & _MASK64


def _sizes(min_size: int, avg_size: int, max_size: int) -> tuple[int, int, int]:
    minimum, average, maximum = int(min_size), int(avg_size), int(max_size)
    if minimum < ROLLING_WINDOW or minimum > average or average > maximum:
        raise ValueError("invalid content block sizes")
    if maximum > 64 * 1024 * 1024:
        raise ValueError("content block maximum too large")
    return minimum, average, maximum


def _boundary_mask(avg_size: int) -> int:
    bits = max(8, round(int(avg_size).bit_length() - 1))
    return (1 << bits) - 1


def content_defined_ranges(
    path: Path,
    *,
    min_size: int = DEFAULT_MIN_BLOCK,
    avg_size: int = DEFAULT_AVG_BLOCK,
    max_size: int = DEFAULT_MAX_BLOCK,
) -> Iterator[tuple[int, int]]:
    """Yield stable content-defined `(offset, length)` ranges.

    The rolling fingerprint is *not* reset on chunk boundaries.  Once the rolling
    window has moved past an insertion, the same source content has the same
    fingerprint again and therefore naturally re-synchronizes block boundaries.
    """
    minimum, average, maximum = _sizes(min_size, avg_size, max_size)
    mask = _boundary_mask(average)
    window: deque[int] = deque(maxlen=ROLLING_WINDOW)
    fingerprint = 0
    block_start = 0
    position = 0
    with Path(path).open("rb") as source:
        while True:
            data = source.read(1024 * 1024)
            if not data:
                break
            for byte in data:
                old = window[0] if len(window) == ROLLING_WINDOW else None
                fingerprint = _rotl(fingerprint, 1) ^ _GEAR[byte]
                if old is not None:
                    fingerprint ^= _rotl(_GEAR[old], ROLLING_WINDOW)
                window.append(byte)
                position += 1
                length = position - block_start
                if length < minimum:
                    continue
                natural_boundary = len(window) == ROLLING_WINDOW and (fingerprint & mask) == 0
                if natural_boundary or length >= maximum:
                    yield block_start, length
                    block_start = position
    if position > block_start:
        yield block_start, position - block_start


def build_content_manifest(
    path: Path,
    *,
    min_size: int = DEFAULT_MIN_BLOCK,
    avg_size: int = DEFAULT_AVG_BLOCK,
    max_size: int = DEFAULT_MAX_BLOCK,
) -> dict[str, Any]:
    path = Path(path)
    minimum, average, maximum = _sizes(min_size, avg_size, max_size)
    blocks: list[dict[str, Any]] = []
    whole = hashlib.sha512()
    with path.open("rb") as source:
        for index, (offset, length) in enumerate(content_defined_ranges(
            path, min_size=minimum, avg_size=average, max_size=maximum,
        )):
            source.seek(offset)
            data = source.read(length)
            if len(data) != length:
                raise OSError("file changed while block manifest was generated")
            digest = sha512_bytes(data)
            whole.update(data)
            blocks.append({"index": index, "offset": offset, "length": length, "sha512": digest})
    return {
        "schema": SCHEMA,
        "hash_algorithm": HASH_ALGORITHM,
        "file_sha512": whole.hexdigest(),
        "size": path.stat().st_size,
        "min_block_size": minimum,
        "avg_block_size": average,
        "max_block_size": maximum,
        "rolling_window": ROLLING_WINDOW,
        "block_count": len(blocks),
        "blocks": blocks,
    }


def content_manifest_valid(manifest: dict[str, Any]) -> bool:
    try:
        if manifest.get("schema") != SCHEMA or manifest.get("hash_algorithm") != HASH_ALGORITHM:
            return False
        normalize_sha512(manifest["file_sha512"])
        size = int(manifest["size"])
        blocks = manifest["blocks"]
        if not isinstance(blocks, list) or int(manifest["block_count"]) != len(blocks):
            return False
        expected_offset = 0
        for index, block in enumerate(blocks):
            length = int(block["length"])
            if int(block["index"]) != index or int(block["offset"]) != expected_offset or length <= 0:
                return False
            normalize_sha512(block["sha512"])
            expected_offset += length
        return expected_offset == size
    except (KeyError, TypeError, ValueError):
        return False


def missing_block_hashes(manifest: dict[str, Any], available: set[str]) -> list[str]:
    normalized = {normalize_sha512(value) for value in available}
    result: list[str] = []
    seen: set[str] = set()
    for block in manifest.get("blocks", []):
        digest = normalize_sha512(block["sha512"])
        if digest not in normalized and digest not in seen:
            result.append(digest)
            seen.add(digest)
    return result


def reusable_bytes(manifest: dict[str, Any], available: set[str]) -> int:
    normalized = {normalize_sha512(value) for value in available}
    return sum(
        int(block["length"])
        for block in manifest.get("blocks", [])
        if normalize_sha512(block["sha512"]) in normalized
    )


class FederationBlockStore:
    """Map SHA-512 blocks to existing local files and a small block cache."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "federation-blocks.sqlite3"
        self.cache = self.control / "federation-block-cache"
        self.initialize()

    @contextmanager
    def _db(self):
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS block_source(
                    sha512 TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    offset INTEGER NOT NULL,
                    length INTEGER NOT NULL,
                    file_size INTEGER NOT NULL,
                    file_mtime_ns INTEGER NOT NULL,
                    indexed_at INTEGER NOT NULL,
                    PRIMARY KEY(sha512,relative_path,offset)
                );
                CREATE INDEX IF NOT EXISTS block_source_path ON block_source(relative_path);
                CREATE TABLE IF NOT EXISTS file_manifest(
                    relative_path TEXT PRIMARY KEY,
                    file_size INTEGER NOT NULL,
                    file_mtime_ns INTEGER NOT NULL,
                    file_sha512 TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    indexed_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS block_event(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    sha512 TEXT NOT NULL DEFAULT '',
                    relative_path TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                );
                """
            )

    def _relative(self, path: Path) -> str:
        resolved = Path(path).resolve()
        if self.root not in (resolved, *resolved.parents):
            raise ValueError("block source outside document root")
        return resolved.relative_to(self.root).as_posix()

    def needs_index(self, path: Path) -> bool:
        path = Path(path).resolve()
        relative = self._relative(path)
        stat = path.stat()
        with self._db() as db:
            row = db.execute("SELECT file_size,file_mtime_ns FROM file_manifest WHERE relative_path=?", (relative,)).fetchone()
        return row is None or int(row["file_size"]) != stat.st_size or int(row["file_mtime_ns"]) != stat.st_mtime_ns

    def manifest_for_file(self, path: Path, *, force: bool = False) -> dict[str, Any]:
        path = Path(path).resolve()
        relative = self._relative(path)
        stat = path.stat()
        if not force:
            with self._db() as db:
                row = db.execute("SELECT * FROM file_manifest WHERE relative_path=?", (relative,)).fetchone()
            if row and int(row["file_size"]) == stat.st_size and int(row["file_mtime_ns"]) == stat.st_mtime_ns:
                try:
                    manifest = json.loads(row["manifest_json"])
                    if content_manifest_valid(manifest):
                        return manifest
                except json.JSONDecodeError:
                    pass
        manifest = build_content_manifest(path)
        self.register_manifest(path, manifest)
        return manifest

    def register_manifest(self, path: Path, manifest: dict[str, Any]) -> None:
        if not content_manifest_valid(manifest):
            raise ValueError("invalid content block manifest")
        path = Path(path).resolve()
        relative = self._relative(path)
        stat = path.stat()
        indexed_at = int(time.time())
        with self._db() as db:
            db.execute("DELETE FROM block_source WHERE relative_path=?", (relative,))
            db.executemany(
                "INSERT OR REPLACE INTO block_source(sha512,relative_path,offset,length,file_size,file_mtime_ns,indexed_at) VALUES(?,?,?,?,?,?,?)",
                [
                    (
                        normalize_sha512(block["sha512"]), relative, int(block["offset"]), int(block["length"]),
                        stat.st_size, stat.st_mtime_ns, indexed_at,
                    )
                    for block in manifest["blocks"]
                ],
            )
            db.execute(
                "INSERT OR REPLACE INTO file_manifest(relative_path,file_size,file_mtime_ns,file_sha512,manifest_json,indexed_at) VALUES(?,?,?,?,?,?)",
                (relative, stat.st_size, stat.st_mtime_ns, manifest["file_sha512"], json.dumps(manifest, separators=(",", ":")), indexed_at),
            )
        self.record_event("file_indexed", relative_path=relative, detail={"blocks": len(manifest["blocks"]), "size": stat.st_size})

    def index_documents(self, *, limit: int | None = None) -> dict[str, int]:
        documents = DocumentStore(self.root)
        indexed = unchanged = skipped = errors = 0
        for document in documents.list_documents():
            path = (self.root / str(document.get("last_path", ""))).resolve()
            if self.root not in (path, *path.parents) or not path.is_file() or path.is_symlink():
                skipped += 1
                continue
            try:
                if not self.needs_index(path):
                    unchanged += 1
                    continue
                if limit is not None and indexed >= max(0, int(limit)):
                    break
                self.manifest_for_file(path, force=True)
                indexed += 1
            except (OSError, ValueError):
                errors += 1
        return {"indexed": indexed, "unchanged": unchanged, "skipped": skipped, "errors": errors}

    def available(self, hashes) -> set[str]:
        requested = []
        for value in hashes:
            try:
                requested.append(normalize_sha512(value))
            except ValueError:
                continue
        result: set[str] = set()
        with self._db() as db:
            for start in range(0, len(requested), 400):
                batch = requested[start:start + 400]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                rows = db.execute(f"SELECT DISTINCT sha512 FROM block_source WHERE sha512 IN ({placeholders})", batch).fetchall()
                result.update(str(row[0]) for row in rows)
        for digest in requested:
            if self.cache_path(digest).is_file():
                result.add(digest)
        return result

    def cache_path(self, digest: str) -> Path:
        digest = normalize_sha512(digest)
        return self.cache / digest[:2] / digest[2:4] / f"{digest}.block"

    def put_cached_block(self, digest: str, data: bytes) -> Path:
        digest = normalize_sha512(digest)
        if sha512_bytes(data) != digest:
            raise ValueError("block sha512 mismatch")
        target = self.cache_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)
        self.record_event("block_cached", sha512=digest, detail={"length": len(data)})
        return target

    def read_block(self, digest: str, expected_length: int | None = None) -> bytes:
        digest = normalize_sha512(digest)
        cached = self.cache_path(digest)
        if cached.is_file() and not cached.is_symlink():
            data = cached.read_bytes()
            if sha512_bytes(data) == digest and (expected_length is None or len(data) == expected_length):
                return data
            cached.unlink(missing_ok=True)
        with self._db() as db:
            rows = db.execute("SELECT * FROM block_source WHERE sha512=? ORDER BY indexed_at DESC", (digest,)).fetchall()
        for row in rows:
            path = (self.root / str(row["relative_path"])).resolve()
            try:
                stat = path.stat()
                if self.root not in (path, *path.parents) or path.is_symlink() or not path.is_file():
                    continue
                if stat.st_size != int(row["file_size"]) or stat.st_mtime_ns != int(row["file_mtime_ns"]):
                    continue
                with path.open("rb") as source:
                    source.seek(int(row["offset"]))
                    data = source.read(int(row["length"]))
                if expected_length is not None and len(data) != expected_length:
                    continue
                if sha512_bytes(data) == digest:
                    return data
            except OSError:
                continue
        raise KeyError(digest)

    def reconstruct(self, destination: Path, manifest: dict[str, Any]) -> dict[str, int]:
        if not content_manifest_valid(manifest):
            raise ValueError("invalid content block manifest")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        reused = 0
        with destination.open("wb") as target:
            for block in manifest["blocks"]:
                data = self.read_block(block["sha512"], int(block["length"]))
                target.write(data)
                reused += len(data)
        whole = hashlib.sha512(destination.read_bytes()).hexdigest()
        if whole != normalize_sha512(manifest["file_sha512"]):
            destination.unlink(missing_ok=True)
            raise ValueError("reconstructed file sha512 mismatch")
        return {"bytes": reused, "blocks": len(manifest["blocks"])}

    def record_event(self, action: str, *, sha512: str = "", relative_path: str = "", detail: dict[str, Any] | None = None) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO block_event(action,sha512,relative_path,detail_json,created_at) VALUES(?,?,?,?,?)",
                (action[:120], sha512[:128], relative_path[:1000], json.dumps(detail or {}, ensure_ascii=False), int(time.time())),
            )

    def stats(self) -> dict[str, int]:
        with self._db() as db:
            sources = int(db.execute("SELECT COUNT(*) FROM block_source").fetchone()[0])
            unique = int(db.execute("SELECT COUNT(DISTINCT sha512) FROM block_source").fetchone()[0])
            manifests = int(db.execute("SELECT COUNT(*) FROM file_manifest").fetchone()[0])
        cached = sum(1 for path in self.cache.glob("*/*/*.block") if path.is_file())
        return {"block_sources": sources, "unique_blocks": unique, "file_manifests": manifests, "cached_blocks": cached}
