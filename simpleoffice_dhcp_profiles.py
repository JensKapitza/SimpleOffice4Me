"""Additional DHCP profiles for systems with multiple network interfaces."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from simpleoffice_mini_core import DEFAULT_CONFIG, _atomic_write, state_dir, validate_config
from simpleoffice_mini_runtime import LeaseStore
from simpleoffice_network_boot_dhcp import BootAwareDhcpService

_PROFILE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
_COMMON_FIELDS = (
    "lease_time", "renewal_time", "rebinding_time", "authoritative", "ping_check",
    "decline_hold_seconds", "mtu", "domain", "domain_search", "ntp_servers",
    "next_server", "tftp_server", "boot_file", "custom_options",
)


def profiles_path(config_path: str | Path) -> Path:
    return state_dir(config_path) / "dhcp-profiles.json"


def profile_leases_path(config_path: str | Path, profile_id: str) -> Path:
    if not _PROFILE_ID.fullmatch(str(profile_id or "")):
        raise ValueError("Ungültige DHCP-Profil-ID")
    return state_dir(config_path) / f"dhcp-leases-{profile_id}.json"


def validate_profiles(rows: Any, primary: dict[str, Any]) -> list[dict[str, Any]]:
    if rows in (None, ""):
        return []
    if not isinstance(rows, list):
        raise ValueError("Zusätzliche DHCP-Profile müssen eine Liste sein")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    active_interfaces: set[str] = set()
    sockets: set[tuple[str, str, int]] = set()
    primary_enabled = bool(primary.get("enabled"))
    primary_interface = str(primary.get("interface") or "").strip()
    if primary_enabled and primary_interface:
        active_interfaces.add(primary_interface)
        sockets.add((primary_interface, str(primary.get("bind") or ""), int(primary.get("port", 67))))
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("DHCP-Profil muss ein Objekt sein")
        profile_id = str(row.get("id") or f"net-{index + 2}").strip()
        if not _PROFILE_ID.fullmatch(profile_id) or profile_id in ids:
            raise ValueError(f"Ungültige oder doppelte DHCP-Profil-ID: {profile_id}")
        candidate = deepcopy(DEFAULT_CONFIG["dhcp"])
        for field in _COMMON_FIELDS:
            if field in primary:
                candidate[field] = deepcopy(primary[field])
        candidate.update({key: deepcopy(value) for key, value in row.items() if key != "id"})
        candidate.setdefault("reservations", [])
        candidate.setdefault("exclusions", [])
        candidate.setdefault("static_routes", [])
        clean = validate_config({"dhcp": candidate, "dns": DEFAULT_CONFIG["dns"]})["dhcp"]
        if clean["enabled"]:
            if not clean["interface"]:
                raise ValueError(f"DHCP-Profil {profile_id} benötigt ein Interface")
            if primary_enabled and not primary_interface:
                raise ValueError("Für zusätzliche aktive DHCP-Netze muss auch das Haupt-DHCP auf ein Interface festgelegt sein")
            if clean["interface"] in active_interfaces:
                raise ValueError(f"Auf Interface {clean['interface']} ist bereits ein DHCP-Server aktiv")
            socket_key = (clean["interface"], clean["bind"], clean["port"])
            if socket_key in sockets:
                raise ValueError(f"DHCP-Profil {profile_id} verwendet bereits Interface/Bind/Port")
            active_interfaces.add(clean["interface"])
            sockets.add(socket_key)
        clean["id"] = profile_id
        result.append(clean)
        ids.add(profile_id)
    return result


def load_profiles(config_path: str | Path, primary: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        value = json.loads(profiles_path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {"profiles": []}
    rows = value.get("profiles", []) if isinstance(value, dict) else []
    return validate_profiles(rows, primary)


def save_profiles(rows: Any, config_path: str | Path, primary: dict[str, Any]) -> list[dict[str, Any]]:
    clean = validate_profiles(rows, primary)
    payload = {"version": 1, "profiles": clean}
    _atomic_write(profiles_path(config_path), (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return clean


def read_profile_leases(config_path: str | Path, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for profile in profiles:
        store = LeaseStore(profile_leases_path(config_path, profile["id"]))
        store.cleanup()
        rows.extend({**item, "dhcp_profile": profile["id"]} for item in list(store.leases))
    return rows


def clear_profile_leases(config_path: str | Path, profiles: list[dict[str, Any]]) -> None:
    for profile in profiles:
        _atomic_write(profile_leases_path(config_path, profile["id"]), b'{"leases": []}\n')


class ProfileDhcpService(BootAwareDhcpService):
    """Boot-aware DHCP service with a lease database isolated per profile."""

    def __init__(self, config: dict[str, Any], config_path: Path, profile_id: str, event=None):
        super().__init__(config, config_path, event)
        self.profile_id = profile_id
        self.leases = LeaseStore(profile_leases_path(config_path, profile_id))
