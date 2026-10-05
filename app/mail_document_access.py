"""Keep managed mail private while reusing the existing case permissions."""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any


def private_mail_document(document: dict[str, Any]) -> bool:
    path = PurePosixPath(str(document.get("last_path") or ""))
    parts = path.parts
    attributes = document.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    return bool(
        attributes.get("mail_case_attachment")
        or (len(parts) >= 2 and parts[0] == "email" and re.fullmatch(r"[0-9a-f]{32}", parts[1]))
        or (len(parts) >= 2 and parts[0] == "MailCases" and re.fullmatch(r"[0-9a-f]{32}", parts[1]))
    )


def mail_document_visible(
    document: dict[str, Any], actor: str, root, *, case_context=False,
    archive_send_case: str = "", archive_send_draft: str = "",
    archive_read_case: str = "", archive_read_id: str = "",
) -> bool:
    if not private_mail_document(document):
        return True
    parts = PurePosixPath(str(document.get("last_path") or "")).parts
    if len(parts) >= 2 and parts[0] == "email":
        if parts[1] == hashlib.sha256(actor.encode("utf-8")).hexdigest()[:32]:
            return True
        if archive_read_case and re.fullmatch(r"[0-9a-f]{128}", archive_read_id.casefold()):
            from .mail_case_store import MailCaseStore
            try:
                case = MailCaseStore(root).get_case(actor, archive_read_case)
            except (KeyError, PermissionError, ValueError):
                return False
            digest = archive_read_id.casefold()
            return bool(
                len(parts) >= 5 and parts[-1] == f"{digest}.eml"
                and parts[1] == hashlib.sha256(case["account_owner"].encode("utf-8")).hexdigest()[:32]
                and parts[2] == case["account_id"]
                and any(row["mail_reference"] == f"sha512:{digest}" for row in case["messages"])
            )
        # An authorized delegated send must archive under the account owner,
        # without granting the delegate general access to that owner's archive.
        if not archive_send_case or len(parts) != 6 or parts[3] != "sent":
            return False
        from .mail_case_store import MailCaseStore
        cases = MailCaseStore(root)
        try:
            case = cases.get_case(actor, archive_send_case)
        except (KeyError, PermissionError, ValueError):
            return False
        owner = case["account_owner"]
        return bool(
            parts[1] == hashlib.sha256(owner.encode("utf-8")).hexdigest()[:32]
            and parts[2] == case["account_id"]
            and "send_request" in case["permissions"]
            and cases.delegations.has_active(owner, case["account_id"], actor)
            and any(row["id"] == archive_send_draft and row["status"] == "sending" for row in case["drafts"])
        )
    attributes = document.get("attributes")
    origin = attributes.get("mail_case_attachment", {}) if isinstance(attributes, dict) else {}
    if not isinstance(origin, dict):
        return False
    owner = str(origin.get("account_owner") or "")
    case_id = str(origin.get("case_id") or (parts[1] if len(parts) >= 2 else ""))
    if not re.fullmatch(r"[0-9a-f]{32}", case_id):
        return False
    from .mail_case_store import MailCaseStore
    try:
        case = MailCaseStore(root).get_case(actor, case_id)
    except (KeyError, PermissionError, ValueError):
        return False
    # Domain routes retain their own compose/send/provenance checks. A case
    # participant does not acquire an independent generic document permission.
    return bool(case_context or actor == (owner or case["account_owner"]))
