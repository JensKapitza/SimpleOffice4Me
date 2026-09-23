"""Concrete V2 adapters for existing SimpleOffice4Me subsystems."""

from .document_store import DocumentStoreStorageAdapter
from .shadow import ShadowDocumentStorageAdapter
from .blob_catalog import BlobCatalogStorageAdapter
from .authoritative import V2AuthoritativeStorageAdapter
from .audit import RevisionHistoryAuditAdapter
from .zfec_codec import ZfecErasureCodec, codec_for_plan

__all__ = [
    "BlobCatalogStorageAdapter",
    "DocumentStoreStorageAdapter",
    "RevisionHistoryAuditAdapter",
    "ShadowDocumentStorageAdapter",
    "V2AuthoritativeStorageAdapter",
    "ZfecErasureCodec",
    "codec_for_plan",
]
