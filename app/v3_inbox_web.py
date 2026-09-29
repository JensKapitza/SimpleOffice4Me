"""Web-upload adapter for the optional 3.0 inbox pipeline."""
from __future__ import annotations

from typing import Mapping

from .v3_capabilities import enabled as v3_capability_enabled
from .v3_inbox import InboxStore, record_completed_best_effort


def begin_web_inbox_upload(config: Mapping[str, object], item, actor: str, source_key: str) -> dict[str, object]:
    """Create/scan an optional inbox item without changing the legacy upload contract."""
    result: dict[str, object] = {"item": None, "malware_status": "not_configured", "error": ""}
    if not v3_capability_enabled("v3.inbox"):
        return result

    root = str(config["DOCUMENT_ROOT"])
    store = InboxStore(root)
    inbox_item = store.begin(
        "web",
        source_key,
        item.filename,
        actor,
        mime_type=item.mimetype or "",
    )
    result["item"] = inbox_item
    if not config.get("WEBDAV_UPLOAD_SCAN", False):
        return result

    from .attachment_security import AttachmentSecurity, QuarantineCapacityError

    try:
        scan = AttachmentSecurity(root).scan_webdav_upload(
            item.stream,
            f"web:{actor}",
            item.filename,
            max(1, int(config.get("WEBDAV_QUARANTINE_BYTES", 200 * 1024 * 1024))),
            source_type="web-inbox",
        )
    except QuarantineCapacityError:
        store.transition(
            inbox_item.item_id,
            "failed",
            step="malware_check",
            error="scan quarantine full",
        )
        result["error"] = "Scan-Quarantäne ist voll."
        return result
    except (OSError, RuntimeError, ValueError):
        store.transition(
            inbox_item.item_id,
            "failed",
            step="malware_check",
            error="scanner unavailable",
        )
        result["error"] = "Malware-Scanner nicht verfügbar."
        return result

    item.stream.seek(0)
    malware_status = str(scan.get("verdict") or "unknown")
    result["malware_status"] = malware_status
    if malware_status != "clean":
        store.transition(
            inbox_item.item_id,
            "quarantined",
            step="malware_check",
            detail=malware_status,
        )
        result["error"] = "Datei wurde durch den Malware-Scan abgewiesen."
    return result


def fail_web_inbox_upload(root: str, inbox_item, error: str) -> None:
    """Best-effort failure bookkeeping; legacy upload errors must still be shown normally."""
    if inbox_item is None:
        return
    try:
        InboxStore(root).transition(
            inbox_item.item_id,
            "failed",
            step="persist_import",
            error=str(error),
        )
    except (LookupError, OSError, ValueError):
        pass


def complete_web_inbox_upload(
    root: str,
    inbox: Mapping[str, object],
    *,
    source_key: str,
    original_name: str,
    actor: str,
    document_id: str,
    imported_metadata: Mapping[str, object],
    imported_size: int,
    mime_type: str,
) -> None:
    """Record successful ingest through the shared idempotent completion helper."""
    record_completed_best_effort(
        root,
        source="web",
        source_key=source_key,
        original_name=original_name,
        actor=actor,
        document_id=document_id,
        sha256=str(imported_metadata.get("sha256") or ""),
        size=int(imported_metadata.get("size") or imported_size or 0),
        malware_status=str(inbox.get("malware_status") or "not_configured"),
        mime_type=mime_type,
    )
