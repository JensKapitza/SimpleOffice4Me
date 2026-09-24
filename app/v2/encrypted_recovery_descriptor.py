"""Portable self-describing metadata for encrypted V2 blob recovery.

The descriptor intentionally contains encrypted-store metadata only. Plaintext
content digests stay inside the authenticated encrypted footer and no recovery
or master-key material is embedded.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Any, Mapping

from .contracts import LogicalObjectId
from .encrypted_blob_store import FORMAT as ENCRYPTED_BLOB_FORMAT


DESCRIPTOR_FORMAT = "simpleoffice-v2-encrypted-recovery-descriptor/v1"
MAX_DESCRIPTOR_BYTES = 80 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _profile_hash(value: str) -> str:
    text = str(value or "").strip().casefold()
    if not _SHA256.fullmatch(text):
        raise ValueError("encrypted recovery descriptor profile hash is invalid")
    return text


def _version_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value or "")))
    except ValueError as exc:
        raise ValueError("encrypted recovery descriptor version id is invalid") from exc


def _manifest_search_refs(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list):
        raise ValueError("encrypted recovery descriptor manifest chunks are invalid")
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for expected_index, row in enumerate(chunks):
        if not isinstance(row, Mapping) or int(row.get("index", -1)) != expected_index:
            raise ValueError("encrypted recovery descriptor chunk order is invalid")
        try:
            physical_id = str(uuid.UUID(str(row.get("physical_id") or "")))
        except ValueError as exc:
            raise ValueError("encrypted recovery descriptor physical id is invalid") from exc
        if physical_id in seen:
            raise ValueError("encrypted recovery descriptor repeats a physical id")
        seen.add(physical_id)
        size = int(row.get("ciphertext_size", -1))
        digest = str(row.get("ciphertext_sha256") or "").strip().casefold()
        if size < 16 or not _SHA256.fullmatch(digest):
            raise ValueError("encrypted recovery descriptor ciphertext metadata is invalid")
        refs.append({
            "index": expected_index,
            "physical_id": physical_id,
            "ciphertext_size": size,
            "ciphertext_sha256": digest,
        })
    return refs


def _descriptor_id(
    *,
    profile_hash: str,
    object_id: str,
    version_id: str,
    manifest_sha256: str,
) -> str:
    payload = "\0".join((
        DESCRIPTOR_FORMAT,
        profile_hash,
        object_id,
        version_id,
        manifest_sha256,
    )).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_encrypted_recovery_descriptor(
    manifest: Mapping[str, Any],
    *,
    recovery_profile_hash: str,
) -> dict[str, Any]:
    """Build a portable descriptor from one authenticated encrypted manifest."""

    if not isinstance(manifest, Mapping):
        raise ValueError("encrypted recovery descriptor manifest must be an object")
    manifest_value = dict(manifest)
    expected_format = {
        "family": ENCRYPTED_BLOB_FORMAT.family,
        "version": ENCRYPTED_BLOB_FORMAT.version,
    }
    if manifest_value.get("format") != expected_format:
        raise ValueError("encrypted recovery descriptor blob format is unsupported")
    object_id = LogicalObjectId(str(manifest_value.get("object_id") or "")).value
    version_id = _version_id(str(manifest_value.get("version_id") or ""))
    if str(manifest_value.get("version_id") or "") != version_id:
        raise ValueError("encrypted recovery descriptor version id is not canonical")
    expected_purpose = (
        f"encrypted-blob:{version_id}:"
        f"{hashlib.sha256(object_id.encode('utf-8')).hexdigest()}"
    )
    if manifest_value.get("purpose") != expected_purpose:
        raise ValueError("encrypted recovery descriptor purpose binding is invalid")
    if not isinstance(manifest_value.get("wrapped_key"), Mapping):
        raise ValueError("encrypted recovery descriptor wrapped key is missing")
    if not isinstance(manifest_value.get("footer"), Mapping):
        raise ValueError("encrypted recovery descriptor footer is missing")

    refs = _manifest_search_refs(manifest_value)
    manifest_sha256 = _digest(manifest_value)
    profile_hash = _profile_hash(recovery_profile_hash)
    descriptor_id = _descriptor_id(
        profile_hash=profile_hash,
        object_id=object_id,
        version_id=version_id,
        manifest_sha256=manifest_sha256,
    )
    return {
        "format": DESCRIPTOR_FORMAT,
        "descriptor_id": descriptor_id,
        "recovery_profile_hash": profile_hash,
        "object_id": object_id,
        "version_id": version_id,
        "encrypted_blob_format": expected_format,
        "manifest_sha256": manifest_sha256,
        "ciphertext_chunks": refs,
        "manifest": manifest_value,
    }


def validate_encrypted_recovery_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate descriptor structure and internal cross-bindings.

    This is a structural/integrity check, not a replacement for decrypting and
    authenticating the embedded manifest with the recovered master key.
    """

    if not isinstance(value, Mapping) or value.get("format") != DESCRIPTOR_FORMAT:
        raise ValueError("unsupported encrypted recovery descriptor format")
    manifest = value.get("manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("encrypted recovery descriptor manifest is missing")
    rebuilt = build_encrypted_recovery_descriptor(
        manifest,
        recovery_profile_hash=str(value.get("recovery_profile_hash") or ""),
    )
    for field in (
        "descriptor_id",
        "object_id",
        "version_id",
        "encrypted_blob_format",
        "manifest_sha256",
        "ciphertext_chunks",
    ):
        if value.get(field) != rebuilt[field]:
            raise ValueError(f"encrypted recovery descriptor {field} binding is invalid")
    return rebuilt


def descriptor_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_encrypted_recovery_descriptor(value)
    refs = checked["ciphertext_chunks"]
    return {
        "valid": True,
        "format": checked["format"],
        "descriptor_id": checked["descriptor_id"],
        "recovery_profile_hash": checked["recovery_profile_hash"],
        "object_id": checked["object_id"],
        "version_id": checked["version_id"],
        "manifest_sha256": checked["manifest_sha256"],
        "chunk_count": len(refs),
        "ciphertext_bytes": sum(int(item["ciphertext_size"]) for item in refs),
        "ciphertext_chunks": refs,
        "contains_plaintext_content_hash": False,
        "contains_key_material": False,
    }


def load_encrypted_recovery_descriptor(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ValueError("encrypted recovery descriptor could not be opened") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_DESCRIPTOR_BYTES
        ):
            raise ValueError("encrypted recovery descriptor file is invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_DESCRIPTOR_BYTES + 1)
        if len(raw) > MAX_DESCRIPTOR_BYTES:
            raise ValueError("encrypted recovery descriptor is too large")
    finally:
        os.close(descriptor)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("encrypted recovery descriptor is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("encrypted recovery descriptor must contain a JSON object")
    return validate_encrypted_recovery_descriptor(document)


def write_encrypted_recovery_descriptor(
    path: str | Path,
    value: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically publish one private portable descriptor."""

    checked = validate_encrypted_recovery_descriptor(value)
    target = Path(path).expanduser()
    try:
        parent = target.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError("encrypted recovery descriptor output parent is unavailable") from exc
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("encrypted recovery descriptor output parent must be a real directory")
    target = parent / target.name
    if target.name in {"", ".", ".."} or target.is_symlink():
        raise ValueError("encrypted recovery descriptor output path is invalid")
    if target.exists() and not overwrite:
        raise FileExistsError("encrypted recovery descriptor output already exists")
    if target.exists() and not target.is_file():
        raise ValueError("encrypted recovery descriptor output must be a regular file")

    payload = _canonical(checked) + b"\n"
    if len(payload) > MAX_DESCRIPTOR_BYTES:
        raise ValueError("encrypted recovery descriptor is too large")

    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    published = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, target)
        else:
            # Race-safe no-overwrite publication.
            os.link(temporary, target)
            temporary.unlink()
        published = True
        if os.name == "posix":
            os.chmod(target, 0o600)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if not published:
            temporary.unlink(missing_ok=True)
    return target
