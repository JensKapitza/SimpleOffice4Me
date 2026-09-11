"""Authorization helpers for document-backed chat attachments."""
from __future__ import annotations

from typing import Any


CHAT_ATTRIBUTE = "chat_attachment"
PRIVATE_VISIBILITY = "chat"


def attachment_policy(document: dict[str, Any]) -> dict[str, Any]:
    attributes = document.get("attributes")
    if not isinstance(attributes, dict):
        return {}
    value = attributes.get(CHAT_ATTRIBUTE)
    return value if isinstance(value, dict) else {}


def is_private_chat_document(document: dict[str, Any]) -> bool:
    policy = attachment_policy(document)
    return bool(policy and policy.get("visibility") == PRIVATE_VISIBILITY)


def document_visible(document: dict[str, Any], username: str, is_admin: bool) -> bool:
    """Ordinary documents remain visible; chat-private documents are room-scoped."""
    policy = attachment_policy(document)
    if not policy or policy.get("visibility") != PRIVATE_VISIBILITY:
        return True
    if is_admin:
        return True
    allowed = policy.get("allowed_local_users")
    if not isinstance(allowed, list):
        return False
    return str(username or "") in {str(item) for item in allowed if isinstance(item, str)}
