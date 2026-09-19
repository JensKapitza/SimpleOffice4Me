"""Central V2 cryptography service built from established primitives.

The module deliberately exposes envelope operations instead of allowing callers
to choose algorithms, nonces or KDF parameters ad hoc.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Final

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


AES_KEY_BYTES: Final = 32
GCM_NONCE_BYTES: Final = 12
ARGON2_SALT_BYTES: Final = 16
ARGON2_TIME_COST: Final = 3
ARGON2_MEMORY_KIB: Final = 65536
ARGON2_PARALLELISM: Final = 4
ARGON2_HASH_BYTES: Final = 32
CRYPTO_FORMAT: Final = "simpleoffice-v2-envelope/v1"

_PAYLOAD_DOMAIN = b"simpleoffice:v2:payload:"
_WRAP_DOMAIN = b"simpleoffice:v2:key-wrap:"
_MASTER_PASSWORD_DOMAIN = b"simpleoffice:v2:master-password"
_MASTER_RECOVERY_DOMAIN = b"simpleoffice:v2:master-recovery"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    text = str(value or "")
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except Exception as exc:
        raise ValueError("invalid base64url cryptographic field") from exc


def _key(value: bytes, label: str) -> bytes:
    value = bytes(value)
    if len(value) != AES_KEY_BYTES:
        raise ValueError(f"{label} must be 32 bytes")
    return value


def _purpose(value: str) -> bytes:
    encoded = str(value or "").strip().encode("utf-8")
    if not encoded or len(encoded) > 512:
        raise ValueError("cryptographic purpose must be 1..512 UTF-8 bytes")
    return encoded


@dataclass(frozen=True)
class WrappedKey:
    format: str
    nonce: str
    ciphertext: str


@dataclass(frozen=True)
class EncryptedPayload:
    format: str
    purpose: str
    nonce: str
    ciphertext: str
    wrapped_key: WrappedKey


@dataclass(frozen=True)
class ProtectedMasterKey:
    format: str
    method: str
    salt: str
    nonce: str
    ciphertext: str
    time_cost: int = 0
    memory_kib: int = 0
    parallelism: int = 0


class CryptoService:
    """One policy point for V2 envelope encryption and master-key protection."""

    @staticmethod
    def generate_master_key() -> bytes:
        return os.urandom(AES_KEY_BYTES)

    @staticmethod
    def generate_recovery_key() -> bytes:
        return os.urandom(AES_KEY_BYTES)

    @staticmethod
    def _wrap_key(content_key: bytes, wrapping_key: bytes, purpose: str) -> WrappedKey:
        content_key = _key(content_key, "content key")
        wrapping_key = _key(wrapping_key, "wrapping key")
        purpose_bytes = _purpose(purpose)
        nonce = os.urandom(GCM_NONCE_BYTES)
        ciphertext = AESGCM(wrapping_key).encrypt(
            nonce,
            content_key,
            _WRAP_DOMAIN + purpose_bytes,
        )
        return WrappedKey(CRYPTO_FORMAT, _b64(nonce), _b64(ciphertext))

    @staticmethod
    def _unwrap_key(wrapped: WrappedKey, wrapping_key: bytes, purpose: str) -> bytes:
        if wrapped.format != CRYPTO_FORMAT:
            raise ValueError("unsupported wrapped-key format")
        wrapping_key = _key(wrapping_key, "wrapping key")
        purpose_bytes = _purpose(purpose)
        try:
            value = AESGCM(wrapping_key).decrypt(
                _unb64(wrapped.nonce),
                _unb64(wrapped.ciphertext),
                _WRAP_DOMAIN + purpose_bytes,
            )
        except InvalidTag as exc:
            raise ValueError("wrapped key authentication failed") from exc
        return _key(value, "unwrapped content key")

    def encrypt(self, plaintext: bytes, master_key: bytes, *, purpose: str) -> EncryptedPayload:
        master_key = _key(master_key, "master key")
        purpose_bytes = _purpose(purpose)
        content_key = os.urandom(AES_KEY_BYTES)
        nonce = os.urandom(GCM_NONCE_BYTES)
        ciphertext = AESGCM(content_key).encrypt(
            nonce,
            bytes(plaintext),
            _PAYLOAD_DOMAIN + purpose_bytes,
        )
        wrapped = self._wrap_key(content_key, master_key, purpose)
        return EncryptedPayload(
            format=CRYPTO_FORMAT,
            purpose=purpose,
            nonce=_b64(nonce),
            ciphertext=_b64(ciphertext),
            wrapped_key=wrapped,
        )

    def decrypt(self, payload: EncryptedPayload, master_key: bytes) -> bytes:
        if payload.format != CRYPTO_FORMAT:
            raise ValueError("unsupported encrypted-payload format")
        master_key = _key(master_key, "master key")
        purpose_bytes = _purpose(payload.purpose)
        content_key = self._unwrap_key(payload.wrapped_key, master_key, payload.purpose)
        try:
            return AESGCM(content_key).decrypt(
                _unb64(payload.nonce),
                _unb64(payload.ciphertext),
                _PAYLOAD_DOMAIN + purpose_bytes,
            )
        except InvalidTag as exc:
            raise ValueError("payload authentication failed") from exc

    def rewrap(self, payload: EncryptedPayload, old_master_key: bytes, new_master_key: bytes) -> EncryptedPayload:
        content_key = self._unwrap_key(payload.wrapped_key, old_master_key, payload.purpose)
        return EncryptedPayload(
            format=payload.format,
            purpose=payload.purpose,
            nonce=payload.nonce,
            ciphertext=payload.ciphertext,
            wrapped_key=self._wrap_key(content_key, new_master_key, payload.purpose),
        )

    @staticmethod
    def _password_key(password: str, salt: bytes) -> bytes:
        secret = str(password or "").encode("utf-8")
        if len(secret) < 1:
            raise ValueError("password must not be empty")
        return hash_secret_raw(
            secret=secret,
            salt=salt,
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_KIB,
            parallelism=ARGON2_PARALLELISM,
            hash_len=ARGON2_HASH_BYTES,
            type=Type.ID,
        )

    def protect_master_key_with_password(self, master_key: bytes, password: str) -> ProtectedMasterKey:
        master_key = _key(master_key, "master key")
        salt = os.urandom(ARGON2_SALT_BYTES)
        password_key = self._password_key(password, salt)
        nonce = os.urandom(GCM_NONCE_BYTES)
        ciphertext = AESGCM(password_key).encrypt(
            nonce,
            master_key,
            _MASTER_PASSWORD_DOMAIN,
        )
        return ProtectedMasterKey(
            format=CRYPTO_FORMAT,
            method="argon2id-aes256gcm",
            salt=_b64(salt),
            nonce=_b64(nonce),
            ciphertext=_b64(ciphertext),
            time_cost=ARGON2_TIME_COST,
            memory_kib=ARGON2_MEMORY_KIB,
            parallelism=ARGON2_PARALLELISM,
        )

    def unlock_master_key_with_password(self, protected: ProtectedMasterKey, password: str) -> bytes:
        if protected.format != CRYPTO_FORMAT or protected.method != "argon2id-aes256gcm":
            raise ValueError("unsupported protected-master-key format")
        if (
            protected.time_cost != ARGON2_TIME_COST
            or protected.memory_kib != ARGON2_MEMORY_KIB
            or protected.parallelism != ARGON2_PARALLELISM
        ):
            raise ValueError("unsupported master-key KDF policy")
        password_key = self._password_key(password, _unb64(protected.salt))
        try:
            value = AESGCM(password_key).decrypt(
                _unb64(protected.nonce),
                _unb64(protected.ciphertext),
                _MASTER_PASSWORD_DOMAIN,
            )
        except InvalidTag as exc:
            raise ValueError("master-key password authentication failed") from exc
        return _key(value, "master key")

    def protect_master_key_with_recovery_key(self, master_key: bytes, recovery_key: bytes) -> ProtectedMasterKey:
        master_key = _key(master_key, "master key")
        recovery_key = _key(recovery_key, "recovery key")
        nonce = os.urandom(GCM_NONCE_BYTES)
        ciphertext = AESGCM(recovery_key).encrypt(
            nonce,
            master_key,
            _MASTER_RECOVERY_DOMAIN,
        )
        return ProtectedMasterKey(
            format=CRYPTO_FORMAT,
            method="recovery-aes256gcm",
            salt="",
            nonce=_b64(nonce),
            ciphertext=_b64(ciphertext),
        )

    def unlock_master_key_with_recovery_key(self, protected: ProtectedMasterKey, recovery_key: bytes) -> bytes:
        if protected.format != CRYPTO_FORMAT or protected.method != "recovery-aes256gcm":
            raise ValueError("unsupported recovery-master-key format")
        recovery_key = _key(recovery_key, "recovery key")
        try:
            value = AESGCM(recovery_key).decrypt(
                _unb64(protected.nonce),
                _unb64(protected.ciphertext),
                _MASTER_RECOVERY_DOMAIN,
            )
        except InvalidTag as exc:
            raise ValueError("recovery-key authentication failed") from exc
        return _key(value, "master key")
