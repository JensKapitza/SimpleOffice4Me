"""Concrete V2 adapters for existing SimpleOffice4Me subsystems."""

from .document_store import DocumentStoreStorageAdapter
from .shadow import ShadowDocumentStorageAdapter
from .blob_catalog import BlobCatalogStorageAdapter
from .audit import RevisionHistoryAuditAdapter

__all__ = ["BlobCatalogStorageAdapter", "DocumentStoreStorageAdapter", "RevisionHistoryAuditAdapter", "ShadowDocumentStorageAdapter"]
