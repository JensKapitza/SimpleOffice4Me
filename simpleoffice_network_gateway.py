"""Cross-platform routing/NAT planning without Flask imports."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

SAFE_IFACE = re.compile(r"^[A-Za-z0-9_.:@ -]{1,128}$")
DEFAULT_GATEWAY_SETTINGS: dict[str, Any] = {
    "version": 1,
    "enabled": False,
    "mode": "off",
    "auto_detect": True,
    "internal_interface": "",
    "external_interface": "",
    "internal_network": "192.168.178.0/24",
    "forward_ipv4": True,
    "allow_established": True,
    "allow_lan_to_wan": True,
    "allow_wan_to_lan": False,
    "nat_name": "SimpleOfficeMiniNat",
}


def sys_platform() -> str:
    return __import__("sys").platform


def platform_kind() -> str:
    if os.name == "nt": return "windows"
    if sys_platform().startswith("linux"): return "linux"
    return "other"


def validate_gateway_settings(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict): raise ValueError("Gateway-Konfiguration muss ein Objekt sein")
    data = deepcopy(DEFAULT_GATEWAY_SETTINGS); data.update(candidate); data["version"] = 1
    data["enabled"] = bool(data.get("enabled")); data["auto_detect"] = bool(data.get("auto_detect", True))
    data["forward_ipv4"] = bool(data.get("forward_ipv4", True)); data["allow_established"] = bool(data.get("allow_established", True))
    data["allow_lan_to_wan"] = bool(data.get("allow_lan_to_wan", True)); data["allow_wan_to_lan"] = bool(data.get("allow_wan_to_lan", False))
    mode = str(data.get("mode") or "off").strip().casefold()
    if mode not in {"off", "route", "nat"}: raise ValueError("Gateway-Modus muss off, route oder nat sein")
    data["mode"] = mode
    network = ipaddress.ip_network(str(data.get("internal_network") or ""), strict=False)
    if network.version != 4: raise ValueError("Gateway unterstützt derzeit nur IPv4-Netze")
    data["internal_network"] = str(network)
    for key in ("internal_interface", "external_interface"):
        value = str(data.get(key) or "").strip()
        if value and not SAFE_IFACE.fullmatch(value): raise ValueError(f"Ungültige Schnittstelle: {value}")
        data[key] = value
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", str(data.get("nat_name") or "SimpleOfficeMiniNat"))[:80]
    data["nat_name"] = name or "SimpleOfficeMiniNat"
    return data


def _run(args: list[str], timeout: int = 10) -> dict[str, Any]:
    executable = shutil.which(args[0])
    if not executable: return {"ok": False, "missing": True, "stdout": "", "stderr": "", "returncode": None}
    try:
        result = subprocess.run([executable, *args[1:]], stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "missing": False, "stdout": "", "stderr": str(exc)[:2000], "returncode": None}
    return {"ok": result.returncode == 0, "missing": False, "stdout": result.stdout[:200000], "stderr": result.stderr[:20000], "returncode": result.returncode}


def _powershell(script: str, timeout: int = 15) -> dict[str, Any]:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if not executable: return {"ok": False, "missing": True, "stdout": "", "stderr": "PowerShell fehlt", "returncode": None}
    return _run([Path(executable).name, "-NoProfile", "-NonInteractive", "-Command", script], timeout)


def interfaces_snapshot() -> dict[str, Any]:
    kind = platform_kind()
    if kind == "linux":
        addresses = _run(["ip", "-j", "address", "show"]); routes = _run(["ip", "-j", "route", "show", "default"])
        try: addr_data = json.loads(addresses["stdout"]) if addresses["ok"] else []
        except json.JSONDecodeError: addr_data = []
        try: route_data = json.loads(routes["stdout"]) if routes["ok"] else []
        except json.JSONDecodeError: route_data = []
        rows = []
        for item in addr_data if isinstance(addr_data, list) else []:
            ips = [f"{a.get('local')}/{a.get('prefixlen')}" for a in item.get("addr_info", []) if isinstance(a, dict) and a.get("family") == "inet" and a.get("local")]
            rows.append({"name": str(item.get("ifname") or ""), "state": str(item.get("operstate") or ""), "addresses": ips, "loopback": str(item.get("link_type") or "") == "loopback"})
        defaults = [str(row.get("dev") or "") for row in route_data if isinstance(row, dict) and row.get("dev")]
        return {"platform": kind, "interfaces": rows, "default_interfaces": defaults}
    if kind == "windows":
        result = _powershell("Get-NetIPConfiguration | Select-Object InterfaceAlias,IPv4Address,IPv4DefaultGateway | ConvertTo-Json -Depth 6")
        try: data = json.loads(result["stdout"]) if result["ok"] else []
        except json.JSONDecodeError: data = []
        if isinstance(data, dict): data = [data]
        rows = []; defaults = []
        for item in data if isinstance(data, list) else []:
            name = str(item.get("InterfaceAlias") or ""); ips_raw = item.get("IPv4Address") or []
            if isinstance(ips_raw, dict): ips_raw = [ips_raw]
            ips = [str(x.get("IPAddress") or "") for x in ips_raw if isinstance(x, dict) and x.get("IPAddress")]
            if item.get("IPv4DefaultGateway"): defaults.append(name)
            rows.append({"name": name, "state": "", "addresses": ips, "loopback": False})
        return {"platform": kind, "interfaces": rows, "default_interfaces": defaults, "netnat_available": _powershell("if (Get-Command New-NetNat -ErrorAction SilentlyContinue) { '1' } else { '0' }")["stdout"].strip() == "1"}
    return {"platform": kind, "interfaces": [], "default_interfaces": []}


def detect_interfaces(internal_network: str, *, server_ip: str = "") -> dict[str, Any]:
    network = ipaddress.ip_network(internal_network, strict=False); snapshot = interfaces_snapshot(); internal = ""; external = ""
    server = ipaddress.ip_address(server_ip) if server_ip else None
    for row in snapshot.get("interfaces", []):
        if row.get("loopback"): continue
        for value in row.get("addresses", []):
            try: addr = ipaddress.ip_interface(value).ip if "/" in value else ipaddress.ip_address(value)
            except ValueError: continue
            if (server and addr == server) or addr in network:
                internal = str(row.get("name") or ""); break
        if internal: break
    for name in snapshot.get("default_interfaces", []):
        if name and name != internal: external = name; break
    return {"internal_interface": internal, "external_interface": external, "snapshot": snapshot}


def effective_gateway(settings: dict[str, Any], *, server_ip: str = "") -> dict[str, Any]:
    data = validate_gateway_settings(settings)
    detected = detect_interfaces(data["internal_network"], server_ip=server_ip) if data["auto_detect"] else {"internal_interface": "", "external_interface": "", "snapshot": interfaces_snapshot()}
    internal = data["internal_interface"] or detected["internal_interface"]; external = data["external_interface"] or detected["external_interface"]
    warnings = []
    if data["enabled"] and data["mode"] != "off" and not internal: warnings.append("Interne Schnittstelle konnte nicht erkannt werden")
    if data["enabled"] and data["mode"] == "nat" and not external: warnings.append("Externe Schnittstelle konnte nicht erkannt werden")
    if internal and external and internal == external: warnings.append("Interne und externe Schnittstelle sind identisch")
    return {**data, "effective_internal_interface": internal, "effective_external_interface": external, "warnings": warnings, "platform": detected["snapshot"].get("platform", platform_kind()), "interfaces": detected["snapshot"]}
