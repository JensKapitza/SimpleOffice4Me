"""Federation helpers for portable network-boot structures.

Peer policy is deliberately expressed from the configured peer's perspective:

``offers_network_boot``
    This peer offers its boot profiles/assets to us. We may fetch from it.

``stores_network_boot``
    This peer is willing to retain/mirror our boot profiles/assets. We may
    replicate our data to it.

These are independent. A peer can be a boot source, a storage/backup peer,
both, or neither.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .federation_store import FederationStore
from .federation_worker import _request
from .network_boot import (
    list_assets,
    load_boot_settings,
    safe_asset_path,
    save_boot_settings,
    store_asset,
)

POLICY_OFFERS = "offers_network_boot"
POLICY_STORES = "stores_network_boot"


def _local_peer_id() -> str:
    configured = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "").strip()
    return configured or socket.gethostname().strip().casefold().replace(" ", "-")[:128]


def _peer(root: str | Path, peer_id: str, capability: str) -> tuple[FederationStore, dict[str, Any], str]:
    store = FederationStore(root)
    peer = store.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError("Federation-Peer ist nicht aktiv")
    policy = peer.get("policy") or {}
    section = policy.get("network_boot", {}) if isinstance(policy, dict) else {}
    if not isinstance(section, dict) or section.get(capability) is not True:
        label = "Networkboot anbieten" if capability == POLICY_OFFERS else "Networkboot-Daten vorhalten"
        raise ValueError(f"Beim Peer ist die Option '{label}' nicht freigegeben")
    return store, peer, store.peer_token(peer_id)


def portable_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Return only host-independent data suitable for federation storage."""
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
    # Service state stays machine-local: bind addresses, ports, enable flags and
    # the HTTP base URL must never be overwritten by another instance.
    return result


def fetch_from_offering_peer(root: str | Path, peer_id: str, config_path: str | Path) -> dict[str, Any]:
    """Fetch boot data from a peer marked as ``offers_network_boot``."""
    store, peer, token = _peer(root, peer_id, POLICY_OFFERS)
    headers = {"X-SimpleOffice-Peer-ID": _local_peer_id()}
    with _request(
        peer["base_url"] + "/federation/v1/network-boot/manifest",
        token=token,
        headers=headers,
        timeout=60,
    ) as response:
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
        url = peer["base_url"] + "/federation/v1/network-boot/assets/" + quote(relative, safe="/")
        with _request(url, token=token, headers=headers, timeout=300) as response:
            result = store_asset(response, relative, config_path)
        if result["sha256"] != digest:
            raise ValueError(f"SHA-256 stimmt nach Download nicht: {relative}")
        downloaded += 1

    remote_settings = manifest.get("settings") if isinstance(manifest.get("settings"), dict) else {}
    local_settings = load_boot_settings(config_path)
    saved = save_boot_settings(
        merge_portable_settings(local_settings, portable_settings(remote_settings)),
        config_path,
    )
    detail = {"downloaded": downloaded, "unchanged": unchanged, "profiles": len(saved["profiles"])}
    store.record_event("network_boot_fetched_from_offering_peer", peer_id=peer_id, detail=detail)
    return detail


def replicate_to_storage_peer(root: str | Path, peer_id: str, config_path: str | Path) -> dict[str, Any]:
    """Replicate local boot data to a peer marked as ``stores_network_boot``."""
    store, peer, token = _peer(root, peer_id, POLICY_STORES)
    local_id = _local_peer_id()
    headers = {"X-SimpleOffice-Peer-ID": local_id}
    uploaded = 0

    for row in list_assets(config_path):
        relative = row["path"]
        path = safe_asset_path(relative, config_path)
        # _request accepts bytes, so this remains bounded by the configured
        # network-boot asset maximum. Chunked SOFP transfer can replace this for
        # multi-GB ISO mirrors in a later optimization without changing policy.
        data = path.read_bytes()
        with _request(
            peer["base_url"] + "/federation/v1/network-boot/storage/assets/" + quote(relative, safe="/"),
            method="PUT",
            token=token,
            body=data,
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "X-Content-SHA256": row["sha256"],
            },
            timeout=300,
        ) as response:
            response.read()
        uploaded += 1

    settings = portable_settings(load_boot_settings(config_path))
    body = json.dumps(settings, ensure_ascii=False).encode("utf-8")
    with _request(
        peer["base_url"] + "/federation/v1/network-boot/storage/settings",
        method="PUT",
        token=token,
        body=body,
        headers={**headers, "Content-Type": "application/json"},
        timeout=60,
    ) as response:
        response.read()

    detail = {"uploaded": uploaded, "profiles": len(settings.get("profiles", []))}
    store.record_event("network_boot_replicated_to_storage_peer", peer_id=peer_id, detail=detail)
    return detail


# Compatibility aliases for branches/tools that may already import the earlier
# names. New UI/code must use the explicit peer-role names above.
pull_network_boot = fetch_from_offering_peer
push_network_boot = replicate_to_storage_peer
