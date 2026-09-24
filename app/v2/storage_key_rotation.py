"""Crash-resumable orchestration for the encrypted V2 storage master key.

Normal application writers must be stopped while a rotation is active. The
journal contains only password-protected/encrypted key material and public
version identifiers; raw master/recovery keys are never persisted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .crypto import CryptoService, EncryptedPayload, ProtectedMasterKey, WrappedKey
from .cutover import LOCAL_ENCRYPTED_BLOB, load_cutover_state
from .encrypted_blob_store import EncryptedBlobIntegrityError, EncryptedBlobStore
from .master_keys import (
    MasterKeyProfileStore,
    encode_recovery_key,
    load_master_password_file,
    load_recovery_key_file,
)
from .runtime_keys import STORAGE_PROFILE_ID, clear_runtime_storage_master_key
from .storage_keys import _external_output, _write_new_secret


FORMAT = "simpleoffice-v2-storage-master-key-rotation/v1"
RECOVERY_PURPOSE = "simpleoffice-v2:storage-master-key-rotation:recovery"
MAX_JOURNAL_BYTES = 32 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _journal_path(root: Path) -> Path:
    return root / ".simpleoffice-v2" / "storage-master-key-rotation.json"


def _master_digest(value: bytes) -> str:
    return hashlib.sha256(bytes(value)).hexdigest()


def _protected(value: dict[str, Any]) -> ProtectedMasterKey:
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
        raise ValueError("storage master-key rotation journal contains invalid protected key material") from exc


def _payload_to_dict(value: EncryptedPayload) -> dict[str, Any]:
    return {
        "format": value.format,
        "purpose": value.purpose,
        "nonce": value.nonce,
        "ciphertext": value.ciphertext,
        "wrapped_key": asdict(value.wrapped_key),
    }


def _payload(value: dict[str, Any]) -> EncryptedPayload:
    try:
        wrapped = value["wrapped_key"]
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
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("storage master-key rotation journal contains invalid recovery material") from exc


def _write_journal(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError("V2 control directory must be a real directory")
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(payload) > MAX_JOURNAL_BYTES:
        raise ValueError("storage master-key rotation journal is too large")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _read_journal(root: Path) -> dict[str, Any]:
    path = _journal_path(root)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("no storage master-key rotation is pending") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_JOURNAL_BYTES
        ):
            raise ValueError("storage master-key rotation journal is invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_JOURNAL_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("storage master-key rotation journal is invalid") from exc
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        raise ValueError("unsupported storage master-key rotation journal")
    versions = value.get("version_ids")
    if not isinstance(versions, list) or any(not isinstance(item, str) for item in versions):
        raise ValueError("storage master-key rotation journal version list is invalid")
    if len(set(versions)) != len(versions):
        raise ValueError("storage master-key rotation journal contains duplicate versions")
    return value


def _external_existing_or_target(root: Path, value: str, label: str) -> Path:
    supplied = Path(str(value or "")).expanduser()
    if supplied.name in {"", ".", ".."}:
        raise ValueError(f"{label} path is invalid")
    if supplied.parent.is_symlink():
        raise ValueError(f"{label} parent must be a real directory")
    try:
        parent = supplied.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} parent directory does not exist") from exc
    if not parent.is_dir():
        raise ValueError(f"{label} parent must be a directory")
    target = parent / supplied.name
    if target == root or root in target.parents:
        raise ValueError(f"{label} must be stored outside the SimpleOffice data root")
    if target.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    if target.exists() and not target.is_file():
        raise ValueError(f"{label} must be a regular file")
    return target


def _version_ids(store: EncryptedBlobStore) -> list[str]:
    inventory = store.inventory()
    blockers = []
    if inventory.get("invalid_manifests"):
        blockers.append("invalid encrypted version manifests")
    if inventory.get("missing_chunks"):
        blockers.append("missing encrypted blob chunks")
    if inventory.get("staging_transactions"):
        blockers.append("unfinished encrypted blob staging transactions")
    if blockers:
        raise ValueError("storage master-key rotation preflight failed: " + "; ".join(blockers))

    result: list[str] = []
    for path in sorted(store.versions.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("encrypted version metadata must be regular files")
        try:
            version_id = str(uuid.UUID(path.stem))
        except ValueError as exc:
            raise ValueError("encrypted version filename is invalid") from exc
        store.version_manifest(version_id)
        result.append(version_id)
    return result


def _unpack_keys(
    journal: dict[str, Any],
    password: str,
) -> tuple[bytes, bytes, bytes]:
    crypto = CryptoService()
    old_master = crypto.unlock_master_key_with_password(
        _protected(journal["old_master"]),
        password,
    )
    new_master = crypto.unlock_master_key_with_password(
        _protected(journal["new_master"]),
        password,
    )
    if _master_digest(old_master) != str(journal.get("old_master_sha256") or ""):
        raise ValueError("storage master-key rotation old-key binding failed")
    if _master_digest(new_master) != str(journal.get("new_master_sha256") or ""):
        raise ValueError("storage master-key rotation new-key binding failed")
    recovery_payload = _payload(journal["new_recovery_key"])
    if recovery_payload.purpose != RECOVERY_PURPOSE:
        raise ValueError("storage master-key rotation recovery-key purpose is invalid")
    recovery_key = crypto.decrypt(recovery_payload, new_master)
    if len(recovery_key) != 32:
        raise ValueError("storage master-key rotation recovery key is invalid")
    return old_master, new_master, recovery_key


def _rotation_state(
    store: EncryptedBlobStore,
    version_ids: list[str],
    old_master: bytes,
    new_master: bytes,
) -> dict[str, int]:
    state = {"old": 0, "new": 0, "invalid": 0}
    for version_id in version_ids:
        old_matches = store.version_key_matches(version_id, old_master)
        new_matches = store.version_key_matches(version_id, new_master)
        if old_matches and not new_matches:
            state["old"] += 1
        elif new_matches and not old_matches:
            state["new"] += 1
        else:
            state["invalid"] += 1
    return state


def _write_recovery_key(target: Path, recovery_key: bytes) -> None:
    encoded = (encode_recovery_key(recovery_key) + "\n").encode("ascii")
    if target.exists():
        existing = load_recovery_key_file(target)
        if not hmac.compare_digest(existing, recovery_key):
            raise ValueError("recovery key output already exists with different material")
        return
    _write_new_secret(target, encoded)


def _write_recovery_bundle(target: Path, bundle: bytes) -> None:
    payload = bytes(bundle)
    if target.exists():
        if target.read_bytes() != payload:
            raise ValueError("recovery bundle output already exists with different material")
        return
    _write_new_secret(target, payload)


def _ensure_cutover(root: Path) -> None:
    state = load_cutover_state(root)
    if state.mode != "v2" or state.protection_mode != LOCAL_ENCRYPTED_BLOB:
        raise ValueError("storage master-key rotation requires authoritative encrypted V2 mode")


def rotation_pending(root: str | Path) -> bool:
    """Return whether encrypted runtime access must remain blocked."""

    source = Path(root).expanduser().resolve()
    path = _journal_path(source)
    if not path.exists():
        return False
    _read_journal(source)
    return True


def rotation_status(root: str | Path) -> dict[str, Any]:
    source = Path(root).expanduser().resolve()
    path = _journal_path(source)
    if not path.exists():
        return {
            "pending": False,
            "format": FORMAT,
        }
    journal = _read_journal(source)
    return {
        "pending": True,
        "format": FORMAT,
        "created_at": str(journal.get("created_at") or ""),
        "profile_hash": str(journal.get("profile_hash") or ""),
        "source_generation": int(journal.get("source_generation") or 0),
        "versions": len(journal.get("version_ids") or []),
        "recovery_key_output": str(journal.get("recovery_key_output") or ""),
        "recovery_bundle_output": str(journal.get("recovery_bundle_output") or ""),
        "raw_key_material_persisted": False,
    }


def _new_journal(
    root: Path,
    password: str,
    recovery_key_output: str | Path,
    recovery_bundle_output: str | Path,
) -> dict[str, Any]:
    profile = MasterKeyProfileStore(root, "v2-storage-key-rotation")
    status = profile.status(STORAGE_PROFILE_ID)
    old_master = profile.unlock_with_password(STORAGE_PROFILE_ID, password)
    encrypted = EncryptedBlobStore(root, old_master, initialize=False)
    versions = _version_ids(encrypted)

    key_target = _external_output(root, recovery_key_output, "recovery key output")
    bundle_target = _external_output(root, recovery_bundle_output, "recovery bundle output")
    if key_target == bundle_target:
        raise ValueError("recovery key and bundle outputs must be different files")

    crypto = CryptoService()
    new_master = crypto.generate_master_key()
    recovery_key = crypto.generate_recovery_key()
    journal = {
        "format": FORMAT,
        "created_at": _now(),
        "profile_hash": str(status["profile_hash"]),
        "source_generation": int(status["generation"]),
        "version_ids": versions,
        "old_master": asdict(crypto.protect_master_key_with_password(old_master, password)),
        "new_master": asdict(crypto.protect_master_key_with_password(new_master, password)),
        "old_master_sha256": _master_digest(old_master),
        "new_master_sha256": _master_digest(new_master),
        "new_recovery_key": _payload_to_dict(
            crypto.encrypt(recovery_key, new_master, purpose=RECOVERY_PURPOSE)
        ),
        "recovery_key_output": str(key_target),
        "recovery_bundle_output": str(bundle_target),
    }
    _write_journal(_journal_path(root), journal)
    return journal


def rotate_storage_master_key(
    root: str | Path,
    *,
    password_file: str | Path,
    recovery_key_output: str | Path,
    recovery_bundle_output: str | Path,
    apply: bool = False,
) -> dict[str, Any]:
    """Start or resume a crash-safe encrypted-storage master-key rotation."""

    source = Path(root).expanduser().resolve()
    _ensure_cutover(source)
    password = load_master_password_file(password_file, forbidden_root=source)
    path = _journal_path(source)
    if not apply:
        if path.exists():
            return {
                **rotation_status(source),
                "applied": False,
                "action": "resume",
            }
        current = MasterKeyProfileStore(source, "v2-storage-key-rotation").status(STORAGE_PROFILE_ID)
        return {
            "pending": False,
            "format": FORMAT,
            "applied": False,
            "action": "start",
            "source_generation": int(current["generation"]),
        }

    journal = _read_journal(source) if path.exists() else _new_journal(
        source,
        password,
        recovery_key_output,
        recovery_bundle_output,
    )
    return _resume_rotation(source, password, journal)


def _resume_rotation(
    root: Path,
    password: str,
    journal: dict[str, Any],
) -> dict[str, Any]:
    old_master, new_master, recovery_key = _unpack_keys(journal, password)
    key_target = _external_existing_or_target(
        root,
        str(journal["recovery_key_output"]),
        "recovery key output",
    )
    bundle_target = _external_existing_or_target(
        root,
        str(journal["recovery_bundle_output"]),
        "recovery bundle output",
    )

    profile = MasterKeyProfileStore(root, "v2-storage-key-rotation")
    status = profile.status(STORAGE_PROFILE_ID)
    active_master = profile.unlock_with_password(STORAGE_PROFILE_ID, password)
    encrypted = EncryptedBlobStore(root, new_master, initialize=False)
    expected_versions = list(journal["version_ids"])
    current_versions = _version_ids(encrypted)
    if current_versions != expected_versions:
        raise ValueError("encrypted version set changed during storage master-key rotation")

    active_is_old = hmac.compare_digest(active_master, old_master)
    active_is_new = hmac.compare_digest(active_master, new_master)
    if not active_is_old and not active_is_new:
        raise ValueError("active storage master key differs from the pending rotation")

    rewrapped = 0
    if active_is_old:
        if int(status["generation"]) != int(journal["source_generation"]):
            raise ValueError("storage master-key profile generation changed during rotation")
        for version_id in expected_versions:
            if encrypted.version_key_matches(version_id, new_master):
                continue
            if not encrypted.version_key_matches(version_id, old_master):
                raise EncryptedBlobIntegrityError(
                    f"encrypted version {version_id} is not readable with either rotation key"
                )
            encrypted.rewrap_version_key(version_id, old_master, new_master)
            rewrapped += 1

        final_state = _rotation_state(
            encrypted,
            expected_versions,
            old_master,
            new_master,
        )
        if final_state["old"] or final_state["invalid"]:
            raise RuntimeError("storage master-key rotation did not rewrap every encrypted version")
        if _version_ids(encrypted) != expected_versions:
            raise ValueError("encrypted version set changed while storage master-key rotation was running")

        _write_recovery_key(key_target, recovery_key)
        material = profile.replace_master_key(
            STORAGE_PROFILE_ID,
            password,
            new_master,
            recovery_key=recovery_key,
            expected_current_master_key=old_master,
        )
        _write_recovery_bundle(bundle_target, material.recovery_bundle)
        clear_runtime_storage_master_key(root)
    else:
        _write_recovery_key(key_target, recovery_key)
        bundle = profile.export_recovery_bundle(STORAGE_PROFILE_ID)
        _write_recovery_bundle(bundle_target, bundle)

    verified = _rotation_state(
        encrypted,
        expected_versions,
        old_master,
        new_master,
    )
    if verified["old"] or verified["invalid"] or verified["new"] != len(expected_versions):
        raise RuntimeError("storage master-key rotation final verification failed")

    _journal_path(root).unlink(missing_ok=True)
    clear_runtime_storage_master_key(root)
    return {
        "format": FORMAT,
        "applied": True,
        "completed": True,
        "versions": len(expected_versions),
        "versions_rewrapped_this_run": rewrapped,
        "new_generation": int(profile.status(STORAGE_PROFILE_ID)["generation"]),
        "recovery_key_output": str(key_target),
        "recovery_bundle_output": str(bundle_target),
        "ciphertext_reencrypted": False,
        "raw_key_material_persisted": False,
    }


def rollback_storage_master_key_rotation(
    root: str | Path,
    *,
    password_file: str | Path,
    apply: bool = False,
) -> dict[str, Any]:
    """Abort a pending rotation before the new profile has been committed."""

    source = Path(root).expanduser().resolve()
    _ensure_cutover(source)
    journal = _read_journal(source)
    password = load_master_password_file(password_file, forbidden_root=source)
    old_master, new_master, recovery_key = _unpack_keys(journal, password)
    profile = MasterKeyProfileStore(source, "v2-storage-key-rotation")
    active_master = profile.unlock_with_password(STORAGE_PROFILE_ID, password)
    if not hmac.compare_digest(active_master, old_master):
        raise ValueError("rotation rollback is only allowed before the new master profile is committed")

    store = EncryptedBlobStore(source, new_master, initialize=False)
    versions = list(journal["version_ids"])
    state = _rotation_state(store, versions, old_master, new_master)
    preview = {
        "format": FORMAT,
        "applied": bool(apply),
        "versions": len(versions),
        "versions_on_old_key": state["old"],
        "versions_on_new_key": state["new"],
        "invalid_versions": state["invalid"],
    }
    if not apply:
        return preview
    if state["invalid"]:
        raise ValueError("rotation rollback is blocked by versions unreadable with either rotation key")

    for version_id in versions:
        if store.version_key_matches(version_id, new_master):
            store.rewrap_version_key(version_id, new_master, old_master)

    final = _rotation_state(store, versions, old_master, new_master)
    if final["new"] or final["invalid"]:
        raise RuntimeError("storage master-key rotation rollback verification failed")

    key_target = _external_existing_or_target(
        source,
        str(journal["recovery_key_output"]),
        "recovery key output",
    )
    if key_target.exists():
        try:
            existing = load_recovery_key_file(key_target)
        except ValueError:
            existing = b""
        if existing and hmac.compare_digest(existing, recovery_key):
            key_target.unlink()

    _journal_path(source).unlink(missing_ok=True)
    clear_runtime_storage_master_key(source)
    return {
        **preview,
        "rolled_back": True,
        "versions_on_old_key": len(versions),
        "versions_on_new_key": 0,
    }
