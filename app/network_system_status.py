"""Small, side-effect-free helpers for DHCP interface and routing diagnostics."""
from __future__ import annotations

import platform
import socket
from pathlib import Path
from typing import Any
from simpleoffice_network_gateway import interfaces_snapshot


def _interface_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for index, name in socket.if_nameindex():
            rows.append({"index": int(index), "name": str(name), "state": "unknown", "ipv4": []})
    except OSError:
        pass
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
