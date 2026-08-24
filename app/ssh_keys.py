"""Audited per-user SSH public keys for the shell-free SFTP service."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


SUPPORTED_KEY_TYPES = {
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
}
MAX_KEYS_PER_USER = 20
MAX_PUBLIC_KEY_BYTES = 16 * 1024


def _path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / CONTROL_DIR / "ssh-authorized-keys.json"


def _load(root: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "users": {}}
    if not isinstance(value, dict) or not isinstance(value.get("users", {}), dict):
        return {"version": 1, "users": {}}
    return value


def _parse_public_key(value: str) -> tuple[str, bytes, str]:
    line = " ".join(str(value).strip().split())
    parts = line.split(" ", 2)
    if len(parts) < 2 or parts[0] not in SUPPORTED_KEY_TYPES:
        raise ValueError("Nicht unterstützter OpenSSH-Public-Key-Typ.")
    try:
        blob = base64.b64decode(parts[1].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError("Der OpenSSH-Public-Key ist nicht gültig kodiert.") from exc
    if not blob or len(blob) > MAX_PUBLIC_KEY_BYTES:
        raise ValueError("Der OpenSSH-Public-Key ist leer oder zu groß.")
    # The SSH wire blob starts with a uint32 length and the algorithm name.
    if len(blob) < 4:
        raise ValueError("Der OpenSSH-Public-Key ist unvollständig.")
    name_length = int.from_bytes(blob[:4], "big")
    try:
        embedded_type = blob[4:4 + name_length].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Der OpenSSH-Public-Key enthält einen ungültigen Typ.") from exc
    if embedded_type != parts[0]:
        raise ValueError("Schlüsseltyp und Schlüsselinhalt stimmen nicht überein.")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
    return parts[0], blob, fingerprint


def keys_for(root: str | Path, username: str) -> list[dict[str, Any]]:
    records = _load(root).get("users", {}).get(username, [])
    if not isinstance(records, list):
        return []
    now = datetime.now(timezone.utc)
    result = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            expires = datetime.fromisoformat(str(record.get("expires_at", ""))).astimezone(timezone.utc)
        except (TypeError, ValueError):
            expires = datetime.min.replace(tzinfo=timezone.utc)
        result.append({
            "key_id": str(record.get("key_id", "")),
            "label": str(record.get("label", "SSHFS-Schlüssel")),
            "key_type": str(record.get("key_type", "")),
            "fingerprint": str(record.get("fingerprint", "")),
            "scope": "read" if record.get("scope") == "read" else "write",
            "created_at": str(record.get("created_at", "")),
            "expires_at": str(record.get("expires_at", "")),
            "expired": expires <= now,
        })
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def add_key(
    root: str | Path,
    username: str,
    public_key: str,
    *,
    label: str,
    scope: str,
    expires_days: int,
    actor: str,
) -> dict[str, Any]:
    if username != actor:
        raise PermissionError("SSH-Schlüssel dürfen nur für das eigene Konto angelegt werden.")
    label = " ".join(str(label).split())
    if not 1 <= len(label) <= 80:
        raise ValueError("Die Schlüsselbezeichnung muss 1 bis 80 Zeichen lang sein.")
    if scope not in {"read", "write"}:
        raise ValueError("Unbekannter SSH-Rechteumfang.")
    if isinstance(expires_days, bool) or not 1 <= int(expires_days) <= 365:
        raise ValueError("SSH-Schlüssel müssen nach 1 bis 365 Tagen ablaufen.")
    key_type, blob, fingerprint = _parse_public_key(public_key)
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    record = {
        "key_id": secrets.token_hex(12), "label": label,
        "key_type": key_type, "key_blob": base64.b64encode(blob).decode("ascii"),
        "fingerprint": fingerprint, "scope": scope,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=int(expires_days))).isoformat(),
    }
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _load(root)
        users = payload.setdefault("users", {})
        records = users.setdefault(username, [])
        if not isinstance(records, list):
            records = []; users[username] = records
        active = [item for item in records if isinstance(item, dict)]
        if len(active) >= MAX_KEYS_PER_USER:
            raise ValueError(f"Höchstens {MAX_KEYS_PER_USER} SSH-Schlüssel pro Benutzer.")
        if any(hmac.compare_digest(str(item.get("key_blob", "")), record["key_blob"]) for item in active):
            raise ValueError("Dieser SSH-Schlüssel ist bereits hinterlegt.")
        records.append(record)
        payload["version"] = 1
        atomic_json_write(path, payload)
    safe = {key: record[key] for key in ("key_id", "label", "key_type", "fingerprint", "scope", "created_at", "expires_at")}
    RevisionHistory(Path(root)).record("ssh_public_key_added", actor, "ssh-keys", username, safe)
    return safe


def revoke_key(root: str | Path, username: str, key_id: str, *, actor: str) -> bool:
    if username != actor:
        raise PermissionError("SSH-Schlüssel dürfen nur für das eigene Konto widerrufen werden.")
    path = _path(root)
    removed: dict[str, Any] | None = None
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _load(root)
        records = payload.get("users", {}).get(username, [])
        if not isinstance(records, list):
            return False
        kept = []
        for item in records:
            if removed is None and isinstance(item, dict) and hmac.compare_digest(str(item.get("key_id", "")), key_id):
                removed = item
            else:
                kept.append(item)
        payload["users"][username] = kept
        atomic_json_write(path, payload)
    if removed is None:
        return False
    safe = {
        "key_id": key_id, "label": str(removed.get("label", "")),
        "fingerprint": str(removed.get("fingerprint", "")), "revoked_at": utc_now(),
    }
    RevisionHistory(Path(root)).record("ssh_public_key_revoked", actor, "ssh-keys", username, safe)
    return True


def authenticate_key(root: str | Path, username: str, key_type: str, blob: bytes) -> dict[str, str] | None:
    encoded = base64.b64encode(blob).decode("ascii")
    now = datetime.now(timezone.utc)
    records = _load(root).get("users", {}).get(username, [])
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict) or str(record.get("key_type", "")) != key_type:
            continue
        try:
            expires = datetime.fromisoformat(str(record.get("expires_at", ""))).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if expires <= now or not hmac.compare_digest(str(record.get("key_blob", "")), encoded):
            continue
        identity = {
            "username": username, "credential_id": str(record.get("key_id", "")),
            "scope": "read" if record.get("scope") == "read" else "write",
            "path_prefix": "", "authentication": "publickey",
        }
        RevisionHistory(Path(root)).record(
            "ssh_public_key_authenticated", f"sftp:{username}", "ssh-keys", username,
            {"key_id": identity["credential_id"], "fingerprint": str(record.get("fingerprint", ""))},
        )
        return identity
    return None
