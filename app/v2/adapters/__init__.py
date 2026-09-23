"""Concrete V2 adapters for existing SimpleOffice4Me subsystems."""

from .document_store import DocumentStoreStorageAdapter
from .shadow import ShadowDocumentStorageAdapter
from .blob_catalog import BlobCatalogStorageAdapter
from .encrypted_blob_catalog import EncryptedBlobCatalogStorageAdapter
from .authoritative import V2AuthoritativeStorageAdapter
from .audit import RevisionHistoryAuditAdapter

__all__ = ["BlobCatalogStorageAdapter", "DocumentStoreStorageAdapter", "EncryptedBlobCatalogStorageAdapter", "RevisionHistoryAuditAdapter", "ShadowDocumentStorageAdapter"]
