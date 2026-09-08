"""Cross-platform routing/NAT planning for Mini Services.

Linux uses a dedicated nftables table owned by SimpleOffice and sysctl forwarding.
Windows uses PowerShell Set-NetIPInterface and New-NetNat when available. All
commands are generated from validated values; no user-provided shell fragments
are executed.
"""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

SAFE_IFACE = re.compile(r"^[A-Za-z0-9_.:@ -]{1,128}$")
DEFAULT_GATEWAY_SETTINGS: dict[str, Any] = {
    "version": 1,
    "enabled": False,
    "mode": "off",  # off|route|nat
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


def platform_kind() -> str:
    if os.name == "nt": return "windows"
    if sys_platform().startswith("linux"): return "linux"
    return "other"


def sys_platform() -> str:
    return __import__("sys").platform


def validate_gateway_settings(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("Gateway-Konfiguration muss ein Objekt sein")
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
    if data["enabled"] and mode != "off" and not data["auto_detect"] and not data["internal_interface"]:
        raise ValueError("Interne Schnittstelle fehlt")
    if data["enabled"] and mode == "nat" and not data["auto_detect"] and not data["external_interface"]:
        raise ValueError("Externe Schnittstelle fehlt")
    return data


def _run(args: list[str], timeout: int = 10) -> dict[str, Any]:
    executable = shutil.which(args[0])
    if not executable:
        return {"ok": False, "missing": True, "stdout": "", "stderr": "", "returncode": None}
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
        addresses = _run(["ip", "-j", "address", "show"])
        routes = _run(["ip", "-j", "route", "show", "default"])
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
        script = "Get-NetIPConfiguration | Select-Object InterfaceAlias,NetAdapter,IPv4Address,IPv4DefaultGateway | ConvertTo-Json -Depth 6"
        result = _powershell(script)
        try: data = json.loads(result["stdout"]) if result["ok"] else []
        except json.JSONDecodeError: data = []
        if isinstance(data, dict): data = [data]
        rows = []; defaults = []
        for item in data if isinstance(data, list) else []:
            name = str(item.get("InterfaceAlias") or "")
            ips_raw = item.get("IPv4Address") or []
            if isinstance(ips_raw, dict): ips_raw = [ips_raw]
            ips = [str(x.get("IPAddress") or "") for x in ips_raw if isinstance(x, dict) and x.get("IPAddress")]
            if item.get("IPv4DefaultGateway"): defaults.append(name)
            rows.append({"name": name, "state": "", "addresses": ips, "loopback": False})
        return {"platform": kind, "interfaces": rows, "default_interfaces": defaults, "netnat_available": _powershell("if (Get-Command New-NetNat -ErrorAction SilentlyContinue) { '1' } else { '0' }")["stdout"].strip() == "1"}
    return {"platform": kind, "interfaces": [], "default_interfaces": []}


def detect_interfaces(internal_network: str, *, server_ip: str = "") -> dict[str, Any]:
    network = ipaddress.ip_network(internal_network, strict=False)
    snapshot = interfaces_snapshot(); internal = ""; external = ""
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
    internal = data["internal_interface"] or detected["internal_interface"]
    external = data["external_interface"] or detected["external_interface"]
    warnings = []
    if data["enabled"] and data["mode"] != "off" and not internal: warnings.append("Interne Schnittstelle konnte nicht erkannt werden")
    if data["enabled"] and data["mode"] == "nat" and not external: warnings.append("Externe Schnittstelle konnte nicht erkannt werden")
    if internal and external and internal == external: warnings.append("Interne und externe Schnittstelle sind identisch")
    return {**data, "effective_internal_interface": internal, "effective_external_interface": external, "warnings": warnings, "platform": detected["snapshot"].get("platform", platform_kind()), "interfaces": detected["snapshot"]}


def linux_nft_script(settings: dict[str, Any], *, server_ip: str = "") -> str:
    data = effective_gateway(settings, server_ip=server_ip)
    internal = data["effective_internal_interface"]; external = data["effective_external_interface"]
    if data["warnings"]: raise ValueError("; ".join(data["warnings"]))
    lines = ["flush table inet simpleoffice_mini", "table inet simpleoffice_mini {", " chain forward {", "  type filter hook forward priority filter; policy drop;"]
    if data["allow_established"]: lines.append("  ct state established,related accept")
    if data["allow_lan_to_wan"] and internal and external: lines.append(f'  iifname "{internal}" oifname "{external}" accept')
    if data["allow_wan_to_lan"] and internal and external: lines.append(f'  iifname "{external}" oifname "{internal}" accept')
    lines.extend([" }", "}"])
    if data["mode"] == "nat":
        lines.extend(["table ip simpleoffice_mini_nat {", " chain postrouting {", "  type nat hook postrouting priority srcnat; policy accept;", f'  ip saddr {data["internal_network"]} oifname "{external}" masquerade', " }", "}"])
    return "\n".join(lines) + "\n"


def apply_gateway(settings: dict[str, Any], *, server_ip: str = "") -> dict[str, Any]:
    data = effective_gateway(settings, server_ip=server_ip)
    if data["warnings"]: raise ValueError("; ".join(data["warnings"]))
    if not data["enabled"] or data["mode"] == "off": return disable_gateway(data)
    if data["platform"] == "linux":
        if not shutil.which("nft") or not shutil.which("sysctl"): raise RuntimeError("nftables/sysctl fehlen")
        if data["forward_ipv4"]:
            result = _run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
            if not result["ok"]: raise RuntimeError(result["stderr"] or "IPv4 forwarding fehlgeschlagen")
        script = linux_nft_script(data, server_ip=server_ip)
        result = subprocess.run([shutil.which("nft") or "nft", "-f", "-"], input=script, text=True, capture_output=True, timeout=15, check=False)
        if result.returncode: raise RuntimeError((result.stderr or result.stdout or "nft failed")[-2000:])
        return {"ok": True, "platform": "linux", "mode": data["mode"], "internal": data["effective_internal_interface"], "external": data["effective_external_interface"]}
    if data["platform"] == "windows":
        internal = data["effective_internal_interface"].replace("'", "''"); external = data["effective_external_interface"].replace("'", "''")
        script = [f"Set-NetIPInterface -InterfaceAlias '{internal}' -AddressFamily IPv4 -Forwarding Enabled -ErrorAction Stop"]
        if external: script.append(f"Set-NetIPInterface -InterfaceAlias '{external}' -AddressFamily IPv4 -Forwarding Enabled -ErrorAction Stop")
        if data["mode"] == "nat":
            name = data["nat_name"].replace("'", "''"); prefix = data["internal_network"]
            script.append("if (-not (Get-Command New-NetNat -ErrorAction SilentlyContinue)) { throw 'New-NetNat unavailable' }")
            script.append(f"Get-NetNat -Name '{name}' -ErrorAction SilentlyContinue | Remove-NetNat -Confirm:$false -ErrorAction SilentlyContinue")
            script.append(f"New-NetNat -Name '{name}' -InternalIPInterfaceAddressPrefix '{prefix}' -ErrorAction Stop | Out-Null")
        result = _powershell("; ".join(script), 30)
        if not result["ok"]: raise RuntimeError(result["stderr"] or result["stdout"] or "Windows routing/NAT fehlgeschlagen")
        return {"ok": True, "platform": "windows", "mode": data["mode"], "internal": data["effective_internal_interface"], "external": data["effective_external_interface"]}
    raise RuntimeError("Routing/NAT wird auf dieser Plattform nicht unterstützt")


def disable_gateway(settings: dict[str, Any]) -> dict[str, Any]:
    data = validate_gateway_settings(settings); kind = platform_kind()
    if kind == "linux" and shutil.which("nft"):
        _run(["nft", "delete", "table", "inet", "simpleoffice_mini"]); _run(["nft", "delete", "table", "ip", "simpleoffice_mini_nat"])
        return {"ok": True, "platform": kind, "mode": "off"}
    if kind == "windows":
        name = data["nat_name"].replace("'", "''")
        result = _powershell(f"Get-NetNat -Name '{name}' -ErrorAction SilentlyContinue | Remove-NetNat -Confirm:$false -ErrorAction SilentlyContinue", 20)
        return {"ok": bool(result["ok"] or result["missing"]), "platform": kind, "mode": "off"}
    return {"ok": True, "platform": kind, "mode": "off"}
