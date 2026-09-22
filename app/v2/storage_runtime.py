"""Runtime selection for the document StoragePort.

The selector is deliberately centralized so browser, WebDAV/SFTP VFS and other
document consumers cannot silently choose different storage modes.
"""
from __future__ import annotations

from pathlib import Path

from .adapters.document_store import DocumentStoreStorageAdapter
from .adapters.shadow import ShadowDocumentStorageAdapter
from .contracts import StoragePort
from .cutover import load_cutover_state


def storage_for(root: str | Path, actor: str) -> StoragePort:
    state = load_cutover_state(root)
    if state.mode == "shadow":
        return ShadowDocumentStorageAdapter(root, actor)
    return DocumentStoreStorageAdapter(root, actor)
