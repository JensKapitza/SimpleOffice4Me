"""Concrete V2 adapters for existing SimpleOffice4Me subsystems."""

from .document_store import DocumentStoreStorageAdapter
from .audit import RevisionHistoryAuditAdapter

__all__ = ["DocumentStoreStorageAdapter", "RevisionHistoryAuditAdapter"]
