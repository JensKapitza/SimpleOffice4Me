"""BlobCatalog StoragePort backed by encrypted V2 blob content."""
from __future__ import annotations

from pathlib import Path

from ..catalog import ObjectCatalog
from ..contracts import AuditPort
from ..encrypted_blob_store import EncryptedBlobStore
from .blob_catalog import BlobCatalogStorageAdapter


class EncryptedBlobCatalogStorageAdapter(BlobCatalogStorageAdapter):
    """Logical V2 storage adapter with ciphertext-only physical blob chunks.

    The caller supplies an already-unlocked master key. This adapter deliberately
    does not decide how the key is provisioned, unlocked or cached.
    """

    def __init__(
        self,
        root: str | Path,
        actor: str,
        master_key: bytes,
        *,
        audit_port: AuditPort | None = None,
        catalog: ObjectCatalog | None = None,
    ):
        encrypted = EncryptedBlobStore(root, master_key)
        super().__init__(
            root,
            actor,
            audit_port=audit_port,
            blob_store=encrypted,
            catalog=catalog,
        )
        self.encrypted_blobs = encrypted
