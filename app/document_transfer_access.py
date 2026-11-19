"""Document-level eligibility shared by file, path and chat transfer actions."""
from __future__ import annotations

from typing import Any

from .chat_access import is_private_chat_document
from .mail_document_access import private_mail_document


def transferable(document: dict[str, Any]) -> bool:
    """Check content restrictions in addition to the caller's storage read check."""
    if private_mail_document(document) or is_private_chat_document(document):
        return False
    if document.get("system_state") in {"held", "quarantined", "malware_held", "webdav_deleted"}:
        return False
    attributes = document.get("attributes") or {}
    if not isinstance(attributes, dict):
        return False
    scan = attributes.get("malware_scan")
    return scan is None or (isinstance(scan, dict) and scan.get("verdict") == "clean")
