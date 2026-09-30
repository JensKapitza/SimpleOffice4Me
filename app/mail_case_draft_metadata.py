"""Validation helpers for mail-case draft attachment metadata."""

from pathlib import Path

MAX_DRAFT_ATTACHMENT_BYTES = 50 * 1024 * 1024


def attachment_metadata(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("draft attachment metadata must be an object")
    document_id = str(value.get("document_id", "")).strip()
    filename = Path(str(value.get("filename", "")).replace("\\", "/")).name.strip(" .")
    content_type = str(value.get("content_type", "application/octet-stream")).strip()
    sha256 = str(value.get("sha256", "")).strip().casefold()
    scan_id = str(value.get("scan_id", "")).strip()
    try:
        size = int(value.get("size", -1))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid draft attachment size") from exc
    if not document_id or len(document_id) > 300:
        raise ValueError("draft attachment document id is required")
    if not filename or len(filename) > 180 or any(ord(ch) < 32 or ord(ch) == 127 for ch in filename):
        raise ValueError("invalid draft attachment filename")
    if (
        "/" not in content_type or len(content_type) > 200
        or any(ch in content_type for ch in "\r\n")
    ):
        raise ValueError("invalid draft attachment content type")
    if size < 0 or size > MAX_DRAFT_ATTACHMENT_BYTES:
        raise ValueError("draft attachment exceeds the 50 MiB limit")
    if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
        raise ValueError("draft attachment SHA-256 is invalid")
    if not scan_id or len(scan_id) > 300:
        raise ValueError("draft attachment malware scan id is required")
    return {
        "document_id": document_id,
        "filename": filename,
        "content_type": content_type,
        "size": size,
        "sha256": sha256,
        "scan_id": scan_id,
    }
