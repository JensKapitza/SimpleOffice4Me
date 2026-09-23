"""Operational provisioning helpers for the V2 local-storage master key."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .crypto import CryptoService
from .master_keys import (
    MasterKeyProfileStore,
    encode_recovery_key,
    load_master_password_file,
)
from .runtime_keys import STORAGE_PROFILE_ID


def _external_output(root: Path, path: str | Path, label: str) -> Path:
    supplied = Path(path).expanduser()
    if supplied.name in {"", ".", ".."}:
        raise ValueError(f"{label} path is invalid")
    try:
        parent = supplied.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} parent directory does not exist") from exc
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError(f"{label} parent must be a real directory")
    target = parent / supplied.name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"{label} already exists")
    if target == root or root in target.parents:
        raise ValueError(f"{label} must be stored outside the SimpleOffice data root")
    return target


def _write_new_secret(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(path, 0o600)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError:
        path.unlink(missing_ok=True)
        raise


def provision_storage_profile(
    root: str | Path,
    *,
    password_file: str | Path,
    recovery_key_output: str | Path,
    recovery_bundle_output: str | Path,
    actor: str = "v2-storage-setup",
) -> dict[str, Any]:
    """Create the dedicated storage key profile and offline recovery material."""

    data_root = Path(root).expanduser().resolve()
    key_target = _external_output(
        data_root,
        recovery_key_output,
        "recovery key output",
    )
    bundle_target = _external_output(
        data_root,
        recovery_bundle_output,
        "recovery bundle output",
    )
    if key_target == bundle_target:
        raise ValueError("recovery key and bundle outputs must be different files")

    password = load_master_password_file(
        password_file,
        forbidden_root=data_root,
    )
    store = MasterKeyProfileStore(data_root, actor)
    if store.configured(STORAGE_PROFILE_ID):
        raise ValueError("V2 storage master-key profile is already configured")

    recovery_key = CryptoService.generate_recovery_key()
    _write_new_secret(
        key_target,
        (encode_recovery_key(recovery_key) + "\n").encode("ascii"),
    )
    try:
        material = store.create(
            STORAGE_PROFILE_ID,
            password,
            recovery_key=recovery_key,
        )
    except (OSError, RuntimeError, ValueError):
        key_target.unlink(missing_ok=True)
        raise

    try:
        _write_new_secret(bundle_target, material.recovery_bundle)
    except OSError as exc:
        raise RuntimeError(
            "storage key profile and recovery key were created, but recovery bundle output failed; "
            "export the recovery bundle before continuing"
        ) from exc

    status = store.status(STORAGE_PROFILE_ID)
    return {
        "configured": True,
        "profile_hash": status["profile_hash"],
        "generation": status["generation"],
        "recovery_key_output": str(key_target),
        "recovery_bundle_output": str(bundle_target),
        "raw_master_key_persisted": False,
        "password_persisted_in_data_root": False,
    }


def export_storage_recovery_bundle(
    root: str | Path,
    output: str | Path,
    *,
    actor: str = "v2-storage-setup",
) -> dict[str, Any]:
    """Export only the non-plaintext recovery bundle to a new external file."""

    data_root = Path(root).expanduser().resolve()
    target = _external_output(
        data_root,
        output,
        "recovery bundle output",
    )
    store = MasterKeyProfileStore(data_root, actor)
    bundle = store.export_recovery_bundle(STORAGE_PROFILE_ID)
    _write_new_secret(target, bundle)
    status = store.status(STORAGE_PROFILE_ID)
    return {
        "exported": True,
        "profile_hash": status["profile_hash"],
        "generation": status["generation"],
        "recovery_bundle_output": str(target),
        "contains_raw_master_key": False,
        "contains_raw_recovery_key": False,
    }
