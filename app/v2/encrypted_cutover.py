"""Offline migration from the plaintext V2 BlobStore to encrypted blobs.

This transition deliberately retains the plaintext BlobStore and legacy
DocumentStore compatibility projection. It activates only the encrypted V2
blob backend; it does not claim full local encryption at rest.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .blob_store import BlobIntegrityError, BlobStore
from .catalog import CatalogEntry, ObjectCatalog
from .cutover import (
    LOCAL_ENCRYPTED_BLOB,
    LOCAL_PLAINTEXT,
    _now,
    _write_cutover_state,
    load_cutover_state,
)
from .encrypted_blob_store import EncryptedBlobIntegrityError, EncryptedBlobStore


FORMAT = "simpleoffice-v2-encrypted-blob-cutover/v1"
_SPOOL_MEMORY_BYTES = 8 * 1024 * 1024


def _catalog_fingerprint(entries: list[CatalogEntry]) -> str:
    payload = [
        {
            "object_id": entry.object_id.value,
            "location": entry.location.relative_path,
            "version_id": entry.version_id,
            "size": entry.size,
            "content_sha256": entry.content_sha256,
            "state": entry.state.value,
            "deleted_at": entry.deleted_at,
        }
        for entry in sorted(entries, key=lambda row: row.object_id.value)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _version_matches(expected: Any, actual: Any) -> bool:
    return (
        expected.object_id == actual.object_id
        and expected.version_id == actual.version_id
        and expected.size == actual.size
        and expected.content_sha256 == actual.content_sha256
    )


def _source_inventory_blockers(store: BlobStore) -> list[str]:
    inventory = store.inventory()
    blockers: list[str] = []
    if inventory["invalid_manifests"]:
        blockers.append("plaintext V2 store contains invalid version manifests")
    if inventory["missing_chunks"]:
        blockers.append("plaintext V2 store contains referenced chunks that are missing")
    if inventory["staging_transactions"]:
        blockers.append("plaintext V2 store contains unfinished staging transactions")
    return blockers


def _verify_active_encrypted_backend(
    catalog: ObjectCatalog,
    encrypted: EncryptedBlobStore,
    *,
    applied: bool,
) -> dict[str, Any]:
    entries = catalog.list(include_deleted=True)
    blockers: list[str] = []
    inventory = encrypted.inventory()
    if inventory["invalid_manifests"]:
        blockers.append("encrypted V2 store contains invalid version manifests")
    if inventory["missing_chunks"]:
        blockers.append("encrypted V2 store contains referenced chunks that are missing")
    if inventory["staging_transactions"]:
        blockers.append("encrypted V2 store contains unfinished staging transactions")

    checked = 0
    for entry in entries:
        checked += 1
        try:
            version = encrypted.verify(
                entry.object_id,
                version_id=entry.version_id,
            )
            if (
                version.size != entry.size
                or version.content_sha256 != entry.content_sha256
            ):
                raise EncryptedBlobIntegrityError(
                    "encrypted current version does not match catalog integrity metadata"
                )
        except (
            EncryptedBlobIntegrityError,
            FileNotFoundError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            blockers.append(
                f"object {entry.object_id.value} version {entry.version_id}: {exc}"
            )

    return {
        "format": FORMAT,
        "applied": bool(applied),
        "ready": not blockers,
        "objects": len(entries),
        "versions_checked": checked,
        "versions_pending": 0,
        "versions_migrated": 0,
        "versions_already_encrypted": checked,
        "catalog_fingerprint": _catalog_fingerprint(entries),
        "blockers": blockers,
        "encrypted_blob_backend_active": not blockers,
        "plaintext_blob_store_retained": True,
        "legacy_plaintext_projection_retained": True,
        "encrypted_at_rest": False,
    }


def encrypted_blob_cutover(
    root: str | Path,
    master_key: bytes,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Verify/migrate all catalogued V2 versions and optionally switch runtime.

    The caller must stop normal application writers during an applying run.
    A catalog fingerprint is checked before and after the migration so a
    concurrent catalog mutation prevents activation rather than silently
    switching to an incomplete encrypted backend.
    """

    source = Path(root).expanduser().resolve()
    state = load_cutover_state(source)
    if state.mode != "v2":
        raise ValueError("encrypted blob cutover requires authoritative V2 mode")
    if state.protection_mode not in {LOCAL_PLAINTEXT, LOCAL_ENCRYPTED_BLOB}:
        raise ValueError("encrypted blob cutover requires an explicit local V2 protection mode")

    catalog = ObjectCatalog(source)
    encrypted_base = source / ".simpleoffice-v2" / "encrypted-blob-store"
    if state.protection_mode == LOCAL_ENCRYPTED_BLOB:
        if not encrypted_base.is_dir() or encrypted_base.is_symlink():
            return {
                "format": FORMAT,
                "applied": bool(apply),
                "ready": False,
                "objects": len(catalog.list(include_deleted=True)),
                "versions_checked": 0,
                "versions_pending": 0,
                "versions_migrated": 0,
                "versions_already_encrypted": 0,
                "catalog_fingerprint": _catalog_fingerprint(
                    catalog.list(include_deleted=True)
                ),
                "blockers": ["encrypted V2 blob store is unavailable"],
                "encrypted_blob_backend_active": False,
                "plaintext_blob_store_retained": True,
                "legacy_plaintext_projection_retained": True,
                "encrypted_at_rest": False,
            }
        encrypted = EncryptedBlobStore(
            source,
            bytes(master_key),
            initialize=False,
        )
        return _verify_active_encrypted_backend(
            catalog,
            encrypted,
            applied=apply,
        )

    encrypted = (
        EncryptedBlobStore(source, bytes(master_key))
        if apply
        else (
            EncryptedBlobStore(source, bytes(master_key), initialize=False)
            if encrypted_base.is_dir() and not encrypted_base.is_symlink()
            else None
        )
    )
    plaintext = BlobStore(source)
    entries = catalog.list(include_deleted=True)
    before_fingerprint = _catalog_fingerprint(entries)
    blockers = _source_inventory_blockers(plaintext)
    if blockers and apply:
        return {
            "format": FORMAT,
            "applied": True,
            "ready": False,
            "objects": len(entries),
            "versions_checked": 0,
            "versions_pending": 0,
            "versions_migrated": 0,
            "versions_already_encrypted": 0,
            "catalog_fingerprint": before_fingerprint,
            "blockers": blockers,
            "encrypted_blob_backend_active": False,
            "plaintext_blob_store_retained": True,
            "legacy_plaintext_projection_retained": True,
            "encrypted_at_rest": False,
        }
    pending = 0
    migrated = 0
    existing = 0
    checked = 0

    for entry in entries:
        versions = plaintext.versions_for(entry.object_id)
        by_id = {version.version_id: version for version in versions}
        if entry.version_id not in by_id:
            blockers.append(
                f"catalog current plaintext version is missing for object {entry.object_id.value}"
            )
            continue

        ordered = [
            version for version in versions
            if version.version_id != entry.version_id
        ] + [by_id[entry.version_id]]

        for expected in ordered:
            checked += 1
            try:
                verified_plain = plaintext.verify(
                    entry.object_id,
                    version_id=expected.version_id,
                )
                if not _version_matches(expected, verified_plain):
                    raise BlobIntegrityError("plaintext version metadata changed during verification")

                if encrypted is not None and encrypted.contains_version(expected.version_id):
                    verified_encrypted = encrypted.verify(
                        entry.object_id,
                        version_id=expected.version_id,
                    )
                    if not _version_matches(expected, verified_encrypted):
                        raise EncryptedBlobIntegrityError(
                            "encrypted version does not match plaintext source"
                        )
                    existing += 1
                    continue

                if not apply:
                    pending += 1
                    continue

                with tempfile.SpooledTemporaryFile(
                    max_size=_SPOOL_MEMORY_BYTES,
                    mode="w+b",
                ) as spool:
                    streamed = plaintext.copy_verified_to(
                        entry.object_id,
                        spool,
                        version_id=expected.version_id,
                    )
                    if not _version_matches(expected, streamed):
                        raise BlobIntegrityError(
                            "plaintext version changed while streaming migration"
                        )
                    spool.seek(0)
                    if encrypted is None:
                        raise RuntimeError("encrypted blob store was not initialized for migration")
                    migrated_version = encrypted.write_stream(
                        entry.object_id,
                        spool,
                        expected_size=expected.size,
                        expected_sha256=expected.content_sha256,
                        version_id=expected.version_id,
                    )
                if not _version_matches(expected, migrated_version):
                    raise EncryptedBlobIntegrityError(
                        "encrypted migration result differs from plaintext source"
                    )
                migrated += 1
            except (
                BlobIntegrityError,
                EncryptedBlobIntegrityError,
                FileNotFoundError,
                OSError,
                TypeError,
                ValueError,
            ) as exc:
                blockers.append(
                    f"object {entry.object_id.value} version {expected.version_id}: {exc}"
                )
                break

        if apply and not blockers:
            try:
                if encrypted is None:
                    raise RuntimeError("encrypted blob store was not initialized for activation")
                encrypted.select_current(entry.object_id, entry.version_id)
                current = encrypted.verify(entry.object_id, version_id=entry.version_id)
                if (
                    current.size != entry.size
                    or current.content_sha256 != entry.content_sha256
                ):
                    raise EncryptedBlobIntegrityError(
                        "encrypted current version does not match catalog integrity metadata"
                    )
            except (
                EncryptedBlobIntegrityError,
                FileNotFoundError,
                OSError,
                TypeError,
                ValueError,
            ) as exc:
                blockers.append(
                    f"object {entry.object_id.value} current encrypted version: {exc}"
                )

    after_entries = catalog.list(include_deleted=True)
    after_fingerprint = _catalog_fingerprint(after_entries)
    if before_fingerprint != after_fingerprint:
        blockers.append("V2 object catalog changed during encrypted migration; rerun during maintenance")

    ready = not blockers and (apply or pending == 0)
    activated = state.protection_mode == LOCAL_ENCRYPTED_BLOB
    if apply and ready and not activated:
        now = _now()
        _write_cutover_state(
            source,
            replace(
                state,
                protection_mode=LOCAL_ENCRYPTED_BLOB,
                verified_at=now,
                updated_at=now,
                dirty=False,
                dirty_reason="",
            ),
        )
        activated = True

    return {
        "format": FORMAT,
        "applied": bool(apply),
        "ready": bool(ready),
        "objects": len(entries),
        "versions_checked": checked,
        "versions_pending": pending if not apply else 0,
        "versions_migrated": migrated,
        "versions_already_encrypted": existing,
        "catalog_fingerprint": after_fingerprint,
        "blockers": blockers,
        "encrypted_blob_backend_active": bool(activated and ready),
        "plaintext_blob_store_retained": True,
        "legacy_plaintext_projection_retained": True,
        "encrypted_at_rest": False,
    }
