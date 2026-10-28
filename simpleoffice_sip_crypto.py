"""SIP digest verifier storage and key helpers shared by app and worker."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
from pathlib import Path
from typing import Mapping

_HA1_PREFIX = "enc:sip-ha1:v1:"
_HA1_STORAGE_CONTEXT = b"SimpleOffice4Me/sip-ha1-storage/v1"
_EXTENSION_RE = re.compile(r"^[0-9]{1,12}$")
_HA1_FORMATS = {"MD5": re.compile(r"^[0-9a-f]{32}$"), "SHA-256": re.compile(r"^[0-9a-f]{64}$")}


def _master_key(value: str | bytes) -> bytes:
    key = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    if len(key) < 32:
        raise ValueError("SIP installation key is too short")
    return key


def _algorithm(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in _HA1_FORMATS:
        raise ValueError("unsupported SIP Digest algorithm")
    return normalized


def sip_digest_hash(data: bytes, algorithm: str) -> str:
    normalized = _algorithm(algorithm)
    if normalized == "SHA-256":
        return hashlib.sha256(data).hexdigest()
    # RFC 3261 legacy endpoints still require MD5; SHA-256 is advertised first.
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def sip_digest_ha1(username: str, realm: str, password: str, algorithm: str) -> str:
    """Calculate RFC 8760 HA1, retaining MD5 for configured legacy endpoints."""
    if not username or not realm or not password:
        raise ValueError("SIP Digest requires username, realm and password")
    normalized = _algorithm(algorithm)
    material = f"{username}:{realm}:{password}".encode("utf-8")
    # RFC 3261 legacy endpoints still require MD5; newer endpoints are offered SHA-256 first.
    return sip_digest_hash(material, normalized)


def sip_digest_verifiers(username: str, realm: str, password: str) -> dict[str, str]:
    return {
        "SHA-256": sip_digest_ha1(username, realm, password, "SHA-256"),
        "MD5": sip_digest_ha1(username, realm, password, "MD5"),
    }


def _validate_verifiers(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("SIP Digest verifier set is empty")
    verifiers: dict[str, str] = {}
    for algorithm, digest in value.items():
        normalized = _algorithm(algorithm)
        clean_digest = str(digest or "").casefold()
        if not _HA1_FORMATS[normalized].fullmatch(clean_digest):
            raise ValueError("SIP Digest verifier is malformed")
        verifiers[normalized] = clean_digest
    return verifiers


def _ha1_key(master_key: str | bytes) -> bytes:
    return hashlib.sha256(_HA1_STORAGE_CONTEXT + b"\0" + _master_key(master_key)).digest()


def encrypt_sip_digest_verifiers(
    extension: str,
    verifiers: Mapping[str, str],
    master_key: str | bytes,
) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    extension = str(extension or "")
    if not _EXTENSION_RE.fullmatch(extension):
        raise ValueError("invalid SIP extension for verifier encryption")
    aad = _HA1_STORAGE_CONTEXT + b"\0" + extension.encode("ascii")
    payload = json.dumps(_validate_verifiers(verifiers), sort_keys=True, separators=(",", ":")).encode("ascii")
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_ha1_key(master_key)).encrypt(nonce, payload, aad)
    token = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii").rstrip("=")
    return _HA1_PREFIX + token


def decrypt_sip_digest_verifiers(
    extension: str,
    value: str,
    master_key: str | bytes,
) -> dict[str, str]:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not isinstance(value, str) or not value.startswith(_HA1_PREFIX):
        raise ValueError("SIP Digest verifier is not encrypted")
    extension = str(extension or "")
    if not _EXTENSION_RE.fullmatch(extension):
        raise ValueError("invalid SIP extension for verifier decryption")
    try:
        token = value.removeprefix(_HA1_PREFIX)
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        if len(raw) < 28:
            raise ValueError("SIP Digest verifier payload is truncated")
        aad = _HA1_STORAGE_CONTEXT + b"\0" + extension.encode("ascii")
        plaintext = AESGCM(_ha1_key(master_key)).decrypt(raw[:12], raw[12:], aad)
        decoded = json.loads(plaintext.decode("ascii"))
        return _validate_verifiers(decoded)
    except (InvalidTag, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("SIP Digest verifier could not be decrypted") from exc


def decrypt_sip_profile_secret(value: str, extension: str, master_key: str | bytes) -> str:
    """Decrypt the existing ``security_controls.protect_value`` format."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not isinstance(value, str) or not value.startswith("enc:v1:"):
        raise ValueError("SIP profile secret is not encrypted")
    extension = str(extension or "")
    if not _EXTENSION_RE.fullmatch(extension):
        raise ValueError("invalid SIP extension for profile secret")
    aad = f"simpleoffice:sip-profile:{extension}:v1".encode("utf-8")
    key = hashlib.sha256(aad + b"\0" + _master_key(master_key)).digest()
    try:
        raw = base64.urlsafe_b64decode(value.removeprefix("enc:v1:").encode("ascii"))
        if len(raw) < 28:
            raise ValueError("SIP profile secret payload is truncated")
        return AESGCM(key).decrypt(raw[:12], raw[12:], aad).decode("utf-8")
    except (InvalidTag, UnicodeError, ValueError) as exc:
        raise ValueError("SIP profile secret could not be decrypted") from exc


def encrypt_sip_profile_secret(value: str, extension: str, master_key: str | bytes) -> str:
    """Create the same purpose-bound SIP secret token used by the Flask admin."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not value:
        raise ValueError("SIP profile secret is empty")
    extension = str(extension or "")
    if not _EXTENSION_RE.fullmatch(extension):
        raise ValueError("invalid SIP extension for profile secret")
    aad = f"simpleoffice:sip-profile:{extension}:v1".encode("utf-8")
    key = hashlib.sha256(aad + b"\0" + _master_key(master_key)).digest()
    nonce = secrets.token_bytes(12)
    payload = nonce + AESGCM(key).encrypt(nonce, value.encode("utf-8"), aad)
    return "enc:v1:" + base64.urlsafe_b64encode(payload).decode("ascii")


def load_sip_installation_key(path: str | Path) -> str:
    secret_path = Path(path).expanduser()
    if secret_path.is_symlink():
        raise RuntimeError("SIP installation key must not be a symbolic link")
    try:
        value = secret_path.read_text(encoding="utf-8").strip()
        mode = stat.S_IMODE(secret_path.stat().st_mode)
    except OSError as exc:
        raise RuntimeError("SIP installation key cannot be read") from exc
    if len(value) < 32:
        raise RuntimeError("SIP installation key is missing or too short")
    if os.name == "posix" and mode & 0o077:
        raise RuntimeError("SIP installation key permissions are too broad")
    return value


def sip_installation_key_path() -> Path:
    """Resolve the same key file used by Flask's ``app.instance_path``."""
    configured = os.environ.get("SIMPLEOFFICE_SECRET_KEY_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent / "app" / "instance" / "session-secret"


def migrate_legacy_sip_verifiers(db_path: str | Path, master_key: str | bytes) -> int:
    """Atomically encrypt legacy HA1 values and add SHA-256 using the stored secret."""
    path = Path(db_path)
    if not path.is_file():
        return 0
    db = sqlite3.connect(path, timeout=5)
    try:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telephony_profile'"
        ).fetchone()
        if table is None:
            return 0
        columns = {str(row[1]) for row in db.execute("PRAGMA table_info(telephony_profile)")}
        if not {"extension", "secret_enc", "digest_ha1"}.issubset(columns):
            return 0
        rows = db.execute(
            "SELECT extension,digest_ha1 FROM telephony_profile WHERE digest_ha1<>''"
        ).fetchall()
        if not rows:
            return 0
        db.execute("BEGIN IMMEDIATE")
        realm_row = db.execute(
            "SELECT value FROM telephony_setting WHERE key='realm'"
        ).fetchone() if "telephony_setting" in {
            str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        } else None
        realm = str(realm_row[0]) if realm_row and realm_row[0] else "simpleoffice.local"
        rows = db.execute(
            "SELECT extension,secret_enc,digest_ha1 FROM telephony_profile WHERE digest_ha1<>''"
        ).fetchall()
        migrated = 0
        for extension, secret_enc, stored in rows:
            extension, stored = str(extension), str(stored)
            if stored.startswith(_HA1_PREFIX):
                decrypt_sip_digest_verifiers(extension, stored, master_key)
                continue
            if not _HA1_FORMATS["MD5"].fullmatch(stored):
                raise ValueError("legacy SIP Digest verifier is malformed")
            password = decrypt_sip_profile_secret(str(secret_enc or ""), extension, master_key)
            md5 = sip_digest_ha1(extension, realm, password, "MD5")
            if not secrets.compare_digest(md5, stored.casefold()):
                raise ValueError("legacy SIP Digest verifier does not match its encrypted profile secret")
            verifiers = sip_digest_verifiers(extension, realm, password)
            encrypted = encrypt_sip_digest_verifiers(extension, verifiers, master_key)
            updated = db.execute(
                "UPDATE telephony_profile SET digest_ha1=? WHERE extension=? AND digest_ha1=?",
                (encrypted, extension, stored),
            )
            if updated.rowcount != 1:
                raise RuntimeError("SIP Digest verifier changed during migration")
            migrated += 1
        db.commit()
        return migrated
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
