"""ClamAV-gated, owner-private storage for mail-case draft attachments."""
from __future__ import annotations

import hashlib
import io
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any

from .attachment_security import AttachmentSecurity, MAX_ATTACHMENT_BYTES, MAX_TOTAL_BYTES
from .document_store import DocumentStore, atomic_json_write
from .v2.contracts import ErrorCode, LogicalObjectId
from .v2.storage_runtime import create_document, result_or_raise, storage_for

_CASE_ID = re.compile(r"^[0-9a-f]{32}$")
_DRAFT_ID = re.compile(r"^[0-9a-f]{32}$")
ATTRIBUTE = "mail_case_attachment"


def _safe_filename(filename: str) -> str:
    value = Path(str(filename or "attachment")).name.replace("/", "_").replace("\\", "_")
    value = "".join(ch for ch in value if ord(ch) >= 32 and ch != "\x7f").strip(" .")
    return value[:180] or "attachment"


def _private_folder(store: DocumentStore, case_id: str, owner: str) -> Path:
    if not _CASE_ID.fullmatch(case_id):
        raise ValueError("invalid mail case id")
    owner = str(owner).strip()
    if not owner:
        raise ValueError("mail account owner is required")
    folder = (store.root / "MailCases" / case_id).resolve()
    folder.relative_to(store.root)
    folder.mkdir(parents=True, exist_ok=True)
    policy_path = store.ensure_folder_policy(folder)
    policy = store._read_json(policy_path, {})
    policy["access_enabled"] = True
    policy["inherit"] = False
    policy["grants"] = [{"principal": owner, "role": "manage"}]
    atomic_json_write(policy_path, policy)
    return folder


class MailCaseAttachmentStore:
    """Reuse the document store and malware gate without exposing case files globally."""

    def __init__(self, root: str | Path, scanner=None):
        self.root = Path(root).expanduser().resolve()
        self.documents = DocumentStore(self.root)
        self.security = AttachmentSecurity(self.root, scanner=scanner)

    def save(
        self,
        content: bytes,
        filename: str,
        content_type: str,
        actor: str,
        *,
        case_id: str,
        draft_id: str,
        owner: str,
    ) -> dict[str, Any]:
        actor = str(actor).strip()
        if not actor:
            raise ValueError("a named actor is required")
        if not _DRAFT_ID.fullmatch(str(draft_id)):
            raise ValueError("invalid mail case draft id")
        payload = bytes(content)
        if not payload:
            raise ValueError("empty draft attachments are not accepted")
        if len(payload) > MAX_ATTACHMENT_BYTES:
            raise ValueError("draft attachment exceeds the 50 MiB limit")
        safe_name = _safe_filename(filename)
        mime = str(content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream").strip().casefold()
        if "/" not in mime or any(ch in mime for ch in "\r\n") or len(mime) > 200:
            mime = "application/octet-stream"

        scan = self.security.scan_webdav_upload(
            payload,
            actor,
            f"MailCases/{case_id}/{draft_id}/{safe_name}",
            MAX_TOTAL_BYTES,
            source_type="mail-case-draft",
        )
        if scan.get("verdict") != "clean":
            raise ValueError("draft attachment failed malware scan")

        folder = _private_folder(self.documents, case_id, owner)
        relative = (
            folder / f"{draft_id[:8]}-{uuid.uuid4().hex[:8]}-{safe_name}"
        ).relative_to(self.documents.root).as_posix()
        document = create_document(
            self.root, actor, relative, payload, max_bytes=MAX_ATTACHMENT_BYTES
        )
        digest = hashlib.sha256(payload).hexdigest()
        if str(document.get("sha256", "")).casefold() != digest:
            raise RuntimeError("stored draft attachment checksum mismatch")

        metadata = self.documents.update_metadata(
            document["document_id"],
            attributes={
                ATTRIBUTE: {
                    "schema": 1,
                    "case_id": case_id,
                    "draft_id": draft_id,
                    "account_owner": owner,
                    "created_by": actor,
                    "original_filename": safe_name,
                    "content_type": mime,
                    "sha256": digest,
                    "scan_id": str(scan.get("scan_id", "")),
                },
                "malware_scan": scan,
            },
            tags=[*document.get("tags", []), "mail-case-attachment"],
            author=actor,
        )
        return {
            "document_id": metadata["document_id"],
            "filename": safe_name,
            "content_type": mime,
            "size": len(payload),
            "sha256": digest,
            "scan_id": str(scan.get("scan_id", "")),
        }

    def read(
        self,
        *,
        case_id: str,
        draft_id: str,
        attachment: dict[str, Any],
        actor: str = "",
    ) -> bytes:
        metadata = self.documents.get_document(str(attachment.get("document_id", "")))
        origin = metadata.get("attributes", {}).get(ATTRIBUTE, {})
        scan = metadata.get("attributes", {}).get("malware_scan", {})
        expected = str(attachment.get("sha256", "")).casefold()
        if (
            not isinstance(origin, dict)
            or origin.get("case_id") != case_id
            or origin.get("draft_id") != draft_id
            or str(origin.get("sha256", "")).casefold() != expected
            or not isinstance(scan, dict)
            or scan.get("verdict") != "clean"
            or str(scan.get("sha256", "")).casefold() != expected
            or str(scan.get("scan_id", "")) != str(attachment.get("scan_id", ""))
        ):
            raise PermissionError("draft attachment provenance validation failed")
        size = attachment.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_ATTACHMENT_BYTES:
            raise ValueError("invalid draft attachment size")
        # Verify the whole object while retaining at most the declared size plus
        # one byte. No provisional bytes leave this service before validation.
        with io.BytesIO() as target:
            result = storage_for(self.root, actor or str(origin.get("created_by", ""))).copy_verified_range_to(
                LogicalObjectId(metadata["document_id"]), target, start=0, length=size + 1,
            )
            if result.error and result.error.code is ErrorCode.INTEGRITY_ERROR:
                raise ValueError("draft attachment changed after malware scan")
            stored = result_or_raise(result)
            payload = target.getvalue()
        if stored.size != size or len(payload) != size:
            raise ValueError("draft attachment size changed after malware scan")
        if stored.version.casefold() != expected or hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError("draft attachment changed after malware scan")
        return payload
