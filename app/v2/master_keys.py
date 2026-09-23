"""Persistent V2 master-key profiles with password and offline recovery wrapping.

The profile store never persists a raw master key, password or recovery key.
Profiles are keyed on a SHA-256 digest of the external profile identifier so
filesystem names do not expose the identifier. Recovery bundles contain only an
encrypted master-key record plus an authenticated key-check record.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters.audit import RevisionHistoryAuditAdapter
from .contracts import AuditEvent, AuditPort
from .crypto import CryptoService, EncryptedPayload, ProtectedMasterKey, WrappedKey


PROFILE_FORMAT = "simpleoffice-v2-master-key-profile/v1"
RECOVERY_FORMAT = "simpleoffice-v2-master-key-recovery/v1"
MAX_PROFILE_BYTES = 256 * 1024
MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_CHARS = 4096
_KEY_CHECK = b"simpleoffice-v2-master-key-profile-check:v1"
logger = logging.getLogger(__name__)


@dataclass(frozen=True, repr=False)
class RecoveryMaterial:
    """One-time recovery material returned to the provisioning caller."""

    recovery_key: bytes
    recovery_bundle: bytes


def encode_recovery_key(value: bytes) -> str:
    key = bytes(value)
    if len(key) != 32:
        raise ValueError("recovery key must be 32 bytes")
    return base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")


def decode_recovery_key(value: str) -> bytes:
    text = str(value or "").strip()
    if not text or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in text):
        raise ValueError("invalid recovery key encoding")
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        key = base64.b64decode(text + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid recovery key encoding") from exc
    if len(key) != 32:
        raise ValueError("invalid recovery key length")
    return key


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _profile_hash(profile_id: str) -> str:
    value = str(profile_id or "").strip()
    if not value or len(value) > 200:
        raise ValueError("invalid master-key profile id")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _password(value: str) -> str:
    if not isinstance(value, str) or not (MIN_PASSWORD_CHARS <= len(value) <= MAX_PASSWORD_CHARS):
        raise ValueError(
            f"master-key password must be between {MIN_PASSWORD_CHARS} and {MAX_PASSWORD_CHARS} characters"
        )
    return value


def _protected_to_dict(value: ProtectedMasterKey) -> dict[str, Any]:
    return {
        "format": value.format,
        "method": value.method,
        "salt": value.salt,
        "nonce": value.nonce,
        "ciphertext": value.ciphertext,
        "time_cost": value.time_cost,
        "memory_kib": value.memory_kib,
        "parallelism": value.parallelism,
    }


def _protected_from_dict(value: Any) -> ProtectedMasterKey:
    if not isinstance(value, dict):
        raise ValueError("protected master-key record is invalid")
    try:
        return ProtectedMasterKey(
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
        raise ValueError("protected master-key record is incomplete") from exc


def _payload_to_dict(value: EncryptedPayload) -> dict[str, Any]:
    return {
        "format": value.format,
        "purpose": value.purpose,
        "nonce": value.nonce,
        "ciphertext": value.ciphertext,
        "wrapped_key": {
            "format": value.wrapped_key.format,
            "nonce": value.wrapped_key.nonce,
            "ciphertext": value.wrapped_key.ciphertext,
        },
    }


def _payload_from_dict(value: Any) -> EncryptedPayload:
    if not isinstance(value, dict) or not isinstance(value.get("wrapped_key"), dict):
        raise ValueError("master-key check record is invalid")
    wrapped = value["wrapped_key"]
    try:
        return EncryptedPayload(
            format=str(value["format"]),
            purpose=str(value["purpose"]),
            nonce=str(value["nonce"]),
            ciphertext=str(value["ciphertext"]),
            wrapped_key=WrappedKey(
                format=str(wrapped["format"]),
                nonce=str(wrapped["nonce"]),
                ciphertext=str(wrapped["ciphertext"]),
            ),
        )
    except KeyError as exc:
        raise ValueError("master-key check record is incomplete") from exc


def _check_purpose(profile_hash: str) -> str:
    return f"master-profile:{profile_hash}"


def _verify_check(crypto: CryptoService, master_key: bytes, profile_hash: str, value: Any) -> None:
    payload = _payload_from_dict(value)
    if payload.purpose != _check_purpose(profile_hash):
        raise ValueError("master-key profile binding mismatch")
    try:
        marker = crypto.decrypt(payload, master_key)
    except ValueError as exc:
        raise ValueError("master-key profile check failed") from exc
    if marker != _KEY_CHECK:
        raise ValueError("master-key profile check failed")


def _load_json_bytes(data: bytes, *, expected_format: str) -> dict[str, Any]:
    if not data or len(data) > MAX_PROFILE_BYTES:
        raise ValueError("master-key record size is invalid")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("master-key record is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("format") != expected_format:
        raise ValueError("unsupported master-key record format")
    profile_hash = str(value.get("profile_hash") or "")
    if len(profile_hash) != 64 or any(char not in "0123456789abcdef" for char in profile_hash):
        raise ValueError("master-key profile hash is invalid")
    return value


def recover_master_key_from_bundle(
    bundle: bytes,
    recovery_key: bytes,
    *,
    crypto: CryptoService | None = None,
) -> bytes:
    """Recover a master key without a live SimpleOffice root or user database."""

    value = _load_json_bytes(bytes(bundle), expected_format=RECOVERY_FORMAT)
    service = crypto or CryptoService()
    protected = _protected_from_dict(value.get("recovery"))
    try:
        master_key = service.unlock_master_key_with_recovery_key(protected, bytes(recovery_key))
    except ValueError as exc:
        raise ValueError("recovery key is invalid or recovery bundle was modified") from exc
    _verify_check(service, master_key, str(value["profile_hash"]), value.get("key_check"))
    return master_key


class MasterKeyProfileStore:
    """Persist password/recovery-protected master keys without raw key material."""

    def __init__(
        self,
        root: str | Path,
        actor: str,
        *,
        audit_port: AuditPort | None = None,
        crypto: CryptoService | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        self.base = self.root / ".simpleoffice-v2" / "master-keys"
        self.actor = str(actor or "").strip()
        if not self.actor or len(self.actor) > 200:
            raise ValueError("master-key profile store requires a valid actor")
        self.audit = audit_port or RevisionHistoryAuditAdapter(self.root)
        self.crypto = crypto or CryptoService()

    def _ensure_base(self) -> None:
        control = self.root / ".simpleoffice-v2"
        control.mkdir(parents=True, exist_ok=True)
        if control.is_symlink() or not control.is_dir():
            raise ValueError("V2 control directory must be a real directory")
        self.base.mkdir(exist_ok=True)
        if self.base.is_symlink() or not self.base.is_dir():
            raise ValueError("master-key directory must be a real directory")
        if os.name == "posix":
            try:
                os.chmod(self.base, 0o700)
            except OSError as exc:
                raise ValueError("master-key directory permissions could not be secured") from exc

    @contextmanager
    def _profile_lock(self, profile_id: str):
        self._ensure_base()
        profile_hash = _profile_hash(profile_id)
        lock = self.base / f".{profile_hash}.lock"
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise RuntimeError("master-key profile is busy or has a stale lock") from exc
        try:
            yield
        finally:
            try:
                lock.rmdir()
            except OSError as exc:
                logger.warning(
                    "master-key profile lock cleanup failed profile=%s error=%s",
                    profile_hash[:16],
                    type(exc).__name__,
                )

    def _path(self, profile_id: str) -> Path:
        return self.base / f"{_profile_hash(profile_id)}.json"

    def configured(self, profile_id: str) -> bool:
        return self._path(profile_id).is_file()

    def _read(self, profile_id: str) -> dict[str, Any]:
        path = self._path(profile_id)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ValueError("master-key profile is not configured") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size <= 0
                or metadata.st_size > MAX_PROFILE_BYTES
            ):
                raise ValueError("master-key profile is unavailable or invalid")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                data = handle.read(MAX_PROFILE_BYTES + 1)
            if len(data) > MAX_PROFILE_BYTES:
                raise ValueError("master-key profile is too large")
        finally:
            os.close(descriptor)
        value = _load_json_bytes(data, expected_format=PROFILE_FORMAT)
        expected_hash = _profile_hash(profile_id)
        if value["profile_hash"] != expected_hash:
            raise ValueError("master-key profile identity mismatch")
        try:
            generation = int(value["generation"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("master-key profile generation is invalid") from exc
        if generation < 1:
            raise ValueError("master-key profile generation is invalid")
        return value

    def _write(self, profile_id: str, value: dict[str, Any]) -> None:
        path = self._path(profile_id)
        self._ensure_base()
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_PROFILE_BYTES:
            raise ValueError("master-key profile is too large")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name == "posix":
                try:
                    directory = os.open(self.base, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except OSError as exc:
                    logger.warning(
                        "master-key profile directory fsync failed profile=%s error=%s",
                        _profile_hash(profile_id)[:16],
                        type(exc).__name__,
                    )
        finally:
            temporary.unlink(missing_ok=True)

    def _audit(self, operation: str, profile_hash: str, **changes: Any) -> None:
        result = self.audit.append(
            AuditEvent(
                actor=self.actor,
                operation=operation,
                object_id=f"master-key-profile:{profile_hash[:24]}",
                occurred_at=_now(),
                source="v2-master-key-profile",
                changes=changes,
            )
        )
        if not result.ok:
            raise RuntimeError("master-key security event could not be audited")

    @staticmethod
    def _bundle(value: dict[str, Any]) -> bytes:
        payload = {
            "format": RECOVERY_FORMAT,
            "profile_hash": value["profile_hash"],
            "recovery": value["recovery"],
            "key_check": value["key_check"],
        }
        return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

    def export_recovery_bundle(self, profile_id: str) -> bytes:
        value = self._read(profile_id)
        bundle = self._bundle(value)
        self._audit(
            "master_key_recovery_bundle_exported",
            str(value["profile_hash"]),
            generation=int(value["generation"]),
        )
        return bundle

    def create(self, profile_id: str, password: str) -> RecoveryMaterial:
        profile_hash = _profile_hash(profile_id)
        password = _password(password)
        path = self._path(profile_id)
        with self._profile_lock(profile_id):
            if path.exists():
                raise ValueError("master-key profile already exists")

            master_key = self.crypto.generate_master_key()
            recovery_key = self.crypto.generate_recovery_key()
            password_record = self.crypto.protect_master_key_with_password(master_key, password)
            recovery_record = self.crypto.protect_master_key_with_recovery_key(master_key, recovery_key)
            key_check = self.crypto.encrypt(_KEY_CHECK, master_key, purpose=_check_purpose(profile_hash))
            timestamp = _now()
            value = {
                "format": PROFILE_FORMAT,
                "profile_hash": profile_hash,
                "generation": 1,
                "created_at": timestamp,
                "updated_at": timestamp,
                "password": _protected_to_dict(password_record),
                "recovery": _protected_to_dict(recovery_record),
                "key_check": _payload_to_dict(key_check),
            }
            self._write(profile_id, value)
            try:
                self._audit("master_key_profile_created", profile_hash, generation=1)
            except RuntimeError:
                try:
                    path.unlink()
                except OSError as rollback_exc:
                    raise RuntimeError(
                        "master-key profile was written but audit failed; manual cleanup is required"
                    ) from rollback_exc
                raise
            return RecoveryMaterial(recovery_key=recovery_key, recovery_bundle=self._bundle(value))

    def _unlock_password(self, profile_id: str, password: str) -> tuple[dict[str, Any], bytes]:
        value = self._read(profile_id)
        try:
            master_key = self.crypto.unlock_master_key_with_password(
                _protected_from_dict(value.get("password")),
                _password(password),
            )
            _verify_check(self.crypto, master_key, str(value["profile_hash"]), value.get("key_check"))
            return value, master_key
        except ValueError as exc:
            try:
                self._audit(
                    "master_key_unlock_failed",
                    str(value["profile_hash"]),
                    reason="invalid_credentials_or_integrity",
                )
            except RuntimeError as audit_exc:
                raise RuntimeError("failed master-key unlock attempt could not be audited") from audit_exc
            raise ValueError("master-key password is invalid or profile was modified") from exc

    def unlock_with_password(self, profile_id: str, password: str) -> bytes:
        value, master_key = self._unlock_password(profile_id, password)
        self._audit(
            "master_key_unlocked",
            str(value["profile_hash"]),
            method="password",
            generation=int(value["generation"]),
        )
        return master_key

    def unlock_with_recovery_key(self, profile_id: str, recovery_key: bytes) -> bytes:
        value = self._read(profile_id)
        try:
            master_key = self.crypto.unlock_master_key_with_recovery_key(
                _protected_from_dict(value.get("recovery")),
                bytes(recovery_key),
            )
            _verify_check(self.crypto, master_key, str(value["profile_hash"]), value.get("key_check"))
        except ValueError as exc:
            try:
                self._audit(
                    "master_key_unlock_failed",
                    str(value["profile_hash"]),
                    reason="invalid_recovery_or_integrity",
                )
            except RuntimeError as audit_exc:
                raise RuntimeError("failed master-key recovery attempt could not be audited") from audit_exc
            raise ValueError("recovery key is invalid or profile was modified") from exc
        self._audit(
            "master_key_recovered",
            str(value["profile_hash"]),
            generation=int(value["generation"]),
        )
        return master_key

    def change_password(self, profile_id: str, old_password: str, new_password: str) -> None:
        with self._profile_lock(profile_id):
            value, master_key = self._unlock_password(profile_id, old_password)
            new_record = self.crypto.protect_master_key_with_password(master_key, _password(new_password))
            previous = dict(value)
            updated = dict(value)
            updated["generation"] = int(value["generation"]) + 1
            updated["updated_at"] = _now()
            updated["password"] = _protected_to_dict(new_record)
            self._write(profile_id, updated)
            try:
                self._audit(
                    "master_key_password_changed",
                    str(value["profile_hash"]),
                    generation=int(updated["generation"]),
                )
            except RuntimeError:
                self._write(profile_id, previous)
                raise

    def rotate_recovery_key(self, profile_id: str, password: str) -> RecoveryMaterial:
        with self._profile_lock(profile_id):
            value, master_key = self._unlock_password(profile_id, password)
            recovery_key = self.crypto.generate_recovery_key()
            recovery_record = self.crypto.protect_master_key_with_recovery_key(master_key, recovery_key)
            previous = dict(value)
            updated = dict(value)
            updated["generation"] = int(value["generation"]) + 1
            updated["updated_at"] = _now()
            updated["recovery"] = _protected_to_dict(recovery_record)
            self._write(profile_id, updated)
            try:
                self._audit(
                    "master_key_recovery_rotated",
                    str(value["profile_hash"]),
                    generation=int(updated["generation"]),
                )
            except RuntimeError:
                self._write(profile_id, previous)
                raise
            return RecoveryMaterial(recovery_key=recovery_key, recovery_bundle=self._bundle(updated))

    def status(self, profile_id: str) -> dict[str, Any]:
        value = self._read(profile_id)
        password_record = _protected_from_dict(value.get("password"))
        recovery_record = _protected_from_dict(value.get("recovery"))
        return {
            "configured": True,
            "profile_hash": str(value["profile_hash"]),
            "generation": int(value["generation"]),
            "created_at": str(value.get("created_at") or ""),
            "updated_at": str(value.get("updated_at") or ""),
            "password_method": password_record.method,
            "recovery_method": recovery_record.method,
            "raw_master_key_persisted": False,
            "raw_recovery_key_persisted": False,
        }
