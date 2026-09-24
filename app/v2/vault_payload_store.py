"""Dedicated V2 object-store boundary for password-vault ciphertext envelopes.

This store deliberately uses a separate V2 root below the vault control directory.
It therefore cannot inherit normal document catalog, indexing or federation rules.
The stored content is already an authenticated AES-GCM vault envelope; no plaintext
credential field is written to this object store.
"""
from __future__ import annotations

import base64
import hashlib
import binascii
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .adapters.blob_catalog import BlobCatalogStorageAdapter
from .contracts import LogicalObjectId, StorageLocation


FORMAT = "simpleoffice-v2-vault-payload/v1"
MAX_ENVELOPE_BYTES = 2 * 1024 * 1024 + 4096


class VaultPayloadStore:
    """Store opaque credential ciphertext in a separate V2 Blob/Catalog root."""

    def __init__(self, root: str | Path, actor: str):
        source = Path(root).expanduser().resolve()
        self.actor = str(actor or "").strip()
        if not self.actor:
            raise ValueError("vault payload store requires an actor")
        scope = hashlib.sha256(self.actor.encode("utf-8")).hexdigest()
        container = source / ".simpleoffice-meta" / "password-vault-objects"
        container.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = container / scope
        self.root.mkdir(exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(container, 0o700)
            os.chmod(self.root, 0o700)
        self.storage = BlobCatalogStorageAdapter(self.root, self.actor)

    @staticmethod
    def _encode(nonce: bytes, ciphertext: bytes) -> bytes:
        nonce_bytes = bytes(nonce)
        ciphertext_bytes = bytes(ciphertext)
        if len(nonce_bytes) != 12 or not ciphertext_bytes:
            raise ValueError("invalid vault ciphertext envelope")
        value = {
            "format": FORMAT,
            "nonce": base64.urlsafe_b64encode(nonce_bytes).decode("ascii"),
            "ciphertext": base64.urlsafe_b64encode(ciphertext_bytes).decode("ascii"),
        }
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise ValueError("vault ciphertext envelope is too large")
        return raw

    @staticmethod
    def _decode(raw: bytes) -> tuple[bytes, bytes]:
        if not raw or len(raw) > MAX_ENVELOPE_BYTES:
            raise ValueError("invalid vault ciphertext envelope size")
        try:
            value: Any = json.loads(bytes(raw).decode("utf-8"))
            if not isinstance(value, dict) or value.get("format") != FORMAT:
                raise ValueError("unsupported vault ciphertext envelope")
            nonce = base64.urlsafe_b64decode(str(value["nonce"]).encode("ascii"))
            ciphertext = base64.urlsafe_b64decode(str(value["ciphertext"]).encode("ascii"))
        except (KeyError, TypeError, ValueError, UnicodeError, binascii.Error) as exc:
            raise ValueError("invalid vault ciphertext envelope") from exc
        if len(nonce) != 12 or not ciphertext:
            raise ValueError("invalid vault ciphertext envelope")
        return nonce, ciphertext

    def write(self, nonce: bytes, ciphertext: bytes) -> tuple[str, str]:
        """Create an immutable payload object and return object/version IDs."""
        location = StorageLocation(f"payload/{uuid.uuid4().hex}.vault")
        result = self.storage.create_bytes(location, self._encode(nonce, ciphertext))
        if not result.ok or result.value is None:
            message = result.error.message if result.error else "vault object-store write failed"
            raise OSError(message)
        return result.value.object_id.value, result.value.version

    def read(self, object_id: str, expected_version: str) -> tuple[bytes, bytes]:
        logical = LogicalObjectId(str(object_id))
        current = self.storage.catalog.get(logical)
        if not current.ok or current.value is None:
            raise OSError("vault payload object is unavailable")
        if str(current.value.version_id) != str(expected_version):
            raise RuntimeError("vault payload object version mismatch")
        result = self.storage.read_bytes(logical)
        if not result.ok or result.value is None:
            message = result.error.message if result.error else "vault object-store read failed"
            raise OSError(message)
        return self._decode(result.value)
