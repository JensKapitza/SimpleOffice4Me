"""Versioned V2 blob/content store with opaque physical chunk identities."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .contracts import LogicalObjectId, PhysicalBlobId, PersistentFormat


FORMAT = PersistentFormat("simpleoffice-v2-blob", 1)
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024


class BlobIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class BlobVersion:
    object_id: LogicalObjectId
    version_id: str
    size: int
    content_sha256: str
    chunk_count: int


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BlobIntegrityError(f"invalid blob-store metadata: {path.name}") from exc
    if not isinstance(value, dict):
        raise BlobIntegrityError(f"invalid blob-store metadata object: {path.name}")
    return value


class BlobStore:
    """Side-by-side V2 physical content store.

    Chunk names are random opaque IDs. Content hashes are integrity metadata and
    are never used as public object/chunk identities.
    """

    def __init__(self, root: str | Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE):
        self.root = Path(root).expanduser().resolve()
        self.base = self.root / ".simpleoffice-v2" / "blob-store"
        self.chunks = self.base / "chunks"
        self.objects = self.base / "objects"
        self.versions = self.base / "versions"
        self.staging = self.base / "staging"
        self.chunk_size = int(chunk_size)
        if self.chunk_size < 64 * 1024 or self.chunk_size > 64 * 1024 * 1024:
            raise ValueError("blob chunk size must be between 64 KiB and 64 MiB")
        for directory in (self.chunks, self.objects, self.versions, self.staging):
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _object_key(object_id: LogicalObjectId) -> str:
        return hashlib.sha256(object_id.value.encode("utf-8")).hexdigest()

    def _current_path(self, object_id: LogicalObjectId) -> Path:
        return self.objects / self._object_key(object_id) / "current.json"

    def _version_path(self, version_id: str) -> Path:
        try:
            normalized = str(uuid.UUID(version_id))
        except ValueError as exc:
            raise ValueError("invalid blob version id") from exc
        return self.versions / f"{normalized}.json"

    def _chunk_path(self, chunk_id: str) -> Path:
        try:
            normalized = uuid.UUID(chunk_id).hex
        except ValueError as exc:
            raise ValueError("invalid physical chunk id") from exc
        return self.chunks / f"{normalized}.bin"

    def write(self, object_id: LogicalObjectId, content: bytes) -> BlobVersion:
        from io import BytesIO

        payload = bytes(content)
        return self.write_stream(object_id, BytesIO(payload), expected_size=len(payload))

    def write_stream(
        self,
        object_id: LogicalObjectId,
        stream: BinaryIO,
        *,
        expected_size: int | None = None,
        expected_sha256: str = "",
    ) -> BlobVersion:
        if expected_size is not None and (
            isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0
        ):
            raise ValueError("expected blob size must be a non-negative integer")
        expected_digest = str(expected_sha256 or "").strip().casefold()
        if expected_digest and (
            len(expected_digest) != 64 or any(char not in "0123456789abcdef" for char in expected_digest)
        ):
            raise ValueError("expected blob sha256 must be a lowercase hexadecimal digest")

        version_id = str(uuid.uuid4())
        transaction = self.staging / version_id
        transaction.mkdir(mode=0o700)
        chunks: list[dict[str, Any]] = []
        whole = hashlib.sha256()
        total = 0
        index = 0
        try:
            while True:
                block = stream.read(self.chunk_size)
                if block is None:
                    raise ValueError("blob stream returned no bytes")
                block = bytes(block)
                if not block and index:
                    break
                total += len(block)
                if expected_size is not None and total > expected_size:
                    raise BlobIntegrityError("streamed blob exceeds expected size")
                chunk_id = PhysicalBlobId(str(uuid.uuid4()))
                digest = hashlib.sha256(block).hexdigest()
                whole.update(block)
                staged = transaction / f"{index:08d}.chunk"
                with staged.open("xb") as handle:
                    handle.write(block)
                    handle.flush()
                    os.fsync(handle.fileno())
                destination = self._chunk_path(chunk_id.value)
                os.replace(staged, destination)
                chunks.append({
                    "index": index,
                    "physical_id": chunk_id.value,
                    "size": len(block),
                    "sha256": digest,
                })
                index += 1
                if not block:
                    break

            digest = whole.hexdigest()
            if expected_size is not None and total != expected_size:
                raise BlobIntegrityError("streamed blob size does not match expected size")
            if expected_digest and digest != expected_digest:
                raise BlobIntegrityError("streamed blob sha256 does not match expected digest")
            manifest = {
                "format": {"family": FORMAT.family, "version": FORMAT.version},
                "object_id": object_id.value,
                "version_id": version_id,
                "created_at": int(time.time()),
                "size": total,
                "content_sha256": digest,
                "chunk_size": self.chunk_size,
                "chunks": chunks,
            }
            _atomic_json(self._version_path(version_id), manifest)
            _atomic_json(
                self._current_path(object_id),
                {
                    "format": {"family": FORMAT.family, "version": FORMAT.version},
                    "object_id": object_id.value,
                    "version_id": version_id,
                },
            )
            return self._as_version(manifest)
        except Exception:
            # Chunks published before a failed manifest remain harmless orphans
            # and are handled by the existing explicit GC path.
            raise
        finally:
            shutil.rmtree(transaction, ignore_errors=True)

    def contains(self, object_id: LogicalObjectId) -> bool:
        return self._current_path(object_id).is_file()

    def current_manifest(self, object_id: LogicalObjectId) -> dict[str, Any]:
        pointer = _read_json(self._current_path(object_id))
        if pointer.get("object_id") != object_id.value:
            raise BlobIntegrityError("blob current pointer object identity mismatch")
        return self.version_manifest(str(pointer.get("version_id") or ""))

    def version_manifest(self, version_id: str) -> dict[str, Any]:
        manifest = _read_json(self._version_path(version_id))
        fmt = manifest.get("format")
        if fmt != {"family": FORMAT.family, "version": FORMAT.version}:
            raise BlobIntegrityError("unsupported blob manifest format")
        if manifest.get("version_id") != version_id:
            raise BlobIntegrityError("blob version identity mismatch")
        chunks = manifest.get("chunks")
        if not isinstance(chunks, list):
            raise BlobIntegrityError("blob manifest chunks are invalid")
        return manifest

    def _verify_content(
        self,
        object_id: LogicalObjectId,
        version_id: str | None = None,
        *,
        collect: bool = False,
    ) -> tuple[dict[str, Any], bytes]:
        manifest = self.version_manifest(version_id) if version_id else self.current_manifest(object_id)
        if manifest.get("object_id") != object_id.value:
            raise BlobIntegrityError("blob manifest object identity mismatch")
        result = bytearray()
        whole = hashlib.sha256()
        total = 0
        for expected_index, chunk in enumerate(manifest["chunks"]):
            if not isinstance(chunk, dict) or int(chunk.get("index", -1)) != expected_index:
                raise BlobIntegrityError("blob chunk order is invalid")
            path = self._chunk_path(str(chunk.get("physical_id") or ""))
            try:
                block = path.read_bytes()
            except OSError as exc:
                raise BlobIntegrityError("blob chunk is missing") from exc
            if len(block) != int(chunk.get("size", -1)):
                raise BlobIntegrityError("blob chunk size mismatch")
            digest = hashlib.sha256(block).hexdigest()
            if digest != chunk.get("sha256"):
                raise BlobIntegrityError("blob chunk integrity mismatch")
            if collect:
                result.extend(block)
            total += len(block)
            whole.update(block)
        if total != int(manifest.get("size", -1)):
            raise BlobIntegrityError("blob content size mismatch")
        if whole.hexdigest() != manifest.get("content_sha256"):
            raise BlobIntegrityError("blob content integrity mismatch")
        return manifest, bytes(result)

    def verify(self, object_id: LogicalObjectId, *, version_id: str | None = None) -> BlobVersion:
        manifest, _ = self._verify_content(object_id, version_id, collect=False)
        return self._as_version(manifest)

    def read(self, object_id: LogicalObjectId, *, version_id: str | None = None) -> bytes:
        _, content = self._verify_content(object_id, version_id, collect=True)
        return content

    def versions_for(self, object_id: LogicalObjectId) -> list[BlobVersion]:
        result: list[BlobVersion] = []
        for path in self.versions.glob("*.json"):
            try:
                manifest = _read_json(path)
            except BlobIntegrityError:
                continue
            if manifest.get("object_id") == object_id.value:
                result.append(self._as_version(manifest))
        return sorted(result, key=lambda row: row.version_id)

    def inventory(self) -> dict[str, Any]:
        manifests = 0
        referenced: set[str] = set()
        invalid_manifests: list[str] = []
        for path in self.versions.glob("*.json"):
            try:
                manifest = _read_json(path)
                if manifest.get("format") != {"family": FORMAT.family, "version": FORMAT.version}:
                    raise BlobIntegrityError("unsupported blob manifest format")
                manifests += 1
                for chunk in manifest.get("chunks", []):
                    referenced.add(uuid.UUID(str(chunk["physical_id"])).hex)
            except (BlobIntegrityError, KeyError, TypeError, ValueError):
                invalid_manifests.append(path.name)

        present = {path.stem for path in self.chunks.glob("*.bin") if path.is_file()}
        staging = [path.name for path in self.staging.iterdir() if path.is_dir()]
        return {
            "format": {"family": FORMAT.family, "version": FORMAT.version},
            "manifests": manifests,
            "chunks": len(present),
            "referenced_chunks": len(referenced),
            "missing_chunks": sorted(referenced - present),
            "orphan_chunks": sorted(present - referenced),
            "invalid_manifests": sorted(invalid_manifests),
            "staging_transactions": sorted(staging),
        }

    def collect_orphans(self, *, dry_run: bool = True, minimum_age_seconds: int = 86400) -> list[str]:
        inventory = self.inventory()
        removed: list[str] = []
        cutoff = time.time() - max(0, int(minimum_age_seconds))
        for chunk_id in inventory["orphan_chunks"]:
            path = self.chunks / f"{chunk_id}.bin"
            try:
                if path.stat().st_mtime > cutoff:
                    continue
                removed.append(chunk_id)
                if not dry_run:
                    path.unlink()
            except OSError:
                continue
        return removed

    def recover_staging(self, *, minimum_age_seconds: int = 3600) -> list[str]:
        cutoff = time.time() - max(0, int(minimum_age_seconds))
        removed: list[str] = []
        for path in self.staging.iterdir():
            try:
                if path.is_dir() and not path.is_symlink() and path.stat().st_mtime <= cutoff:
                    shutil.rmtree(path)
                    removed.append(path.name)
            except OSError:
                continue
        return sorted(removed)

    @staticmethod
    def _as_version(manifest: dict[str, Any]) -> BlobVersion:
        return BlobVersion(
            object_id=LogicalObjectId(str(manifest["object_id"])),
            version_id=str(manifest["version_id"]),
            size=int(manifest["size"]),
            content_sha256=str(manifest["content_sha256"]),
            chunk_count=len(manifest["chunks"]),
        )
