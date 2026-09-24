"""Operational trustee recovery helpers for the V2 storage master-key profile."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .crypto import CryptoService
from .master_keys import (
    MasterKeyProfileStore,
    encode_trustee_key,
    load_master_password_file,
)
from .runtime_keys import STORAGE_PROFILE_ID, clear_runtime_storage_master_key
from .storage_keys import _external_output, _write_new_secret


def _targets(
    root: Path,
    key_output: str | Path,
    bundle_output: str | Path,
) -> tuple[Path, Path]:
    key_target = _external_output(root, key_output, "trustee key output")
    bundle_target = _external_output(root, bundle_output, "trustee bundle output")
    if key_target == bundle_target:
        raise ValueError("trustee key and bundle outputs must be different files")
    return key_target, bundle_target



def provision_storage_trustee(
    root: str | Path,
    *,
    password_file: str | Path,
    trustee_key_output: str | Path,
    trustee_bundle_output: str | Path,
    actor: str = "v2-storage-trustee",
) -> dict[str, Any]:
    data_root = Path(root).expanduser().resolve()
    key_target, bundle_target = _targets(
        data_root,
        trustee_key_output,
        trustee_bundle_output,
    )
    password = load_master_password_file(password_file, forbidden_root=data_root)
    store = MasterKeyProfileStore(data_root, actor)
    if store.status(STORAGE_PROFILE_ID).get("trustee_configured"):
        raise ValueError("storage trustee recovery is already configured")

    trustee_key = CryptoService.generate_trustee_key()
    _write_new_secret(
        key_target,
        (encode_trustee_key(trustee_key) + "\n").encode("ascii"),
    )
    try:
        material = store.enable_trustee_key(
            STORAGE_PROFILE_ID,
            password,
            trustee_key=trustee_key,
        )
    except (OSError, RuntimeError, ValueError):
        key_target.unlink(missing_ok=True)
        raise
    try:
        _write_new_secret(bundle_target, material.trustee_bundle)
    except OSError as exc:
        raise RuntimeError(
            "storage trustee was enabled, but trustee bundle output failed; "
            "export a trustee bundle before relying on emergency recovery"
        ) from exc

    clear_runtime_storage_master_key(data_root)
    status = store.status(STORAGE_PROFILE_ID)
    return {
        "configured": True,
        "profile_hash": status["profile_hash"],
        "generation": status["generation"],
        "trustee_key_output": str(key_target),
        "trustee_bundle_output": str(bundle_target),
        "raw_master_key_persisted": False,
        "raw_trustee_key_persisted": False,
    }


def rotate_storage_trustee(
    root: str | Path,
    *,
    password_file: str | Path,
    trustee_key_output: str | Path,
    trustee_bundle_output: str | Path,
    actor: str = "v2-storage-trustee",
) -> dict[str, Any]:
    data_root = Path(root).expanduser().resolve()
    key_target, bundle_target = _targets(
        data_root,
        trustee_key_output,
        trustee_bundle_output,
    )
    password = load_master_password_file(password_file, forbidden_root=data_root)
    store = MasterKeyProfileStore(data_root, actor)
    if not store.status(STORAGE_PROFILE_ID).get("trustee_configured"):
        raise ValueError("storage trustee recovery is not configured")

    trustee_key = CryptoService.generate_trustee_key()
    _write_new_secret(
        key_target,
        (encode_trustee_key(trustee_key) + "\n").encode("ascii"),
    )
    try:
        material = store.rotate_trustee_key(
            STORAGE_PROFILE_ID,
            password,
            trustee_key=trustee_key,
        )
    except (OSError, RuntimeError, ValueError):
        key_target.unlink(missing_ok=True)
        raise
    try:
        _write_new_secret(bundle_target, material.trustee_bundle)
    except OSError as exc:
        raise RuntimeError(
            "storage trustee was rotated, but trustee bundle output failed; "
            "export a trustee bundle before relying on emergency recovery"
        ) from exc

    clear_runtime_storage_master_key(data_root)
    status = store.status(STORAGE_PROFILE_ID)
    return {
        "rotated": True,
        "profile_hash": status["profile_hash"],
        "generation": status["generation"],
        "trustee_key_output": str(key_target),
        "trustee_bundle_output": str(bundle_target),
        "raw_master_key_persisted": False,
        "raw_trustee_key_persisted": False,
    }


def disable_storage_trustee(
    root: str | Path,
    *,
    password_file: str | Path,
    actor: str = "v2-storage-trustee",
) -> dict[str, Any]:
    data_root = Path(root).expanduser().resolve()
    password = load_master_password_file(password_file, forbidden_root=data_root)
    store = MasterKeyProfileStore(data_root, actor)
    store.disable_trustee_key(STORAGE_PROFILE_ID, password)
    clear_runtime_storage_master_key(data_root)
    status = store.status(STORAGE_PROFILE_ID)
    return {
        "configured": bool(status["trustee_configured"]),
        "profile_hash": status["profile_hash"],
        "generation": status["generation"],
    }


def export_storage_trustee_bundle(
    root: str | Path,
    output: str | Path,
    *,
    actor: str = "v2-storage-trustee",
) -> dict[str, Any]:
    data_root = Path(root).expanduser().resolve()
    target = _external_output(data_root, output, "trustee bundle output")
    store = MasterKeyProfileStore(data_root, actor)
    bundle = store.export_trustee_bundle(STORAGE_PROFILE_ID)
    _write_new_secret(target, bundle)
    status = store.status(STORAGE_PROFILE_ID)
    return {
        "exported": True,
        "profile_hash": status["profile_hash"],
        "generation": status["generation"],
        "trustee_bundle_output": str(target),
        "contains_raw_master_key": False,
        "contains_raw_trustee_key": False,
    }
