"""ClamAV-gated downloads for attachments stored inside archived EML messages."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .attachment_security import AttachmentSecurity
from .document_store import CONTROL_DIR, utc_now


def latest_scan_for_sha256(root: str | Path, digest: str) -> dict[str, Any] | None:
    """Return the newest persisted file scan for a payload SHA-256."""
    digest = str(digest).strip().casefold()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        return None
    registry = Path(root).resolve() / CONTROL_DIR / "malware-scan.json"
    try:
        rows = json.loads(registry.read_text(encoding="utf-8")).get("scans", [])
    except (OSError, json.JSONDecodeError):
        return None
    for row in reversed(rows):
        if row.get("sha256") == digest and row.get("verdict") in {"clean", "infected", "error"}:
            result = dict(row)
            scanned = str(result.get("scanned_at", ""))
            result["clamav_tag"] = f"CLAMAV:{scanned[:10]}" if scanned else "CLAMAV"
            return result
    return None


def scan_attachment_for_download(
    root: str | Path,
    actor: str,
    account_id: str,
    archive_id: str,
    filename: str,
    payload: bytes,
) -> dict[str, Any]:
    """Scan an archived-mail attachment immediately before download.

    Clean payloads are removed from the private scan staging directory after the
    verdict has been recorded. Infected/error payloads remain isolated and are
    never returned to the browser.
    """
    security = AttachmentSecurity(root)
    staging = security.control / "mail-download-quarantine"
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(staging, 0o700)
    except OSError:
        pass

    scan_id = uuid.uuid4().hex
    pending = staging / f"{scan_id}.pending"
    with pending.open("xb") as handle:
        os.chmod(pending, 0o600)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())

    base = {
        "scan_id": scan_id,
        "event_type": "file_scan",
        "scanned_at": utc_now(),
        "actor": actor,
        "source_type": "mail-archive-download",
        "account_id": account_id,
        "archive_id": archive_id,
        "filename": Path(filename).name[:180],
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    try:
        verdict = security.scanner.scan(pending)
        record = {**base, **asdict(verdict)}
        if verdict.verdict == "infected":
            retained = staging / f"{scan_id}.infected"
            pending.replace(retained)
            record.update({"action": "blocked_quarantined", "quarantine_id": retained.name})
            security._record_scan(record)
            return record
        if verdict.verdict != "clean":
            raise RuntimeError("ClamAV returned an unsupported verdict")
        record["action"] = "allowed_download"
        security._record_scan(record)
        pending.unlink(missing_ok=True)
        return record
    except Exception as exc:
        error_path = staging / f"{scan_id}.error"
        if pending.exists():
            pending.replace(error_path)
        record = {
            **base,
            "verdict": "error",
            "engine": "",
            "detail": security.safe_error_code(exc),
            "action": "scan_failed_quarantined",
            "quarantine_id": error_path.name if error_path.exists() else "",
        }
        try:
            security._record_scan(record)
        except OSError:
            pass
        return record
