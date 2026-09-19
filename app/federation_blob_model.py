"""Metadata model for encrypted, chunked federation blobs.

Canonical identity is the SHA-256 of plaintext content. Public storage records
must never receive plaintext hashes, object metadata, passwords or key material.
Encryption/key derivation itself belongs to the crypto adapter; this module
keeps its storage contract explicit and testable.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any, Iterable

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def opaque_storage_id() -> str:
    return secrets.token_urlsafe(24)


@dataclass(frozen=True)
class EncryptedChunk:
    index: int
    offset: int
    plain_length: int
    plain_hash: str
    cipher_length: int
    cipher_hash: str
    storage_id: str
    nonce: str

    def trusted_record(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "offset": self.offset,
            "plain_length": self.plain_length,
            "plain_hash": self.plain_hash,
            "cipher_length": self.cipher_length,
            "cipher_hash": self.cipher_hash,
            "storage_id": self.storage_id,
            "nonce": self.nonce,
        }

    def public_record(self) -> dict[str, Any]:
        return {
            "storage_id": self.storage_id,
            "size": self.cipher_length,
            "cipher_hash": self.cipher_hash,
        }


@dataclass(frozen=True)
class KeyEnvelope:
    """Wrapped content-key reference. Never contains a password or raw CEK."""

    envelope_id: str
    kind: str
    recipient_id: str
    algorithm: str
    wrapped_key: str
    salt: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in {"peer", "knowledge-group", "local"}:
            raise ValueError("unsupported key envelope kind")
        if not self.envelope_id or not self.recipient_id or not self.wrapped_key:
            raise ValueError("incomplete key envelope")


@dataclass(frozen=True)
class BlobManifest:
    content_hash: str
    size: int
    chunks: tuple[EncryptedChunk, ...]
    encryption_algorithm: str = "AES-256-GCM"
    key_id: str = ""

    def __post_init__(self) -> None:
        if len(self.content_hash) != 64:
            raise ValueError("invalid content hash")
        if self.size < 0:
            raise ValueError("invalid blob size")
        expected_offset = 0
        for expected_index, chunk in enumerate(self.chunks):
            if chunk.index != expected_index or chunk.offset != expected_offset:
                raise ValueError("non-contiguous chunk manifest")
            expected_offset += chunk.plain_length
        if expected_offset != self.size:
            raise ValueError("chunk lengths do not match blob size")

    @property
    def blob_id(self) -> str:
        return f"sha256:{self.content_hash}"

    def trusted_record(self) -> dict[str, Any]:
        return {
            "blob_id": self.blob_id,
            "content_hash": self.content_hash,
            "size": self.size,
            "chunk_size": DEFAULT_CHUNK_SIZE,
            "chunk_count": len(self.chunks),
            "encryption": {"algorithm": self.encryption_algorithm, "key_id": self.key_id},
            "chunks": [chunk.trusted_record() for chunk in self.chunks],
        }

    def public_records(self) -> list[dict[str, Any]]:
        return [chunk.public_record() for chunk in self.chunks]


def plaintext_chunk_descriptors(data: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[dict[str, Any]]:
    """Build local-only plaintext descriptors before the crypto adapter encrypts chunks."""
    if chunk_size <= 0:
        raise ValueError("chunk size must be positive")
    result = []
    for index, offset in enumerate(range(0, len(data), chunk_size)):
        chunk = data[offset : offset + chunk_size]
        result.append({
            "index": index,
            "offset": offset,
            "length": len(chunk),
            "plain_hash": sha256_hex(chunk),
        })
    return result


def knowledge_group_descriptor(group_id: str, *, kdf: str = "argon2id") -> dict[str, str]:
    """Public descriptor only; the actual shared knowledge is never serialized."""
    group_id = str(group_id or "").strip()
    if not group_id:
        raise ValueError("knowledge group id required")
    return {"group_id": group_id, "kdf": kdf}
