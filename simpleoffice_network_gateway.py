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
from simpleoffice_mini_core import config_bool

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
    for key in ("enabled", "auto_detect", "forward_ipv4", "allow_established", "allow_lan_to_wan", "allow_wan_to_lan"):
        data[key] = config_bool(data[key], key)
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
        addresses = _run(["ip", "-j", "address", "show"], 3); routes = _run(["ip", "-j", "route", "show", "default"], 3)
        try: addr_data = json.loads(addresses["stdout"]) if addresses["ok"] else []
        except json.JSONDecodeError: addr_data = None
        try: route_data = json.loads(routes["stdout"]) if routes["ok"] else []
        except json.JSONDecodeError: route_data = []
        rows = []
        for item in addr_data if isinstance(addr_data, list) else []:
            if not isinstance(item, dict):
                continue
            ips = [f"{a.get('local')}/{a.get('prefixlen')}" for a in item.get("addr_info", []) if isinstance(a, dict) and a.get("family") in {"inet", "inet6"} and a.get("local")
                   and not a.get("tentative") and not a.get("dadfailed")
                   and not {"tentative", "dadfailed"}.intersection(a.get("flags") or [])]
            rows.append({"index": item.get("ifindex", 0), "name": str(item.get("ifname") or ""), "state": str(item.get("operstate") or ""), "addresses": ips, "loopback": str(item.get("link_type") or "") == "loopback"})
        defaults = [str(row.get("dev") or "") for row in route_data if isinstance(row, dict) and row.get("dev")]
        return {"platform": kind, "available": bool(addresses["ok"] and isinstance(addr_data, list)), "address_families": [4, 6], "interfaces": rows, "default_interfaces": defaults}
    if kind == "windows":
        result = _powershell("Get-NetIPConfiguration | Select-Object InterfaceAlias,InterfaceIndex,IPv4Address,IPv4DefaultGateway,@{Name='Status';Expression={$_.NetAdapter.Status}} | ConvertTo-Json -Depth 6", 3)
        try: data = json.loads(result["stdout"]) if result["ok"] else []
        except json.JSONDecodeError: data = None
        if isinstance(data, dict): data = [data]
        rows = []; defaults = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("InterfaceAlias") or ""); ips_raw = item.get("IPv4Address") or []
            if isinstance(ips_raw, dict): ips_raw = [ips_raw]
            ips = [str(x["IPAddress"]) + ("/" + str(x["PrefixLength"]) if x.get("PrefixLength") is not None else "") for x in ips_raw if isinstance(x, dict) and x.get("IPAddress")]
            if item.get("IPv4DefaultGateway"): defaults.append(name)
            rows.append({"index": item.get("InterfaceIndex", 0), "name": name, "state": str(item.get("Status") or "unknown"), "addresses": ips, "loopback": False})
        return {"platform": kind, "available": bool(result["ok"] and isinstance(data, list)), "interfaces": rows, "default_interfaces": defaults, "netnat_available": _powershell("if (Get-Command New-NetNat -ErrorAction SilentlyContinue) { '1' } else { '0' }", 3)["stdout"].strip() == "1"}
    return {"platform": kind, "available": False, "interfaces": [], "default_interfaces": []}


def detect_interfaces(internal_network: str, *, server_ip: str = "", snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    network = ipaddress.ip_network(internal_network, strict=False); snapshot = interfaces_snapshot() if snapshot is None else snapshot; internal = ""; external = ""
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


def binding_available(settings: dict[str, Any], snapshot: dict[str, Any], *, addresses=(), interfaces=()) -> bool:
    """Unknown OS inventory never means missing hardware; socket bind still decides."""
    if not snapshot.get("available"):
        return True
    rows = [row for row in snapshot.get("interfaces", []) if str(row.get("state", "")).lower() not in {"down", "notpresent", "disconnected", "lowerlayerdown"}]
    names = {row["name"] for row in rows}
    assigned = []
    for row in rows:
        for value in row.get("addresses", []):
            try:
                address = ipaddress.ip_interface(value).ip if "/" in value else ipaddress.ip_address(value)
            except ValueError:
                continue
            assigned.append((address, str(row.get("name", "")), str(row.get("index", ""))))
    # Older snapshots and the Windows inventory only guarantee IPv4 coverage.
    families = snapshot.get("address_families", [4])
    for key in interfaces:
        if settings.get(key) and settings[key] not in names:
            return False
    for key in addresses:
        values = settings.get(key) or []
        for value in values if isinstance(values, list) else [values]:
            address = ipaddress.ip_address(value)
            if address.version not in families or address.is_loopback or address.is_unspecified:
                continue
            scope = getattr(address, "scope_id", None)
            if not any(address.packed == candidate.packed and
                       (scope is None or scope in {name, index})
                       for candidate, name, index in assigned):
                return False
    return True


def effective_gateway(settings: dict[str, Any], *, server_ip: str = "") -> dict[str, Any]:
    data = validate_gateway_settings(settings)
    detected = detect_interfaces(data["internal_network"], server_ip=server_ip) if data["auto_detect"] else {"internal_interface": "", "external_interface": "", "snapshot": interfaces_snapshot()}
    internal = data["internal_interface"] or detected["internal_interface"]; external = data["external_interface"] or detected["external_interface"]
    for interface in (internal, external):
        if interface and not SAFE_IFACE.fullmatch(interface):
            raise ValueError("Erkannte Schnittstelle enthält nicht unterstützte Zeichen")
    warnings = []
    if data["enabled"] and data["mode"] != "off" and not internal: warnings.append("Interne Schnittstelle konnte nicht erkannt werden")
    if data["enabled"] and data["mode"] == "nat" and not external: warnings.append("Externe Schnittstelle konnte nicht erkannt werden")
    if internal and external and internal == external: warnings.append("Interne und externe Schnittstelle sind identisch")
    return {**data, "effective_internal_interface": internal, "effective_external_interface": external, "warnings": warnings, "platform": detected["snapshot"].get("platform", platform_kind()), "interfaces": detected["snapshot"]}
