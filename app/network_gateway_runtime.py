"""Persistent runtime wrapper for Mini Services routing/NAT."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .mini_services import _atomic_write, default_config_path, state_dir
from .network_gateway import (
    DEFAULT_GATEWAY_SETTINGS,
    _powershell,
    _run,
    effective_gateway,
    platform_kind,
    validate_gateway_settings,
)


def gateway_settings_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path or default_config_path()) / "gateway.json"


def load_gateway_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    path = gateway_settings_path(config_path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = DEFAULT_GATEWAY_SETTINGS
    return validate_gateway_settings(value)


def save_gateway_settings(value: dict[str, Any], config_path: str | Path | None = None) -> dict[str, Any]:
    clean = validate_gateway_settings(value)
    _atomic_write(gateway_settings_path(config_path), (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return clean


def _linux_script(data: dict[str, Any]) -> str:
    internal = data["effective_internal_interface"]
    external = data["effective_external_interface"]
    lines = [
        "table inet simpleoffice_mini {",
        " chain forward {",
        "  type filter hook forward priority filter; policy drop;",
    ]
    if data["allow_established"]:
        lines.append("  ct state established,related accept")
    if data["allow_lan_to_wan"] and internal and external:
        lines.append(f'  iifname "{internal}" oifname "{external}" accept')
    if data["allow_wan_to_lan"] and internal and external:
        lines.append(f'  iifname "{external}" oifname "{internal}" accept')
    lines.extend([" }", "}"])
    if data["mode"] == "nat":
        lines.extend([
            "table ip simpleoffice_mini_nat {",
            " chain postrouting {",
            "  type nat hook postrouting priority srcnat; policy accept;",
            f'  ip saddr {data["internal_network"]} oifname "{external}" masquerade',
            " }",
            "}",
        ])
    return "\n".join(lines) + "\n"


def disable_gateway(value: dict[str, Any]) -> dict[str, Any]:
    data = validate_gateway_settings(value)
    kind = platform_kind()
    if kind == "linux":
        if shutil.which("nft"):
            _run(["nft", "delete", "table", "inet", "simpleoffice_mini"])
            _run(["nft", "delete", "table", "ip", "simpleoffice_mini_nat"])
        return {"ok": True, "platform": kind, "mode": "off"}
    if kind == "windows":
        name = data["nat_name"].replace("'", "''")
        result = _powershell(
            f"Get-NetNat -Name '{name}' -ErrorAction SilentlyContinue | Remove-NetNat -Confirm:$false -ErrorAction SilentlyContinue",
            20,
        )
        return {"ok": bool(result["ok"] or result["missing"]), "platform": kind, "mode": "off"}
    return {"ok": True, "platform": kind, "mode": "off"}


def apply_gateway(value: dict[str, Any], *, server_ip: str = "") -> dict[str, Any]:
    data = effective_gateway(value, server_ip=server_ip)
    if data["warnings"]:
        raise ValueError("; ".join(data["warnings"]))
    if not data["enabled"] or data["mode"] == "off":
        return disable_gateway(data)
    if data["platform"] == "linux":
        nft = shutil.which("nft")
        sysctl = shutil.which("sysctl")
        if not nft or not sysctl:
            raise RuntimeError("nftables und iproute/sysctl werden für Routing/NAT benötigt")
        if data["forward_ipv4"]:
            result = _run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
            if not result["ok"]:
                raise RuntimeError(str(result["stderr"] or "IPv4 forwarding fehlgeschlagen"))
        # Only tables owned by SimpleOffice are replaced. Existing firewall
        # tables/chains from administrators, Docker, firewalld etc. are untouched.
        _run(["nft", "delete", "table", "inet", "simpleoffice_mini"])
        _run(["nft", "delete", "table", "ip", "simpleoffice_mini_nat"])
        result = subprocess.run(
            [nft, "-f", "-"], input=_linux_script(data), text=True,
            capture_output=True, timeout=15, check=False,
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "nft failed")[-2000:])
        return {
            "ok": True, "platform": "linux", "mode": data["mode"],
            "internal": data["effective_internal_interface"],
            "external": data["effective_external_interface"],
        }
    if data["platform"] == "windows":
        internal = data["effective_internal_interface"].replace("'", "''")
        external = data["effective_external_interface"].replace("'", "''")
        script = [
            f"Set-NetIPInterface -InterfaceAlias '{internal}' -AddressFamily IPv4 -Forwarding Enabled -ErrorAction Stop"
        ]
        if external:
            script.append(
                f"Set-NetIPInterface -InterfaceAlias '{external}' -AddressFamily IPv4 -Forwarding Enabled -ErrorAction Stop"
            )
        if data["mode"] == "nat":
            name = data["nat_name"].replace("'", "''")
            script.extend([
                "if (-not (Get-Command New-NetNat -ErrorAction SilentlyContinue)) { throw 'New-NetNat unavailable on this Windows installation' }",
                f"Get-NetNat -Name '{name}' -ErrorAction SilentlyContinue | Remove-NetNat -Confirm:$false -ErrorAction SilentlyContinue",
                f"New-NetNat -Name '{name}' -InternalIPInterfaceAddressPrefix '{data['internal_network']}' -ErrorAction Stop | Out-Null",
            ])
        result = _powershell("; ".join(script), 30)
        if not result["ok"]:
            raise RuntimeError(str(result["stderr"] or result["stdout"] or "Windows Routing/NAT fehlgeschlagen"))
        return {
            "ok": True, "platform": "windows", "mode": data["mode"],
            "internal": data["effective_internal_interface"],
            "external": data["effective_external_interface"],
        }
    raise RuntimeError("Routing/NAT wird auf dieser Plattform nicht unterstützt")
