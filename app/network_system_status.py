"""Small, side-effect-free helpers for DHCP interface and routing diagnostics."""
from __future__ import annotations

import ipaddress
import platform
import socket
from pathlib import Path
from typing import Any
from simpleoffice_network_gateway import interfaces_snapshot


def _interface_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if_nameindex = getattr(socket, "if_nameindex", None)
        if not callable(if_nameindex):
            return rows
        interface_names = if_nameindex()
    except (AttributeError, OSError):
        return rows
    for index, name in interface_names:
        rows.append({"index": int(index), "name": str(name), "state": "unknown", "ipv4": []})
    return rows


def network_interfaces() -> list[dict[str, Any]]:
    """Return detected interfaces with IPv4 addresses when the OS exposes them."""
    rows = _interface_rows()
    by_name = {row["name"]: row for row in rows}
    for item in interfaces_snapshot().get("interfaces", []):
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        row = by_name.setdefault(name, {"index": item.get("index", 0), "name": name})
        row.update(state=str(item.get("state") or "unknown").lower(), ipv4=item.get("addresses", []))
    return sorted(by_name.values(), key=lambda row: (row["name"] == "lo", row["name"].casefold()))


def ipv4_routing_status() -> dict[str, Any]:
    """Report IPv4 forwarding state without changing the host configuration."""
    system = platform.system().lower()
    if system == "linux":
        try:
            value = Path("/proc/sys/net/ipv4/ip_forward").read_text(encoding="ascii").strip()
            enabled = value == "1"
            return {
                "platform": "linux",
                "enabled": enabled,
                "known": value in {"0", "1"},
                "temporary_command": "sudo sysctl -w net.ipv4.ip_forward=1",
                "persistent_command": "echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-simpleoffice-routing.conf && sudo sysctl --system",
            }
        except OSError:
            return {"platform": "linux", "enabled": False, "known": False}
    if system == "windows":
        return {
            "platform": "windows",
            "enabled": False,
            "known": False,
            "temporary_command": "Set-NetIPInterface -AddressFamily IPv4 -Forwarding Enabled",
            "persistent_command": "Set-NetIPInterface -AddressFamily IPv4 -Forwarding Enabled",
        }
    return {"platform": system or "unknown", "enabled": False, "known": False}


def dhcp_network_candidates(
    interfaces: list[dict[str, Any]],
    configured_network: str = "",
    configured_server_ip: str = "",
) -> list[dict[str, Any]]:
    """Return explicit IPv4 candidates without enabling or guessing DHCP state."""
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    configured_network = str(configured_network or "").strip()
    configured_server_ip = str(configured_server_ip or "").strip()
    for item in interfaces:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        for value in item.get("ipv4", []) or []:
            try:
                interface = ipaddress.ip_interface(str(value))
            except ValueError:
                continue
            address = interface.ip
            network = interface.network
            if (
                address.version != 4
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
                or network.prefixlen > 30
            ):
                continue
            key = (name, str(network))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "interface": name,
                    "address": str(address),
                    "network": str(network),
                    "selected": configured_network == str(network)
                    and (not configured_server_ip or configured_server_ip == str(address)),
                }
            )
    return sorted(
        candidates,
        key=lambda row: (
            not row["selected"],
            row["interface"].casefold(),
            ipaddress.ip_network(row["network"]).network_address,
        ),
    )
