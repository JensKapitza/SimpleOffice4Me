"""Runtime selection for the document StoragePort.

The selector is deliberately centralized so browser, WebDAV/SFTP VFS and other
document consumers cannot silently choose different storage modes.
"""
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO
import hashlib
import io

from app.document_store import DocumentStore
from app.safe_paths import resolve_under

from .adapters.document_store import DocumentStoreStorageAdapter
from .adapters.shadow import ShadowDocumentStorageAdapter
from .adapters.authoritative import V2AuthoritativeStorageAdapter
from .adapters.encrypted_blob_catalog import EncryptedBlobCatalogStorageAdapter
from .contracts import ErrorCode, LogicalObjectId, OperationResult, StorageLocation, StoragePort
from .cutover import LOCAL_ENCRYPTED_BLOB, load_cutover_state
from .runtime_keys import runtime_storage_master_key
from .catalog import ObjectCatalog
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


def create_or_verify_document(root, actor, relative_path, content, *, max_bytes):
    """Retry a fixed-location create without overwriting an existing object.

    Existing bytes must be identical and verified by the selected authority;
    a missing V2 projection never permits a second create or a legacy fallback.
    """
    payload = bytes(content)
    if len(payload) > max_bytes:
        raise ValueError("document exceeds the configured upload size limit")
    location = StorageLocation(relative_path)
    documents = DocumentStore(root)
    if load_cutover_state(root).mode == "v2":
        entry = ObjectCatalog(root).get_by_location(location.relative_path)
        if not entry.ok:
            if entry.error.code is not ErrorCode.NOT_FOUND:
                result_or_raise(entry)
            document = None
        else:
            document = documents.get_document(entry.value.object_id.value)
    else:
        target = resolve_under(documents.root, location.relative_path, strict=False)
        document = documents.get_document(location.relative_path) if target.exists() else None
    if document is None:
        documents.ensure_folder_policy(documents.root / Path(relative_path).parent, actor)
        return create_document(root, actor, relative_path, payload, max_bytes=max_bytes), True
    with io.BytesIO() as target:
        stored = result_or_raise(storage_for(root, actor).copy_verified_range_to(
            LogicalObjectId(document["document_id"]), target, start=0, length=len(payload) + 1,
        ))
        if (target.getvalue() != payload or stored.size != len(payload)
                or stored.version != hashlib.sha256(payload).hexdigest()
                or stored.location != location):
            raise ValueError("existing document does not match retry content")
    return document, False


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
