"""Runtime selection for the document StoragePort.

The selector is deliberately centralized so browser, WebDAV/SFTP VFS and other
document consumers cannot silently choose different storage modes.
"""
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from app.document_store import DocumentStore

from .adapters.document_store import DocumentStoreStorageAdapter
from .adapters.shadow import ShadowDocumentStorageAdapter
from .adapters.authoritative import V2AuthoritativeStorageAdapter
from .adapters.encrypted_blob_catalog import EncryptedBlobCatalogStorageAdapter
from .contracts import ErrorCode, LogicalObjectId, OperationResult, StorageLocation, StoragePort
from .cutover import LOCAL_ENCRYPTED_BLOB, load_cutover_state
from .runtime_keys import runtime_storage_master_key
from .storage_key_rotation import rotation_pending


def storage_for(root: str | Path, actor: str) -> StoragePort:
    state = load_cutover_state(root)
    if state.mode == "shadow":
        return ShadowDocumentStorageAdapter(root, actor)
    if state.mode == "v2":
        if state.protection_mode == LOCAL_ENCRYPTED_BLOB:
            if rotation_pending(root):
                raise RuntimeError(
                    "encrypted V2 storage is unavailable while master-key rotation is pending"
                )
            master_key = runtime_storage_master_key(root)
            primary = EncryptedBlobCatalogStorageAdapter(root, actor, master_key)
            return V2AuthoritativeStorageAdapter(root, actor, primary=primary)
        return V2AuthoritativeStorageAdapter(root, actor)
    return DocumentStoreStorageAdapter(root, actor)


def result_or_raise(result: OperationResult):
    if result.ok:
        return result.value
    error = result.error
    message = error.message if error else "storage operation failed"
    code = error.code if error else ErrorCode.INTERNAL_ERROR
    if code is ErrorCode.NOT_FOUND:
        raise FileNotFoundError(message)
    if code is ErrorCode.INTEGRITY_ERROR:
        raise RuntimeError(message)
    if code in {ErrorCode.STORAGE_UNAVAILABLE, ErrorCode.RETRYABLE}:
        raise OSError(message)
    if code is ErrorCode.FORBIDDEN:
        raise PermissionError(message)
    raise ValueError(message)


def _metadata(root: str | Path, object_id: LogicalObjectId):
    return DocumentStore(root).get_document(object_id.value)


def create_document(
    root: str | Path,
    actor: str,
    relative_path: str,
    content: bytes,
    *,
    max_bytes: int = 512 * 1024 * 1024,
):
    payload = bytes(content)
    if len(payload) > int(max_bytes):
        raise ValueError("document exceeds the configured upload size limit")
    stored = result_or_raise(
        storage_for(root, actor).create_bytes(
            StorageLocation(relative_path),
            payload,
        )
    )
    return _metadata(root, stored.object_id)


def replace_document(
    root: str | Path,
    actor: str,
    object_id: str,
    content: bytes,
    *,
    expected_version: str | None = None,
    source: str = "v2-storage-runtime",
    restored_from_version: str = "",
    max_bytes: int = 512 * 1024 * 1024,
):
    payload = bytes(content)
    if len(payload) > int(max_bytes):
        raise ValueError("document exceeds the configured upload size limit")
    stored = result_or_raise(
        storage_for(root, actor).replace_bytes(
            LogicalObjectId(str(object_id)),
            payload,
            expected_version=expected_version,
            source=source,
            restored_from_version=restored_from_version,
        )
    )
    return _metadata(root, stored.object_id)


def import_document(
    root: str | Path,
    actor: str,
    stream: BinaryIO,
    filename: str,
    *,
    archive: bool = False,
    max_bytes: int = 512 * 1024 * 1024,
):
    stored = result_or_raise(
        storage_for(root, actor).import_stream(
            stream,
            filename,
            archive=archive,
            max_bytes=max_bytes,
        )
    )
    return _metadata(root, stored.object_id)


def delete_document(
    root: str | Path,
    actor: str,
    object_id: str,
    *,
    expected_version: str | None = None,
):
    return result_or_raise(
        storage_for(root, actor).delete(
            LogicalObjectId(str(object_id)),
            expected_version=expected_version,
        )
    )


def move_document(
    root: str | Path,
    actor: str,
    object_id: str,
    destination: str,
):
    stored = result_or_raise(
        storage_for(root, actor).move(
            LogicalObjectId(str(object_id)),
            StorageLocation(destination),
        )
    )
    return _metadata(root, stored.object_id)


def restore_document(
    root: str | Path,
    actor: str,
    object_id: str,
    destination: str,
    *,
    expected_version: str | None = None,
):
    stored = result_or_raise(
        storage_for(root, actor).restore(
            LogicalObjectId(str(object_id)),
            StorageLocation(destination),
            expected_version=expected_version,
        )
    )
    return _metadata(root, stored.object_id)


def restore_document_content(
    root: str | Path,
    actor: str,
    object_id: str,
    archived_version: str,
    expected_current_version: str,
    *,
    max_bytes: int = 512 * 1024 * 1024,
):
    content = DocumentStore(root).read_content_recovery_version(
        str(object_id),
        str(archived_version),
        str(expected_current_version),
        str(actor),
        max_bytes=int(max_bytes),
    )
    return replace_document(
        root,
        actor,
        object_id,
        content,
        expected_version=expected_current_version,
        source="recovery",
        restored_from_version=archived_version,
        max_bytes=max_bytes,
    )
