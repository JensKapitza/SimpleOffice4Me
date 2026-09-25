"""Read-only Phase-15 legacy-cleanup readiness assessment.

This module intentionally implements no deletion. It centralizes the conditions
that must be satisfied before the retained plaintext compatibility stores can be
considered removable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters.authoritative import V2AuthoritativeStorageAdapter
from .cutover import LOCAL_ENCRYPTED_BLOB, load_cutover_state
from .storage_key_rotation import rotation_pending


FORMAT = "simpleoffice-v2-legacy-cleanup-readiness"
FORMAT_VERSION = 1

# Keep this list explicit and reviewable.  It is a release-safety inventory,
# not a runtime source-code scanner.  Each item represents a production path
# that can still consume or mutate persistent plaintext document files.
_REMAINING_CONTENT_PROJECTION_CONSUMERS = (
    "DocumentStore mutation/recovery still persists plaintext document files",
    "WebDAV document access still resolves managed document paths directly",
    "business/rental/photo/contact/replication consumers still resolve managed document paths directly",
    "legacy federation transfer still resolves managed document paths directly",
)


def remaining_content_projection_consumers() -> list[str]:
    """Return the known blockers for removing persistent plaintext content."""

    return list(_REMAINING_CONTENT_PROJECTION_CONSUMERS)


def _count_regular_json(directory: Path) -> int:
    if not directory.is_dir() or directory.is_symlink():
        return 0
    count = 0
    for path in directory.glob("*.json"):
        if path.is_file() and not path.is_symlink():
            count += 1
    return count


def legacy_cleanup_status(root: str | Path) -> dict[str, Any]:
    """Return cleanup readiness without changing the data root."""

    source = Path(root).expanduser().resolve()
    state = load_cutover_state(source)
    blockers: list[str] = []

    if state.mode != "v2":
        blockers.append("authoritative V2 mode is required before legacy cleanup")
    if state.protection_mode != LOCAL_ENCRYPTED_BLOB:
        blockers.append(
            "encrypted V2 blob protection must be active before plaintext cleanup"
        )

    rotation_blocked = False
    try:
        rotation_blocked = rotation_pending(source)
    except (OSError, RuntimeError, ValueError):
        rotation_blocked = True
        blockers.append(
            "storage master-key rotation state is unreadable and must be resolved"
        )
    if rotation_blocked and not any("rotation state is unreadable" in item for item in blockers):
        blockers.append("storage master-key rotation is still pending")

    projection_required = bool(
        V2AuthoritativeStorageAdapter.requires_legacy_projection
    )
    projection_consumers = (
        remaining_content_projection_consumers() if projection_required else []
    )
    if projection_required:
        blockers.append(
            "authoritative storage still requires the legacy DocumentStore compatibility projection"
        )

    legacy_metadata = source / ".simpleoffice-meta" / "documents"
    plaintext_blob_store = source / ".simpleoffice-v2" / "blob-store"

    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "root": str(source),
        "mode": state.mode,
        "protection_mode": state.protection_mode,
        "ready_for_cleanup": not blockers,
        "deletion_supported": False,
        "compatibility_projection_required": projection_required,
        "remaining_content_projection_consumers": projection_consumers,
        "projection_free_cutover_ready": not blockers,
        "storage_key_rotation_pending": rotation_blocked,
        "legacy_document_metadata_present": legacy_metadata.is_dir(),
        "legacy_document_metadata_files": _count_regular_json(legacy_metadata),
        "plaintext_v2_blob_store_present": plaintext_blob_store.is_dir(),
        "blockers": blockers,
    }
