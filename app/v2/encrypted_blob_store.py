"""Side-by-side encrypted V2 blob store using chunk-level AEAD.

This store is intentionally separate from the existing plaintext BlobStore.
Callers must inject an already-unlocked master key. Key provisioning/unlock and
live StoragePort cutover remain separate concerns.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .contracts import LogicalObjectId, PhysicalBlobId, PersistentFormat
from .crypto import (
    CHUNK_CRYPTO_FORMAT,
    CryptoService,
    EncryptedChunk,
    WrappedKey,
)


FORMAT = PersistentFormat("simpleoffice-v2-encrypted-blob", 1)
FOOTER_SCHEMA = "simpleoffice-v2-encrypted-blob-footer/v1"
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024 * 1024
_GCM_TAG_BYTES = 16


class EncryptedBlobIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncryptedBlobVersion:
    object_id: LogicalObjectId
    version_id: str
    size: int
    content_sha256: str
    chunk_count: int


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    text = str(value or "")
    if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in text):
        raise EncryptedBlobIntegrityError("invalid encrypted blob base64url field")
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        return base64.b64decode(text + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise EncryptedBlobIntegrityError("invalid encrypted blob base64url field") from exc


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise OSError("encrypted blob metadata directory must be a real directory")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EncryptedBlobIntegrityError(f"invalid encrypted blob metadata: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_METADATA_BYTES
        ):
            raise EncryptedBlobIntegrityError(f"invalid encrypted blob metadata: {path.name}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            raise EncryptedBlobIntegrityError(f"encrypted blob metadata is too large: {path.name}")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EncryptedBlobIntegrityError(f"invalid encrypted blob metadata: {path.name}") from exc
    if not isinstance(value, dict):
        raise EncryptedBlobIntegrityError("encrypted blob metadata must be a JSON object")
    return value


def _read_regular(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EncryptedBlobIntegrityError("encrypted blob chunk is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise EncryptedBlobIntegrityError("encrypted blob chunk is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(maximum + 1)
        if len(payload) > maximum:
            raise EncryptedBlobIntegrityError("encrypted blob chunk exceeds declared size")
        return payload
    finally:
        os.close(descriptor)


def _wrapped_to_dict(value: WrappedKey) -> dict[str, str]:
    return {
        "format": value.format,
        "nonce": value.nonce,
        "ciphertext": value.ciphertext,
    }


def _wrapped_from_dict(value: Any) -> WrappedKey:
    if not isinstance(value, dict):
        raise EncryptedBlobIntegrityError("encrypted blob wrapped key is invalid")
    try:
        return WrappedKey(
            format=str(value["format"]),
            nonce=str(value["nonce"]),
            ciphertext=str(value["ciphertext"]),
        )
    except KeyError as exc:
        raise EncryptedBlobIntegrityError("encrypted blob wrapped key is incomplete") from exc


def _chunk_to_dict(value: EncryptedChunk, *, include_ciphertext: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "format": value.format,
        "purpose": value.purpose,
        "index": value.index,
        "plaintext_size": value.plaintext_size,
        "nonce": value.nonce,
    }
    if include_ciphertext:
        result["ciphertext"] = value.ciphertext
    return result


def _chunk_from_dict(value: Any, ciphertext: str) -> EncryptedChunk:
    if not isinstance(value, dict):
        raise EncryptedBlobIntegrityError("encrypted chunk metadata is invalid")
    try:
        return EncryptedChunk(
            format=str(value["format"]),
            purpose=str(value["purpose"]),
            index=int(value["index"]),
            plaintext_size=int(value["plaintext_size"]),
            nonce=str(value["nonce"]),
            ciphertext=str(ciphertext),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EncryptedBlobIntegrityError("encrypted chunk metadata is incomplete") from exc


class EncryptedBlobStore:
    """Versioned physical store whose data chunks are ciphertext at rest."""

    def __init__(
        self,
        root: str | Path,
        master_key: bytes,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        crypto: CryptoService | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        self.base = self.root / ".simpleoffice-v2" / "encrypted-blob-store"
        self.chunks = self.base / "chunks"
        self.objects = self.base / "objects"
        self.versions = self.base / "versions"
        self.staging = self.base / "staging"
        self.master_key = bytes(master_key)
        if len(self.master_key) != 32:
            raise ValueError("encrypted blob master key must be 32 bytes")
        self.chunk_size = int(chunk_size)
        if self.chunk_size < 64 * 1024 or self.chunk_size > 64 * 1024 * 1024:
            raise ValueError("encrypted blob chunk size must be between 64 KiB and 64 MiB")
        self.crypto = crypto or CryptoService()
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        control = self.root / ".simpleoffice-v2"
        control.mkdir(parents=True, exist_ok=True)
        if control.is_symlink() or not control.is_dir():
            raise ValueError("V2 control directory must be a real directory")
        self.base.mkdir(exist_ok=True)
        if self.base.is_symlink() or not self.base.is_dir():
            raise ValueError("encrypted blob store must be a real directory")
        for directory in (self.chunks, self.objects, self.versions, self.staging):
            directory.mkdir(exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("encrypted blob store subdirectories must be real directories")
        if os.name == "posix":
            for directory in (self.base, self.chunks, self.objects, self.versions, self.staging):
                try:
                    os.chmod(directory, 0o700)
                except OSError as exc:
                    raise ValueError("encrypted blob store directory permissions could not be secured") from exc

    @staticmethod
    def _object_key(object_id: LogicalObjectId) -> str:
        return hashlib.sha256(object_id.value.encode("utf-8")).hexdigest()

    def _current_path(self, object_id: LogicalObjectId) -> Path:
        return self.objects / self._object_key(object_id) / "current.json"

    def _version_path(self, version_id: str) -> Path:
        try:
            normalized = str(uuid.UUID(str(version_id)))
        except ValueError as exc:
            raise ValueError("invalid encrypted blob version id") from exc
        return self.versions / f"{normalized}.json"

    def _chunk_path(self, physical_id: str) -> Path:
        try:
            normalized = uuid.UUID(str(physical_id)).hex
        except ValueError as exc:
            raise EncryptedBlobIntegrityError("invalid encrypted blob physical id") from exc
        return self.chunks / f"{normalized}.bin"

    @staticmethod
    def _purpose(object_id: LogicalObjectId, version_id: str) -> str:
        object_digest = hashlib.sha256(object_id.value.encode("utf-8")).hexdigest()
        return f"encrypted-blob:{version_id}:{object_digest}"

    @staticmethod
    def _expected(expected_size: int | None, expected_sha256: str) -> tuple[int | None, str]:
        if expected_size is not None and (
            isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0
        ):
            raise ValueError("expected encrypted blob size must be a non-negative integer")
        digest = str(expected_sha256 or "").strip().casefold()
        if digest and (len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
            raise ValueError("expected encrypted blob sha256 must be a lowercase hexadecimal digest")
        return expected_size, digest

    def write(self, object_id: LogicalObjectId, content: bytes) -> EncryptedBlobVersion:
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
    ) -> EncryptedBlobVersion:
        expected_size, expected_digest = self._expected(expected_size, expected_sha256)
        version_id = str(uuid.uuid4())
        purpose = self._purpose(object_id, version_id)
        session = self.crypto.begin_chunk_encryption(self.master_key, purpose=purpose)
        transaction = self.staging / version_id
        transaction.mkdir(mode=0o700)
        chunks: list[dict[str, Any]] = []
        whole = hashlib.sha256()
        total = 0
        try:
            index = 0
            while True:
                block = stream.read(self.chunk_size)
                if block is None:
                    raise ValueError("encrypted blob stream returned no bytes")
                block = bytes(block)
                if len(block) > self.chunk_size:
                    raise ValueError("encrypted blob stream exceeded requested chunk size")
                if not block:
                    break
                total += len(block)
                if expected_size is not None and total > expected_size:
                    raise EncryptedBlobIntegrityError("encrypted blob stream exceeds expected size")
                whole.update(block)
                encrypted = session.encrypt_chunk(index, block)
                ciphertext = _unb64(encrypted.ciphertext)
                if len(ciphertext) != len(block) + _GCM_TAG_BYTES:
                    raise EncryptedBlobIntegrityError("unexpected encrypted chunk ciphertext size")
                physical_id = PhysicalBlobId(str(uuid.uuid4()))
                staged = transaction / f"{index:08d}.chunk"
                descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(ciphertext)
                    handle.flush()
                    os.fsync(handle.fileno())
                destination = self._chunk_path(physical_id.value)
                os.replace(staged, destination)
                chunks.append({
                    **_chunk_to_dict(encrypted, include_ciphertext=False),
                    "physical_id": physical_id.value,
                    "ciphertext_size": len(ciphertext),
                    "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
                })
                index += 1

            digest = whole.hexdigest()
            if expected_size is not None and total != expected_size:
                raise EncryptedBlobIntegrityError("encrypted blob size does not match expected size")
            if expected_digest and digest != expected_digest:
                raise EncryptedBlobIntegrityError("encrypted blob sha256 does not match expected digest")

            footer_plaintext = json.dumps(
                {
                    "schema": FOOTER_SCHEMA,
                    "size": total,
                    "content_sha256": digest,
                    "chunk_count": len(chunks),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            footer = session.encrypt_chunk(len(chunks), footer_plaintext)
            manifest = {
                "format": {"family": FORMAT.family, "version": FORMAT.version},
                "object_id": object_id.value,
                "version_id": version_id,
                "created_at": int(time.time()),
                "purpose": purpose,
                "chunk_size": self.chunk_size,
                "wrapped_key": _wrapped_to_dict(session.wrapped_key),
                "chunks": chunks,
                "footer": _chunk_to_dict(footer, include_ciphertext=True),
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
            return EncryptedBlobVersion(object_id, version_id, total, digest, len(chunks))
        finally:
            shutil.rmtree(transaction, ignore_errors=True)

    def contains(self, object_id: LogicalObjectId) -> bool:
        return self._current_path(object_id).is_file()

    def current_manifest(self, object_id: LogicalObjectId) -> dict[str, Any]:
        pointer = _read_json(self._current_path(object_id))
        if pointer.get("format") != {"family": FORMAT.family, "version": FORMAT.version}:
            raise EncryptedBlobIntegrityError("unsupported encrypted blob current-pointer format")
        if pointer.get("object_id") != object_id.value:
            raise EncryptedBlobIntegrityError("encrypted blob current-pointer object mismatch")
        return self.version_manifest(str(pointer.get("version_id") or ""))

    def version_manifest(self, version_id: str) -> dict[str, Any]:
        manifest = _read_json(self._version_path(version_id))
        if manifest.get("format") != {"family": FORMAT.family, "version": FORMAT.version}:
            raise EncryptedBlobIntegrityError("unsupported encrypted blob manifest format")
        if manifest.get("version_id") != version_id:
            raise EncryptedBlobIntegrityError("encrypted blob version identity mismatch")
        if not isinstance(manifest.get("chunks"), list) or not isinstance(manifest.get("footer"), dict):
            raise EncryptedBlobIntegrityError("encrypted blob manifest structure is invalid")
        return manifest

    def read(self, object_id: LogicalObjectId, *, version_id: str | None = None) -> bytes:
        manifest = self.version_manifest(version_id) if version_id else self.current_manifest(object_id)
        if manifest.get("object_id") != object_id.value:
            raise EncryptedBlobIntegrityError("encrypted blob object identity mismatch")
        purpose = self._purpose(object_id, str(manifest["version_id"]))
        if manifest.get("purpose") != purpose:
            raise EncryptedBlobIntegrityError("encrypted blob purpose mismatch")
        wrapped = _wrapped_from_dict(manifest.get("wrapped_key"))
        try:
            session = self.crypto.open_chunk_encryption(self.master_key, wrapped, purpose=purpose)
        except ValueError as exc:
            raise EncryptedBlobIntegrityError("encrypted blob key authentication failed") from exc

        result = bytearray()
        physical_ids: set[str] = set()
        for expected_index, row in enumerate(manifest["chunks"]):
            if not isinstance(row, dict) or int(row.get("index", -1)) != expected_index:
                raise EncryptedBlobIntegrityError("encrypted blob chunk order is invalid")
            physical_id = str(row.get("physical_id") or "")
            if not physical_id or physical_id in physical_ids:
                raise EncryptedBlobIntegrityError("encrypted blob physical ids are invalid")
            physical_ids.add(physical_id)
            plaintext_size = int(row.get("plaintext_size", -1))
            ciphertext_size = int(row.get("ciphertext_size", -1))
            if plaintext_size < 0 or ciphertext_size != plaintext_size + _GCM_TAG_BYTES:
                raise EncryptedBlobIntegrityError("encrypted blob chunk size metadata is invalid")
            ciphertext = _read_regular(self._chunk_path(physical_id), ciphertext_size)
            if len(ciphertext) != ciphertext_size:
                raise EncryptedBlobIntegrityError("encrypted blob ciphertext size mismatch")
            if hashlib.sha256(ciphertext).hexdigest() != str(row.get("ciphertext_sha256") or ""):
                raise EncryptedBlobIntegrityError("encrypted blob ciphertext integrity mismatch")
            encrypted = _chunk_from_dict(row, _b64(ciphertext))
            try:
                plaintext = session.decrypt_chunk(encrypted, expected_index=expected_index)
            except ValueError as exc:
                raise EncryptedBlobIntegrityError("encrypted blob chunk authentication failed") from exc
            result.extend(plaintext)

        footer_row = manifest["footer"]
        footer = _chunk_from_dict(footer_row, str(footer_row.get("ciphertext") or ""))
        try:
            footer_plaintext = session.decrypt_chunk(footer, expected_index=len(manifest["chunks"]))
            metadata = json.loads(footer_plaintext.decode("utf-8"))
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise EncryptedBlobIntegrityError("encrypted blob footer authentication failed") from exc
        if not isinstance(metadata, dict) or metadata.get("schema") != FOOTER_SCHEMA:
            raise EncryptedBlobIntegrityError("encrypted blob footer format is invalid")
        digest = hashlib.sha256(result).hexdigest()
        if (
            int(metadata.get("size", -1)) != len(result)
            or int(metadata.get("chunk_count", -1)) != len(manifest["chunks"])
            or str(metadata.get("content_sha256") or "") != digest
        ):
            raise EncryptedBlobIntegrityError("encrypted blob final integrity check failed")
        return bytes(result)

    def verify(
        self,
        object_id: LogicalObjectId,
        *,
        version_id: str | None = None,
    ) -> EncryptedBlobVersion:
        manifest = self.version_manifest(version_id) if version_id else self.current_manifest(object_id)
        resolved_version = str(manifest["version_id"])
        content = self.read(object_id, version_id=resolved_version)
        return EncryptedBlobVersion(
            object_id=object_id,
            version_id=resolved_version,
            size=len(content),
            content_sha256=hashlib.sha256(content).hexdigest(),
            chunk_count=len(manifest["chunks"]),
        )

    def versions_for(self, object_id: LogicalObjectId) -> list[EncryptedBlobVersion]:
        result: list[EncryptedBlobVersion] = []
        for path in sorted(self.versions.glob("*.json")):
            try:
                manifest = _read_json(path)
                if manifest.get("object_id") != object_id.value:
                    continue
                version_id = str(manifest.get("version_id") or "")
                result.append(self.verify(object_id, version_id=version_id))
            except (EncryptedBlobIntegrityError, OSError, ValueError, TypeError):
                continue
        return sorted(result, key=lambda row: row.version_id)

    def inventory(self) -> dict[str, Any]:
        manifests = 0
        referenced: set[str] = set()
        invalid_manifests: list[str] = []
        for path in self.versions.glob("*.json"):
            try:
                manifest = _read_json(path)
                if manifest.get("format") != {"family": FORMAT.family, "version": FORMAT.version}:
                    raise EncryptedBlobIntegrityError("unsupported encrypted blob manifest format")
                chunks = manifest.get("chunks")
                if not isinstance(chunks, list):
                    raise EncryptedBlobIntegrityError("encrypted blob manifest chunks are invalid")
                seen: set[str] = set()
                for expected_index, row in enumerate(chunks):
                    if not isinstance(row, dict) or int(row.get("index", -1)) != expected_index:
                        raise EncryptedBlobIntegrityError("encrypted blob chunk order is invalid")
                    physical_id = uuid.UUID(str(row.get("physical_id") or "")).hex
                    if physical_id in seen:
                        raise EncryptedBlobIntegrityError("encrypted blob manifest repeats a physical id")
                    seen.add(physical_id)
                    referenced.add(physical_id)
                manifests += 1
            except (EncryptedBlobIntegrityError, KeyError, TypeError, ValueError):
                invalid_manifests.append(path.name)

        present = {path.stem for path in self.chunks.glob("*.bin") if path.is_file() and not path.is_symlink()}
        staging = [
            path.name
            for path in self.staging.iterdir()
            if path.is_dir() and not path.is_symlink()
        ]
        return {
            "format": {"family": FORMAT.family, "version": FORMAT.version},
            "encrypted_at_rest": True,
            "manifests": manifests,
            "chunks": len(present),
            "referenced_chunks": len(referenced),
            "missing_chunks": sorted(referenced - present),
            "orphan_chunks": sorted(present - referenced),
            "invalid_manifests": sorted(invalid_manifests),
            "staging_transactions": sorted(staging),
        }

    def collect_orphans(
        self,
        *,
        dry_run: bool = True,
        minimum_age_seconds: int = 86400,
    ) -> list[str]:
        inventory = self.inventory()
        removed: list[str] = []
        cutoff = time.time() - max(0, int(minimum_age_seconds))
        for chunk_id in inventory["orphan_chunks"]:
            path = self.chunks / f"{chunk_id}.bin"
            try:
                metadata = path.stat()
                if path.is_symlink() or not path.is_file() or metadata.st_mtime > cutoff:
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

    def rewrap_version_key(
        self,
        version_id: str,
        old_master_key: bytes,
        new_master_key: bytes,
    ) -> None:
        manifest = self.version_manifest(version_id)
        object_id = LogicalObjectId(str(manifest.get("object_id") or ""))
        purpose = self._purpose(object_id, version_id)
        if manifest.get("purpose") != purpose:
            raise EncryptedBlobIntegrityError("encrypted blob purpose mismatch")
        wrapped = _wrapped_from_dict(manifest.get("wrapped_key"))
        try:
            rotated = self.crypto.rewrap_chunk_key(
                wrapped,
                old_master_key,
                new_master_key,
                purpose=purpose,
            )
        except ValueError as exc:
            raise EncryptedBlobIntegrityError("encrypted blob key rotation failed") from exc
        updated = dict(manifest)
        updated["wrapped_key"] = _wrapped_to_dict(rotated)
        _atomic_json(self._version_path(version_id), updated)
