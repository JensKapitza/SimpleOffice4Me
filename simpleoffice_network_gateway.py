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


def _windows_inventory(data):
    """Reject incomplete scans rather than interpret them as lost bindings."""
    complete = isinstance(data, dict) and "interfaces" in data and "addresses" in data
    if complete:
        interfaces, addresses = data["interfaces"], data["addresses"]
        if not isinstance(interfaces, list) or not isinstance(addresses, list):
            raise ValueError("Windows-Netzwerkinventar unvollständig")
    else:
        # Compatibility with earlier IPv4-only snapshots.
        interfaces = [data] if isinstance(data, dict) else data
        addresses = []
        if not isinstance(interfaces, list):
            raise ValueError("Windows-Netzwerkinventar ungültig")
    rows, defaults = [], []
    for item in interfaces:
        if not isinstance(item, dict) or not item.get("InterfaceAlias") or type(item.get("InterfaceIndex")) is not int:
            raise ValueError("Windows-Interface ungültig")
        ips = []
        if not complete:
            raw = item.get("IPv4Address") or []
            for entry in [raw] if isinstance(raw, dict) else raw:
                ips.append(str(ipaddress.ip_interface(str(entry["IPAddress"]) + "/" + str(entry["PrefixLength"]))))
        name = item["InterfaceAlias"]
        if item.get("IPv4DefaultGateway"):
            defaults.append(name)
        rows.append({"index": item["InterfaceIndex"], "name": name, "state": str(item.get("Status") or "unknown"),
                     "addresses": ips, "loopback": False})
    by_index = {row["index"]: row for row in rows}
    if len(by_index) != len(rows):
        raise ValueError("Windows-Interfaceindex ist mehrdeutig")
    for entry in addresses:
        if not isinstance(entry, dict) or entry.get("InterfaceIndex") not in by_index:
            raise ValueError("Windows-Adresse ohne Interface")
        address = ipaddress.ip_interface(str(entry["IPAddress"]) + "/" + str(entry["PrefixLength"]))
        state = entry["State"]
        if state not in {"Preferred", "Deprecated", "Invalid", "Tentative", "Duplicate"}:
            raise ValueError("Windows-Adressstatus unbekannt")
        if state in {"Preferred", "Deprecated"}:
            by_index[entry["InterfaceIndex"]]["addresses"].append(str(address))
    return rows, defaults, [4, 6] if complete else [4]


def _linux_inventory(data):
    """A malformed address scan is unknown, never evidence of lost hardware."""
    if not isinstance(data, list):
        raise ValueError("Linux-Netzwerkinventar ungültig")
    rows = []
    for item in data:
        if not isinstance(item, dict) or not item.get("ifname") or not isinstance(item.get("addr_info"), list):
            raise ValueError("Linux-Interface unvollständig")
        ips = []
        for entry in item["addr_info"]:
            if not isinstance(entry, dict):
                raise ValueError("Linux-Adresse ungültig")
            family = entry.get("family")
            if family not in {"inet", "inet6"}:
                continue
            address = ipaddress.ip_interface(str(entry["local"]) + "/" + str(entry["prefixlen"]))
            if address.version != (4 if family == "inet" else 6):
                raise ValueError("Adressfamilie widersprüchlich")
            flags = entry.get("flags", [])
            if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
                raise ValueError("Adressflags ungültig")
            if not entry.get("tentative") and not entry.get("dadfailed") and not {"tentative", "dadfailed"}.intersection(flags):
                ips.append(str(address))
        rows.append({"index": item.get("ifindex", 0), "name": str(item["ifname"]),
                     "state": str(item.get("operstate") or ""), "addresses": ips,
                     "loopback": item.get("link_type") == "loopback"})
    return rows


def interfaces_snapshot() -> dict[str, Any]:
    kind = platform_kind()
    if kind == "linux":
        addresses = _run(["ip", "-j", "address", "show"], 3); routes = _run(["ip", "-j", "route", "show", "default"], 3)
        try:
            if not addresses["ok"]:
                raise ValueError("Interface-Abfrage fehlgeschlagen")
            rows = _linux_inventory(json.loads(addresses["stdout"]))
        except (ValueError, TypeError, KeyError):
            return {"platform": kind, "available": False, "interfaces": [], "default_interfaces": []}
        try:
            route_data = json.loads(routes["stdout"]) if routes["ok"] else []
        except (ValueError, TypeError):
            route_data = []
        defaults = [str(row["dev"]) for row in route_data if isinstance(row, dict) and row.get("dev")] if isinstance(route_data, list) else []
        return {"platform": kind, "available": True, "address_families": [4, 6], "interfaces": rows, "default_interfaces": defaults}
    if kind == "windows":
        script = ("$ErrorActionPreference='Stop'; "
                  "$a = @(Get-NetIPAddress -ErrorAction Stop | Select-Object InterfaceIndex,IPAddress,PrefixLength,@{Name='State';Expression={[string]$_.AddressState}}); "
                  "$i = @(Get-NetIPConfiguration -All -ErrorAction Stop | Select-Object InterfaceAlias,InterfaceIndex,IPv4DefaultGateway,@{Name='Status';Expression={$_.NetAdapter.Status}}); "
                  "@{interfaces=$i;addresses=$a} | ConvertTo-Json -Depth 6")
        result = _powershell(script, 3)
        try:
            data = json.loads(result["stdout"]) if result["ok"] else None
            rows, defaults, families = _windows_inventory(data)
        except (ValueError, TypeError, KeyError):
            return {"platform": kind, "available": False, "interfaces": [], "default_interfaces": []}
        return {"platform": kind, "available": True, "address_families": families,
                "interfaces": rows, "default_interfaces": defaults,
                "netnat_available": _powershell("if (Get-Command New-NetNat -ErrorAction SilentlyContinue) { '1' } else { '0' }", 3)["stdout"].strip() == "1"}
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
