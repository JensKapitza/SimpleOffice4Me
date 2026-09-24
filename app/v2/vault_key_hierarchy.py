"""V2 key hierarchy helpers for the password vault.

The vault payload key is treated as a 256-bit master key. Password protection
and offline recovery use the central V2 CryptoService; no vault-specific
cryptographic primitive is introduced here.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import asdict, dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import CRYPTO_FORMAT, CryptoService, ProtectedMasterKey


PASSWORD_PROTECTION_FORMAT = "simpleoffice-v2-vault-password-protection/v1"
RECOVERY_BUNDLE_FORMAT = "simpleoffice-v2-vault-recovery/v1"
MAX_RECOVERY_BUNDLE_BYTES = 256 * 1024
VAULT_KEY_CHECK = b"simpleoffice-password-vault:key-check:v1"


@dataclass(frozen=True, repr=False)
class VaultRecoveryMaterial:
    recovery_key: bytes
    recovery_bundle: bytes


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(bytes(value)).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    text = str(value or "")
    if not text or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for char in text
    ):
        raise ValueError("invalid vault recovery base64url field")
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        return base64.b64decode(text + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid vault recovery base64url field") from exc


def vault_key_check_aad(user_id: str) -> bytes:
    return f"simpleoffice-password-vault:key-check:v1:{str(user_id)}".encode("utf-8")


def _protected_to_dict(value: ProtectedMasterKey) -> dict[str, Any]:
    return asdict(value)


def protected_from_dict(value: Any) -> ProtectedMasterKey:
    if not isinstance(value, dict):
        raise ValueError("protected vault key record is invalid")
    try:
        protected = ProtectedMasterKey(
            format=str(value["format"]),
            method=str(value["method"]),
            salt=str(value.get("salt") or ""),
            nonce=str(value["nonce"]),
            ciphertext=str(value["ciphertext"]),
            time_cost=int(value.get("time_cost") or 0),
            memory_kib=int(value.get("memory_kib") or 0),
            parallelism=int(value.get("parallelism") or 0),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("protected vault key record is incomplete") from exc
    if protected.format != CRYPTO_FORMAT:
        raise ValueError("unsupported protected vault key format")
    return protected


def encode_password_protection(vault_key: bytes, password: str) -> str:
    protected = CryptoService().protect_master_key_with_password(
        bytes(vault_key),
        str(password),
    )
    payload = {
        "format": PASSWORD_PROTECTION_FORMAT,
        "protected_key": _protected_to_dict(protected),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_password_protection(value: str) -> ProtectedMasterKey:
    try:
        payload = json.loads(str(value or ""))
    except json.JSONDecodeError as exc:
        raise ValueError("vault password protection record is invalid") from exc
    if not isinstance(payload, dict) or payload.get("format") != PASSWORD_PROTECTION_FORMAT:
        raise ValueError("unsupported vault password protection format")
    protected = protected_from_dict(payload.get("protected_key"))
    if protected.method != "argon2id-aes256gcm":
        raise ValueError("unsupported vault password protection method")
    return protected


def unlock_password_protection(value: str, password: str) -> bytes:
    protected = decode_password_protection(value)
    return CryptoService().unlock_master_key_with_password(protected, str(password))


def encode_recovery_record(vault_key: bytes, recovery_key: bytes) -> str:
    protected = CryptoService().protect_master_key_with_recovery_key(
        bytes(vault_key),
        bytes(recovery_key),
    )
    return json.dumps(
        _protected_to_dict(protected),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_recovery_record(value: str) -> ProtectedMasterKey:
    try:
        payload = json.loads(str(value or ""))
    except json.JSONDecodeError as exc:
        raise ValueError("vault recovery record is invalid") from exc
    protected = protected_from_dict(payload)
    if protected.method != "recovery-aes256gcm":
        raise ValueError("unsupported vault recovery method")
    return protected


def unlock_recovery_record(value: str, recovery_key: bytes) -> bytes:
    return CryptoService().unlock_master_key_with_recovery_key(
        decode_recovery_record(value),
        bytes(recovery_key),
    )


def recovery_bundle(
    user_id: str,
    recovery_record: str,
    *,
    key_check_nonce: bytes,
    key_check_ciphertext: bytes,
) -> bytes:
    user = str(user_id or "").strip()
    if not user or len(user) > 160:
        raise ValueError("invalid vault recovery user")
    decode_recovery_record(recovery_record)
    nonce = bytes(key_check_nonce)
    ciphertext = bytes(key_check_ciphertext)
    if len(nonce) != 12 or not ciphertext:
        raise ValueError("vault key check is incomplete")
    payload = {
        "format": RECOVERY_BUNDLE_FORMAT,
        "user_id": user,
        "recovery": json.loads(recovery_record),
        "key_check_nonce": _b64(nonce),
        "key_check_ciphertext": _b64(ciphertext),
    }
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_RECOVERY_BUNDLE_BYTES:
        raise ValueError("vault recovery bundle is too large")
    return encoded


def create_recovery_material(
    user_id: str,
    vault_key: bytes,
    *,
    key_check_nonce: bytes,
    key_check_ciphertext: bytes,
) -> tuple[str, VaultRecoveryMaterial]:
    crypto = CryptoService()
    recovery_key = crypto.generate_recovery_key()
    record = encode_recovery_record(vault_key, recovery_key)
    bundle = recovery_bundle(
        user_id,
        record,
        key_check_nonce=key_check_nonce,
        key_check_ciphertext=key_check_ciphertext,
    )
    return record, VaultRecoveryMaterial(
        recovery_key=recovery_key,
        recovery_bundle=bundle,
    )


def recover_vault_key_from_bundle(bundle: bytes, recovery_key: bytes) -> bytes:
    raw = bytes(bundle)
    if not raw or len(raw) > MAX_RECOVERY_BUNDLE_BYTES:
        raise ValueError("vault recovery bundle size is invalid")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("vault recovery bundle is invalid") from exc
    if not isinstance(payload, dict) or payload.get("format") != RECOVERY_BUNDLE_FORMAT:
        raise ValueError("unsupported vault recovery bundle format")
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id or len(user_id) > 160:
        raise ValueError("vault recovery bundle user is invalid")
    recovery = payload.get("recovery")
    record = json.dumps(
        recovery,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    vault_key = unlock_recovery_record(record, recovery_key)
    nonce = _unb64(str(payload.get("key_check_nonce") or ""))
    ciphertext = _unb64(str(payload.get("key_check_ciphertext") or ""))
    if len(nonce) != 12 or not ciphertext:
        raise ValueError("vault recovery bundle key check is invalid")
    try:
        marker = AESGCM(vault_key).decrypt(
            nonce,
            ciphertext,
            vault_key_check_aad(user_id),
        )
    except (InvalidTag, ValueError, TypeError) as exc:
        raise ValueError("vault recovery authentication failed") from exc
    if marker != VAULT_KEY_CHECK:
        raise ValueError("vault recovery authentication failed")
    return vault_key
