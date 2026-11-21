"""Per-message attachment folders for archived EML mail.

An archived message keeps its byte-identical EML. Optionally, its attachments
are materialized into a logical ``attachments/`` folder next to the EML using
the generic, scanner-gated :class:`AttachmentSecurity` pipeline: safe names,
deduplication, quarantine on infection, document links back to the source.
No mail-specific storage or retention logic lives here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .attachment_security import AttachmentSecurity
from .mail_archive_storage import archive_document, archive_path_by_id


def archive_message_attachments(
    store: Any,
    actor: str,
    account_id: str,
    archive_id: str,
    *,
    scanner: Any | None = None,
) -> dict[str, Any]:
    """Extract every attachment of one archived EML into its folder.

    Returns the relative attachment folder plus one row per MIME part
    (released ``document_id`` or quarantine ``action``). Raises on unknown
    messages, permission problems, or changed source bytes.
    """
    path = archive_path_by_id(store, actor, account_id, archive_id)
    document = archive_document(store, actor, account_id, path)
    security = (
        AttachmentSecurity(store.root, scanner=scanner)
        if scanner is not None
        else AttachmentSecurity(store.root)
    )
    manifest = security.preview_eml(document["document_id"], actor)
    folder = Path(str(document.get("last_path", path))).parent / "attachments"
    parts = [row["part"] for row in manifest.get("attachments", [])]
    if not parts:
        return {"attachment_folder": folder.as_posix(), "parts": [], "total": 0}
    # extract() yields exactly one row per chosen part in ascending part
    # order; zipping keeps the stable part identity of duplicate names.
    released = security.extract(manifest["manifest_id"], sorted(set(parts)), actor, idempotent=True)
    if len(released) != len(set(parts)):
        raise ValueError("attachment extraction returned an incomplete result")
    rows = []
    for index, row in zip(sorted(set(parts)), released):
        rows.append(
            {
                "part": index,
                "filename": row.get("filename"),
                "size": row.get("size"),
                "sha256": row.get("sha256"),
                "verdict": row.get("verdict"),
                "action": row.get("action"),
                "document_id": row.get("document_id"),
                "quarantine_id": row.get("quarantine_id"),
            }
        )
    return {"attachment_folder": folder.as_posix(), "parts": rows, "total": len(rows)}
