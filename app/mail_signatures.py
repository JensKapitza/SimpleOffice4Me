"""Per-account mail signatures and read-only unread counters.

Signatures are plain-text account settings owned by one user. They are shown
in the compose panel and appended to the sent body; they never become secrets
and never change server state. Unread counters use the read-only IMAP STATUS
command, so no flags are touched.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .document_store import atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .mail_client import _safe_id

MAX_SIGNATURE_CHARS = 2000

_SIGNATURE_SEPARATOR = "-- "


def normalize_signature(value: object) -> str:
    """Validate a user-supplied signature and return its canonical form."""
    text = str(value or "")
    if "\r" in text or "\x00" in text:
        raise ValueError("signature must not contain carriage returns or NUL")
    text = text.strip("\n ")
    if len(text) > MAX_SIGNATURE_CHARS:
        raise ValueError("signature exceeds 2000 characters")
    for line in text.split("\n"):
        if len(line.encode("utf-8")) > 998:
            raise ValueError("signature line exceeds 998 bytes")
    return text


def append_signature(body: str, signature: str) -> str:
    """Append a signature to a plain-text body using the classic separator."""
    clean = normalize_signature(signature)
    if not clean:
        return body
    return f"{body.rstrip()}\n\n{_SIGNATURE_SEPARATOR}\n{clean}\n"


def get_signature(store: Any, actor: str, account_id: str) -> str:
    """Return the stored signature for one owned account, empty when unset."""
    row = store._owned_row(actor, _safe_id(account_id))
    try:
        return normalize_signature(row.get("signature", ""))
    except ValueError:
        return ""


def get_signature_or_default(store: Any, actor: str, account_id: str) -> str:
    """Return the stored signature; never fail rendering or sending on it."""
    try:
        return get_signature(store, actor, account_id)
    except (KeyError, ValueError, OSError):
        return ""


def set_signature(store: Any, actor: str, account_id: str, signature: object) -> str:
    """Store a validated signature on one owned account (audited)."""
    account_id = _safe_id(account_id)
    clean = normalize_signature(signature)
    path = store.accounts_path
    with exclusive_file_lock(path.with_suffix(".lock")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {"version": 1, "accounts": []}
        if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), list):
            raise ValueError("mail account storage is invalid")
        row = next(
            (
                item
                for item in payload["accounts"]
                if isinstance(item, dict)
                and item.get("id") == account_id
                and item.get("owner") == actor
            ),
            None,
        )
        if row is None:
            raise KeyError("mail account does not exist")
        row["signature"] = clean
        row["updated_at"] = utc_now()
        store.control.mkdir(parents=True, exist_ok=True)
        atomic_json_write(path, payload)
    store.history.record(
        "mail_account_signature_updated",
        actor,
        "mail-accounts",
        account_id,
        {"signature_chars": len(clean)},
    )
    return clean


STATUS_UNSEEN_RE = re.compile(rb"UNSEEN\s+(\d+)", re.IGNORECASE)


def parse_status_unseen(response: Any) -> int | None:
    """Extract the UNSEEN count from one IMAP STATUS response."""
    for item in response or []:
        if isinstance(item, tuple):
            item = b" ".join(
                part if isinstance(part, bytes) else str(part).encode("utf-8", "replace")
                for part in item
                if part is not None
            )
        raw = item if isinstance(item, bytes) else str(item).encode("utf-8", "replace")
        match = STATUS_UNSEEN_RE.search(raw)
        if match:
            try:
                return max(0, int(match.group(1)))
            except (TypeError, ValueError):
                return None
    return None
