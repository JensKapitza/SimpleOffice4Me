"""Federation push/pull helpers for portable network-boot structures."""
from __future__ import annotations

import hashlib
import os
import socket
from pathlib import Path
from typing import Any

from .federation_store import FederationStore
from .federation_worker import _json_request, _request
from .network_boot import (
    federation_manifest,
    list_assets,
    load_boot_settings,
    safe_asset_path,
    save_boot_settings,
    store_asset,
)


def _local_peer_id() -> str:
    configured = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "").strip()
    return configured or socket.gethostname().strip().casefold().replace(" ", "-")[:128]


def _peer(root: str | Path, peer_id: str, direction: str) -> tuple[FederationStore, dict[str, Any], str]:
    store = FederationStore(root)
    peer = store.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError("Federation-Peer ist nicht aktiv")
    policy = peer.get("policy") or {}
    section = policy.get("network_boot", {}) if isinstance(policy, dict) else {}
    if not isinstance(section, dict) or section.get(direction) is not True:
        raise ValueError(f"Networkboot-{direction} ist für diesen Peer nicht freigegeben")
    return store, peer, store.peer_token(peer_id)


def portable_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Return only settings that are safe to copy between different hosts."""
    return {
        "default_profile": settings.get("default_profile", ""),
        "bios_loader": settings.get("bios_loader", "undionly.kpxe"),
        "uefi_x64_loader": settings.get("uefi_x64_loader", "ipxe.efi"),
        "uefi_arm64_loader": settings.get("uefi_arm64_loader", "ipxe-arm64.efi"),
        "profiles": settings.get("profiles", []),
    }


def merge_portable_settings(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    result = dict(local)
    for key in ("default_profile", "bios_loader", "uefi_x64_loader", "uefi_arm64_loader", "profiles"):
        if key in remote:
            result[key] = remote[key]
    # Bind addresses, ports, enable state and HTTP base URL remain local.
    return result


def pull_network_boot(root: str | Path, peer_id: str, config_path: str | Path) -> dict[str, Any]:
    store, peer, token = _peer(root, peer_id, "receive")
    headers = {"X-SimpleOffice-Peer-ID": _local_peer_id()}
    with _request(
        peer["base_url"] + "/federation/v1/network-boot/manifest",
        token=token,
        headers=headers,
        timeout=60,
    ) as response:
        import json
        manifest = json.loads(response.read().decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != "simpleoffice-network-boot/v1":
        raise ValueError("Peer liefert kein kompatibles Networkboot-Manifest")

    local_assets = {row["path"]: row for row in list_assets(config_path)}
    downloaded = unchanged = 0
    for remote in manifest.get("assets", []):
        if not isinstance(remote, dict):
            continue
        relative = str(remote.get("path") or "")
        digest = str(remote.get("sha256") or "").casefold()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("Remote-Bootasset besitzt keine gültige SHA-256")
        current = local_assets.get(relative)
        if current and current.get("sha256") == digest:
            unchanged += 1
            continue
        with _request(
            peer["base_url"] + "/federation/v1/network-boot/assets/" + relative,
            token=token,
            headers=headers,
            timeout=300,
        ) as response:
            result = store_asset(response, relative, config_path)
        if result["sha256"] != digest:
            raise ValueError(f"SHA-256 stimmt nach Download nicht: {relative}")
        downloaded += 1

    remote_settings = manifest.get("settings") if isinstance(manifest.get("settings"), dict) else {}
    local_settings = load_boot_settings(config_path)
    saved = save_boot_settings(merge_portable_settings(local_settings, portable_settings(remote_settings)), config_path)
    detail = {"downloaded": downloaded, "unchanged": unchanged, "profiles": len(saved["profiles"])}
    store.record_event("network_boot_pulled", peer_id=peer_id, detail=detail)
    return detail


def push_network_boot(root: str | Path, peer_id: str, config_path: str | Path) -> dict[str, Any]:
    store, peer, token = _peer(root, peer_id, "send")
    local_id = _local_peer_id()
    headers = {"X-SimpleOffice-Peer-ID": local_id}
    assets = list_assets(config_path)
    uploaded = 0
    for row in assets:
        relative = row["path"]
        path = safe_asset_path(relative, config_path)
        with path.open("rb") as source:
            with _request(
                peer["base_url"] + "/federation/v1/network-boot/assets/" + relative,
                method="PUT",
                token=token,
                body=source.read(),
                headers={**headers, "Content-Type": "application/octet-stream", "X-Content-SHA256": row["sha256"]},
                timeout=300,
            ) as response:
                response.read()
        uploaded += 1
    settings = portable_settings(load_boot_settings(config_path))
    _json_request(
        peer["base_url"] + "/federation/v1/network-boot/settings",
        method="PUT",
        token=token,
        payload=settings,
        timeout=60,
    )
    detail = {"uploaded": uploaded, "profiles": len(settings.get("profiles", []))}
    store.record_event("network_boot_pushed", peer_id=peer_id, detail=detail)
    return detail
