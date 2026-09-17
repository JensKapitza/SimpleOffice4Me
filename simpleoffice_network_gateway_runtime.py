"""Persistent cross-platform routing/NAT runtime without Flask imports."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from simpleoffice_gateway_rule_content import expected_rules, normalize_expressions
from simpleoffice_mini_services import _atomic_write, default_config_path, state_dir
from simpleoffice_network_gateway import (
    DEFAULT_GATEWAY_SETTINGS,
    _powershell,
    _run,
    effective_gateway,
    platform_kind,
    validate_gateway_settings,
)


def gateway_settings_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path or default_config_path()) / "gateway.json"


def gateway_ownership_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path or default_config_path()) / "gateway-owned.json"


def load_gateway_ownership(config_path: str | Path | None = None) -> dict[str, Any] | None:
    path = gateway_ownership_path(config_path)
    if path.is_symlink():
        raise ValueError("Gateway-Ownership-Datei darf kein symbolischer Link sein")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("Gateway-Ownership-Datei ist ungültig") from exc
    return validate_gateway_settings(value)


def remember_gateway_ownership(value: dict[str, Any], config_path: str | Path | None = None) -> dict[str, Any]:
    clean = validate_gateway_settings(value)
    _atomic_write(
        gateway_ownership_path(config_path),
        (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return clean


def clear_gateway_ownership(config_path: str | Path | None = None) -> None:
    path = gateway_ownership_path(config_path)
    if path.is_symlink():
        raise ValueError("Gateway-Ownership-Datei darf kein symbolischer Link sein")
    path.unlink(missing_ok=True)


def load_gateway_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    path = gateway_settings_path(config_path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        value = DEFAULT_GATEWAY_SETTINGS
    return validate_gateway_settings(value)


def save_gateway_settings(value: dict[str, Any], config_path: str | Path | None = None) -> dict[str, Any]:
    clean = validate_gateway_settings(value)
    _atomic_write(gateway_settings_path(config_path), (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return clean


def _linux_script(data: dict[str, Any]) -> str:
    internal = data["effective_internal_interface"]; external = data["effective_external_interface"]
    lines = ["table inet simpleoffice_mini {", " chain forward {", "  type filter hook forward priority filter; policy drop;"]
    if data["allow_established"]: lines.append("  ct state established,related accept")
    if data["allow_lan_to_wan"] and internal and external: lines.append(f'  iifname "{internal}" oifname "{external}" accept')
    if data["allow_wan_to_lan"] and internal and external: lines.append(f'  iifname "{external}" oifname "{internal}" accept')
    lines.extend([" }", "}"])
    if data["mode"] == "nat":
        lines.extend(["table ip simpleoffice_mini_nat {", " chain postrouting {", "  type nat hook postrouting priority srcnat; policy accept;", f'  ip saddr {data["internal_network"]} oifname "{external}" masquerade', " }", "}"])
    return "\n".join(lines) + "\n"


_OWN_TABLES = (("inet", "simpleoffice_mini"), ("ip", "simpleoffice_mini_nat"))


def _linux_tables() -> set[tuple[str, str]]:
    result = _run(["nft", "-j", "list", "tables"], 2)
    if result.get("missing"):
        raise FileNotFoundError("nftables fehlt")
    if not result["ok"]:
        raise RuntimeError("nftables-Regeln konnten nicht gelesen werden")
    try:
        data = json.loads(result["stdout"])
        return {(row["table"]["family"], row["table"]["name"])
                for row in data["nftables"] if "table" in row}
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("nftables-Status ist ungültig") from exc


def _replace_linux_rules(script: str = "") -> None:
    tables = _linux_tables()
    # Deleting and installing in ONE nft transaction preserves the previous
    # working rules if validation or installation fails. Never flush other rules.
    deletion = "".join(f"delete table {family} {name}\n" for family, name in _OWN_TABLES if (family, name) in tables)
    if not deletion and not script:
        return
    executable = shutil.which("nft")
    if not executable:
        raise FileNotFoundError("nftables fehlt")
    result = subprocess.run([executable, "-f", "-"], input=deletion + script,
                            text=True, capture_output=True, timeout=5, check=False)
    if result.returncode:
        raise RuntimeError("nftables-Regeln konnten nicht geändert werden")


def _linux_rule_structure(value: dict[str, Any]) -> bool:
    """Inspect owned chains and rule counts, not arbitrary host firewall tables."""
    counts = value.get("rule_counts")
    if not isinstance(counts, dict):
        raise ValueError("Erwartete Gateway-Regelstruktur fehlt")
    content = value.get("rule_expressions")
    if not isinstance(content, dict):
        raise ValueError("Erwarteter Gateway-Regelinhalt fehlt")
    specifications = [("inet", "simpleoffice_mini", "forward", "filter", 0, "drop")]
    if value.get("mode") == "nat":
        specifications.append(("ip", "simpleoffice_mini_nat", "postrouting", "nat", 100, "accept"))
    for family, table, chain, kind, priority, policy in specifications:
        result = _run(["nft", "-j", "list", "table", family, table], 2)
        if not result.get("ok"):
            raise RuntimeError("Gateway-Regeln nicht lesbar")
        rows = json.loads(result["stdout"])["nftables"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Ungültige nftables-Struktur")
        tables = [row["table"] for row in rows if "table" in row]
        chains = [row["chain"] for row in rows if "chain" in row]
        rules = [row["rule"] for row in rows if "rule" in row]
        if any(not isinstance(item, dict) for item in tables + chains + rules):
            raise ValueError("Ungültige nftables-Objekte")
        if len(tables) != 1 or tables[0].get("family") != family or tables[0].get("name") != table:
            return False
        if tables[0].get("flags"):
            return False
        expected = {"family": family, "table": table, "name": chain,
                    "type": kind, "hook": chain, "prio": priority, "policy": policy}
        if len(chains) != 1 or any(chains[0].get(key) != val for key, val in expected.items()):
            return False
        count = counts.get(chain)
        if type(count) is not int or count < 0:
            raise ValueError("Erwartete Regelanzahl fehlt")
        if len(rules) != count:
            return False
        expected_expressions = content.get(chain)
        if not isinstance(expected_expressions, list) or len(expected_expressions) != count:
            raise ValueError("Erwarteter Gateway-Regelinhalt ist ungültig")
        for rule in rules:
            if any(rule.get(key) != val for key, val in {"family": family, "table": table, "chain": chain}.items()):
                return False
            if not isinstance(rule.get("expr"), list) or not rule["expr"]:
                return False
        if normalize_expressions([rule["expr"] for rule in rules]) != normalize_expressions(expected_expressions):
            return False
    return True


def gateway_health(value: dict[str, Any]) -> dict[str, Any]:
    """Read actual owned rules/forwarding. This does not probe Internet access."""
    kind = value.get("platform", platform_kind())
    try:
        if kind == "linux":
            tables = _linux_tables()
            rules = _OWN_TABLES[0] in tables and (value.get("mode") != "nat" or _OWN_TABLES[1] in tables)
            if rules:
                rules = _linux_rule_structure(value)
            forwarding = Path("/proc/sys/net/ipv4/ip_forward").read_text(encoding="ascii").strip() == "1"
            ok = rules and forwarding
        elif kind == "windows":
            internal = str(value.get("internal", "")).replace("'", "''")
            external = str(value.get("external", "")).replace("'", "''")
            name = str(value.get("nat_name", "SimpleOfficeMiniNat")).replace("'", "''")
            aliases = ",".join(f"'{alias}'" for alias in (internal, external) if alias)
            if not aliases:
                raise ValueError("Gateway-Interface fehlt")
            script = f"$i = @(Get-NetIPInterface -InterfaceAlias {aliases} -AddressFamily IPv4 -ErrorAction Stop); $f = ($i.Count -gt 0 -and @($i | Where-Object {{$_.Forwarding -ne 'Enabled'}}).Count -eq 0); "
            script += (f"$n = @(Get-NetNat -Name '{name}' -ErrorAction Stop).Count -gt 0; " if value.get("mode") == "nat" else "$n = $true; ")
            result = _powershell(script + "@{forwarding=$f;rules=$n} | ConvertTo-Json -Compress", 3)
            if not result["ok"]:
                raise RuntimeError("Windows-Routingstatus nicht verfügbar")
            state = json.loads(result["stdout"])
            ok = state["forwarding"] is True and state["rules"] is True
        else:
            raise RuntimeError("Routingstatus auf dieser Plattform nicht verfügbar")
        return {"ok": ok, "message": "Eigene Regeln und IPv4-Forwarding vorhanden; Internetzugang nicht geprüft." if ok else "Gateway-Regelstruktur oder IPv4-Forwarding fehlen oder weichen ab."}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return {"ok": None, "message": "Routingstatus nicht lesbar. Dienstrechte und Systemwerkzeuge prüfen."}


def disable_gateway(value: dict[str, Any]) -> dict[str, Any]:
    data = validate_gateway_settings(value); kind = platform_kind()
    if kind == "linux":
        _replace_linux_rules()
        return {"ok": True, "platform": kind, "mode": "off"}
    if kind == "windows":
        if data["mode"] != "nat":
            # Forwarding is shared host state, not an owned NAT resource.
            return {"ok": True, "platform": kind, "mode": "off"}
        name = data["nat_name"].replace("'", "''")
        result = _powershell(f"Get-NetNat -ErrorAction Stop | Where-Object {{$_.Name -eq '{name}'}} | Remove-NetNat -Confirm:$false -ErrorAction Stop", 5)
        if not result["ok"]:
            raise RuntimeError("Windows-NAT konnte nicht deaktiviert werden")
        return {"ok": True, "platform": kind, "mode": "off"}
    return {"ok": True, "platform": kind, "mode": "off"}


def apply_gateway(value: dict[str, Any], *, server_ip: str = "") -> dict[str, Any]:
    data = effective_gateway(value, server_ip=server_ip)
    if data["warnings"]: raise ValueError("; ".join(data["warnings"]))
    if not data["enabled"] or data["mode"] == "off": return disable_gateway(data)
    if data["platform"] == "linux":
        nft = shutil.which("nft"); sysctl = shutil.which("sysctl")
        if not nft or not sysctl: raise RuntimeError("nftables und iproute/sysctl werden für Routing/NAT benötigt")
        if data["forward_ipv4"]:
            result = _run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
            if not result["ok"]: raise RuntimeError(str(result["stderr"] or "IPv4 forwarding fehlgeschlagen"))
        expressions = expected_rules(data)
        _replace_linux_rules(_linux_script(data))
        return {"ok": True, "platform": "linux", "mode": data["mode"], "internal": data["effective_internal_interface"], "external": data["effective_external_interface"],
                "rule_expressions": expressions,
                "rule_counts": {chain: len(rules) for chain, rules in expressions.items()}}
    if data["platform"] == "windows":
        internal = data["effective_internal_interface"].replace("'", "''"); external = data["effective_external_interface"].replace("'", "''")
        script = [f"Set-NetIPInterface -InterfaceAlias '{internal}' -AddressFamily IPv4 -Forwarding Enabled -ErrorAction Stop"]
        if external: script.append(f"Set-NetIPInterface -InterfaceAlias '{external}' -AddressFamily IPv4 -Forwarding Enabled -ErrorAction Stop")
        if data["mode"] == "nat":
            name = data["nat_name"].replace("'", "''")
            script.extend(["if (-not (Get-Command New-NetNat -ErrorAction SilentlyContinue)) { throw 'New-NetNat unavailable on this Windows installation' }", f"Get-NetNat -Name '{name}' -ErrorAction SilentlyContinue | Remove-NetNat -Confirm:$false -ErrorAction SilentlyContinue", f"New-NetNat -Name '{name}' -InternalIPInterfaceAddressPrefix '{data['internal_network']}' -ErrorAction Stop | Out-Null"])
        result = _powershell("; ".join(script), 30)
        if not result["ok"]: raise RuntimeError(str(result["stderr"] or result["stdout"] or "Windows Routing/NAT fehlgeschlagen"))
        return {"ok": True, "platform": "windows", "mode": data["mode"], "nat_name": data["nat_name"], "internal": data["effective_internal_interface"], "external": data["effective_external_interface"]}
    raise RuntimeError("Routing/NAT wird auf dieser Plattform nicht unterstützt")
