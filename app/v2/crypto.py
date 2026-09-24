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
CHUNK_CRYPTO_FORMAT: Final = "simpleoffice-v2-chunk-aead/v1"
MAX_CRYPTO_CHUNK_BYTES: Final = 64 * 1024 * 1024

_PAYLOAD_DOMAIN = b"simpleoffice:v2:payload:"
_WRAP_DOMAIN = b"simpleoffice:v2:key-wrap:"
_MASTER_PASSWORD_DOMAIN = b"simpleoffice:v2:master-password"
_MASTER_RECOVERY_DOMAIN = b"simpleoffice:v2:master-recovery"
_MASTER_TRUSTEE_DOMAIN = b"simpleoffice:v2:master-trustee"
_CHUNK_DOMAIN = b"simpleoffice:v2:chunk:"


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


@dataclass(frozen=True)
class EncryptedChunk:
    format: str
    purpose: str
    index: int
    plaintext_size: int
    nonce: str
    ciphertext: str


class CryptoService:
    """One policy point for V2 envelope encryption and master-key protection."""

    @staticmethod
    def generate_master_key() -> bytes:
        return os.urandom(AES_KEY_BYTES)

    @staticmethod
    def generate_recovery_key() -> bytes:
        return os.urandom(AES_KEY_BYTES)

    @staticmethod
    def generate_trustee_key() -> bytes:
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

    def begin_chunk_encryption(self, master_key: bytes, *, purpose: str) -> "ChunkCryptoSession":
        master_key = _key(master_key, "master key")
        _purpose(purpose)
        content_key = os.urandom(AES_KEY_BYTES)
        wrapped = self._wrap_key(content_key, master_key, purpose)
        return ChunkCryptoSession(purpose=purpose, content_key=content_key, wrapped_key=wrapped)

    def open_chunk_encryption(
        self,
        master_key: bytes,
        wrapped_key: WrappedKey,
        *,
        purpose: str,
    ) -> "ChunkCryptoSession":
        master_key = _key(master_key, "master key")
        content_key = self._unwrap_key(wrapped_key, master_key, purpose)
        return ChunkCryptoSession(purpose=purpose, content_key=content_key, wrapped_key=wrapped_key)

    def rewrap_chunk_key(
        self,
        wrapped_key: WrappedKey,
        old_master_key: bytes,
        new_master_key: bytes,
        *,
        purpose: str,
    ) -> WrappedKey:
        content_key = self._unwrap_key(wrapped_key, old_master_key, purpose)
        return self._wrap_key(content_key, new_master_key, purpose)

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


    def protect_master_key_with_trustee_key(
        self,
        master_key: bytes,
        trustee_key: bytes,
    ) -> ProtectedMasterKey:
        master_key = _key(master_key, "master key")
        trustee_key = _key(trustee_key, "trustee key")
        nonce = os.urandom(GCM_NONCE_BYTES)
        ciphertext = AESGCM(trustee_key).encrypt(
            nonce,
            master_key,
            _MASTER_TRUSTEE_DOMAIN,
        )
        return ProtectedMasterKey(
            format=CRYPTO_FORMAT,
            method="trustee-aes256gcm",
            salt="",
            nonce=_b64(nonce),
            ciphertext=_b64(ciphertext),
        )

    def unlock_master_key_with_trustee_key(
        self,
        protected: ProtectedMasterKey,
        trustee_key: bytes,
    ) -> bytes:
        if protected.format != CRYPTO_FORMAT or protected.method != "trustee-aes256gcm":
            raise ValueError("unsupported trustee-master-key format")
        trustee_key = _key(trustee_key, "trustee key")
        try:
            value = AESGCM(trustee_key).decrypt(
                _unb64(protected.nonce),
                _unb64(protected.ciphertext),
                _MASTER_TRUSTEE_DOMAIN,
            )
        except InvalidTag as exc:
            raise ValueError("trustee-key authentication failed") from exc
        return _key(value, "master key")


class ChunkCryptoSession:
    """Bounded per-chunk AEAD session using one wrapped random CEK.

    The raw CEK exists only in this in-memory session. Persistent callers store
    the wrapped key plus independently authenticated EncryptedChunk records.
    """

    def __init__(self, *, purpose: str, content_key: bytes, wrapped_key: WrappedKey):
        _purpose(purpose)
        self.purpose = str(purpose)
        self._content_key = _key(content_key, "content key")
        self.wrapped_key = wrapped_key

    @staticmethod
    def _index(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value >= 2**63:
            raise ValueError("chunk index must be an integer in range 0..2^63-1")
        return value

    def _aad(self, index: int, plaintext_size: int) -> bytes:
        return (
            _CHUNK_DOMAIN
            + _purpose(self.purpose)
            + b"\x00"
            + index.to_bytes(8, "big")
            + plaintext_size.to_bytes(8, "big")
        )

    def encrypt_chunk(self, index: int, plaintext: bytes) -> EncryptedChunk:
        index = self._index(index)
        payload = bytes(plaintext)
        if len(payload) > MAX_CRYPTO_CHUNK_BYTES:
            raise ValueError("crypto chunk exceeds 64 MiB limit")
        nonce = os.urandom(GCM_NONCE_BYTES)
        ciphertext = AESGCM(self._content_key).encrypt(
            nonce,
            payload,
            self._aad(index, len(payload)),
        )
        return EncryptedChunk(
            format=CHUNK_CRYPTO_FORMAT,
            purpose=self.purpose,
            index=index,
            plaintext_size=len(payload),
            nonce=_b64(nonce),
            ciphertext=_b64(ciphertext),
        )

    def decrypt_chunk(self, chunk: EncryptedChunk, *, expected_index: int) -> bytes:
        expected_index = self._index(expected_index)
        if chunk.format != CHUNK_CRYPTO_FORMAT:
            raise ValueError("unsupported encrypted-chunk format")
        if chunk.purpose != self.purpose:
            raise ValueError("encrypted chunk purpose mismatch")
        if self._index(chunk.index) != expected_index:
            raise ValueError("encrypted chunk index mismatch")
        if (
            isinstance(chunk.plaintext_size, bool)
            or not isinstance(chunk.plaintext_size, int)
            or chunk.plaintext_size < 0
            or chunk.plaintext_size > MAX_CRYPTO_CHUNK_BYTES
        ):
            raise ValueError("invalid encrypted chunk plaintext size")
        nonce = _unb64(chunk.nonce)
        if len(nonce) != GCM_NONCE_BYTES:
            raise ValueError("invalid encrypted chunk nonce")
        try:
            plaintext = AESGCM(self._content_key).decrypt(
                nonce,
                _unb64(chunk.ciphertext),
                self._aad(expected_index, chunk.plaintext_size),
            )
        except InvalidTag as exc:
            raise ValueError("encrypted chunk authentication failed") from exc
        if len(plaintext) != chunk.plaintext_size:
            raise ValueError("encrypted chunk plaintext size mismatch")
        return plaintext
