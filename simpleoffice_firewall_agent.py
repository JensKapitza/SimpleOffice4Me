"""Root-side firewall agent for UFW/firewalld with fail-safe test rollback.

This module is intentionally independent from Flask. Requests arrive through a
root-owned systemd Unix socket. Every mutation is a 20-second test backed by a
separate rollback process/timer before the first rule is changed.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from simpleoffice_firewall import AGENT_SOCKET, normalize_rules, validate_test_id

STATE_ROOT = Path(os.environ.get("SIMPLEOFFICE_FIREWALL_AGENT_STATE", "/var/lib/simpleoffice4me/firewall-agent"))
PLAN_DIR = STATE_ROOT / "tests"
COMMAND_TIMEOUT = 6
_UFW_LINE = re.compile(r"^\[\s*(\d+)\]\s+(\S+)\s+(ALLOW|DENY|REJECT)(?:\s+(IN|OUT))?\s+(.+)$", re.I)
_RICH_PORT = re.compile(r'port\s+port="(\d+)(?:-(\d+))?"\s+protocol="(tcp|udp)"', re.I)


def _safe_error(_exc: BaseException) -> str:
    return "Firewall-Aktion fehlgeschlagen. Agent- und Systemprotokoll prüfen."


def _run(command: list[str], timeout: int = COMMAND_TIMEOUT) -> dict[str, Any]:
    if not command or not all(isinstance(item, str) and item and "\x00" not in item for item in command):
        raise ValueError("Ungültiger Agent-Befehl")
    executable = shutil.which(command[0])
    if not executable:
        return {"ok": False, "missing": True, "stdout": "", "stderr": "", "returncode": None}
    env = {"PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "NO_COLOR": "1"}
    try:
        result = subprocess.run([executable, *command[1:]], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=timeout, check=False, env=env)
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "missing": False, "stdout": "", "stderr": "", "returncode": None}
    return {"ok": result.returncode == 0, "missing": False, "stdout": result.stdout[:200000], "stderr": result.stderr[:4000], "returncode": result.returncode}


def _port_spec(start: int, end: int, protocol: str, *, ufw: bool = False) -> str:
    if start == end:
        return f"{start}/{protocol}"
    separator = ":" if ufw else "-"
    return f"{start}{separator}{end}/{protocol}"


def _parse_port_spec(value: str) -> tuple[int, int, str] | None:
    match = re.fullmatch(r"(\d+)(?:[:-](\d+))?/(tcp|udp)", value.strip(), re.I)
    if not match:
        return None
    start = int(match.group(1)); end = int(match.group(2) or start)
    if not (1 <= start <= end <= 65535):
        return None
    return start, end, match.group(3).lower()


def parse_firewalld_active_zones(text: str) -> list[str]:
    zones: list[str] = []
    for line in text.splitlines():
        if line and not line[0].isspace():
            zone = line.split()[0].strip()
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", zone) and zone not in zones:
                zones.append(zone)
    return zones


def _firewalld_service_ports(service: str) -> list[tuple[int, int, str]]:
    result = _run(["firewall-cmd", f"--info-service={service}"])
    if not result["ok"]:
        return []
    for line in str(result["stdout"]).splitlines():
        if line.strip().startswith("ports:"):
            values = line.split(":", 1)[1].split()
            return [parsed for value in values if (parsed := _parse_port_spec(value))]
    return []


def parse_firewalld_zone(zone: str, text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fields: dict[str, str] = {}
    rich: list[str] = []
    in_rich = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("rich rules:"):
            in_rich = True
            continue
        if in_rich and stripped.startswith("rule "):
            rich.append(stripped)
            continue
        in_rich = False
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            fields[key.strip()] = value.strip()
    rules: list[dict[str, Any]] = []
    for value in fields.get("ports", "").split():
        parsed = _parse_port_spec(value)
        if parsed:
            start, end, protocol = parsed
            rules.append({"effect": "allow", "protocol": protocol, "port_start": start, "port_end": end, "zone": zone, "origin": "port", "summary": f"firewalld {zone}: {value} freigegeben"})
    for service in fields.get("services", "").split():
        for start, end, protocol in _firewalld_service_ports(service):
            rules.append({"effect": "allow", "protocol": protocol, "port_start": start, "port_end": end, "zone": zone, "origin": f"service:{service}", "summary": f"firewalld {zone}: Service {service}"})
    for value in rich:
        match = _RICH_PORT.search(value)
        if not match:
            continue
        start = int(match.group(1)); end = int(match.group(2) or start); protocol = match.group(3).lower()
        effect = "deny" if re.search(r"\b(drop|reject)\b", value) else "allow" if re.search(r"\baccept\b", value) else "unknown"
        if effect != "unknown":
            rules.append({"effect": effect, "protocol": protocol, "port_start": start, "port_end": end, "zone": zone, "origin": "rich-rule", "summary": f"firewalld {zone}: Rich Rule ({effect})"})
    meta = {"zone": zone, "target": fields.get("target", ""), "interfaces": fields.get("interfaces", "").split(), "sources": fields.get("sources", "").split(), "services": fields.get("services", "").split(), "rich_rules": rich[:128]}
    return rules, meta


def _firewalld_snapshot() -> dict[str, Any]:
    active = _run(["firewall-cmd", "--state"])
    is_active = active["ok"] and str(active["stdout"]).strip().lower() == "running"
    result: dict[str, Any] = {"installed": not active.get("missing", False), "active": is_active, "active_zones": [], "zones": [], "rules": []}
    if not is_active:
        return result
    zones_result = _run(["firewall-cmd", "--get-active-zones"])
    zones = parse_firewalld_active_zones(str(zones_result["stdout"])) if zones_result["ok"] else []
    if not zones:
        default = _run(["firewall-cmd", "--get-default-zone"])
        if default["ok"] and str(default["stdout"]).strip():
            zones = [str(default["stdout"]).strip().split()[0]]
    result["active_zones"] = zones
    for zone in zones[:16]:
        listing = _run(["firewall-cmd", "--zone", zone, "--list-all"])
        if not listing["ok"]:
            continue
        rules, meta = parse_firewalld_zone(zone, str(listing["stdout"]))
        result["rules"].extend(rules)
        result["zones"].append(meta)
    return result


def parse_ufw_status(verbose: str, numbered: str) -> dict[str, Any]:
    active = bool(re.search(r"^Status:\s*active\s*$", verbose, re.I | re.M))
    default_match = re.search(r"^Default:\s*([^\s,]+)\s*\(incoming\)", verbose, re.I | re.M)
    rules: list[dict[str, Any]] = []
    for raw in numbered.splitlines():
        line = raw.strip()
        match = _UFW_LINE.match(line)
        if not match:
            continue
        order = int(match.group(1)); destination = match.group(2); action = match.group(3).upper(); direction = (match.group(4) or "IN").upper(); source = match.group(5)
        if direction == "OUT":
            continue
        parsed = _parse_port_spec(destination)
        if not parsed and destination.isdigit():
            parsed = (int(destination), int(destination), "any")
        if not parsed:
            continue
        start, end, protocol = parsed
        effect = "allow" if action == "ALLOW" else "deny"
        comment = source.split("#", 1)[1].strip() if "#" in source else ""
        rules.append({"effect": effect, "protocol": protocol, "port_start": start, "port_end": end, "order": order, "source": source.split("#", 1)[0].strip(), "comment": comment, "origin": "ufw", "summary": f"UFW #{order}: {action} {destination}"})
    return {"active": active, "default_incoming": default_match.group(1).lower() if default_match else "unknown", "rules": rules}


def _ufw_snapshot() -> dict[str, Any]:
    verbose = _run(["ufw", "status", "verbose"])
    installed = not verbose.get("missing", False)
    if not installed:
        return {"installed": False, "active": False, "default_incoming": "unknown", "rules": []}
    numbered = _run(["ufw", "status", "numbered"])
    parsed = parse_ufw_status(str(verbose["stdout"]), str(numbered["stdout"]) if numbered["ok"] else "")
    return {"installed": True, **parsed}


def _pending_tests() -> list[dict[str, Any]]:
    rows = []
    if not PLAN_DIR.is_dir():
        return rows
    for path in sorted(PLAN_DIR.glob("*.json"))[:128]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("status") == "pending":
            rows.append({"id": data.get("id"), "backend": data.get("backend"), "created_at": data.get("created_at"), "expires_at": data.get("expires_at"), "rules": data.get("rules", [])})
    return rows


def firewall_snapshot() -> dict[str, Any]:
    firewalld = _firewalld_snapshot()
    ufw = _ufw_snapshot()
    active = [name for name, value in (("firewalld", firewalld), ("ufw", ufw)) if value.get("active")]
    conflict = len(active) > 1
    backend = active[0] if len(active) == 1 else "conflict" if conflict else "none"
    selected = firewalld if backend == "firewalld" else ufw if backend == "ufw" else {}
    return {
        "backend": backend,
        "active": len(active) == 1,
        "writable": len(active) == 1,
        "conflict": conflict,
        "installed": {"firewalld": bool(firewalld.get("installed")), "ufw": bool(ufw.get("installed")), "nftables": bool(shutil.which("nft"))},
        "active_zones": selected.get("active_zones", []),
        "zones": selected.get("zones", []),
        "default_incoming": selected.get("default_incoming", "unknown"),
        "rules": selected.get("rules", []),
        "tests": _pending_tests(),
        "message": "UFW und firewalld sind gleichzeitig aktiv; Änderungen sind gesperrt." if conflict else "Kein unterstützter Firewall-Manager ist aktiv." if not active else f"{backend} ist aktiv.",
        "checked_at": time.time(),
    }


def _plan_path(ident: str) -> Path:
    return PLAN_DIR / f"{validate_test_id(ident)}.json"


def _write_plan(data: dict[str, Any]) -> None:
    PLAN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if STATE_ROOT.exists():
        os.chmod(STATE_ROOT, 0o700)
    path = _plan_path(str(data["id"]))
    temp = path.with_name(f".{path.name}.{os.urandom(6).hex()}.tmp")
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _read_plan(ident: str) -> dict[str, Any]:
    try:
        data = json.loads(_plan_path(ident).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Firewall-Test ist nicht vorhanden") from exc
    if not isinstance(data, dict) or data.get("id") != ident:
        raise ValueError("Firewall-Testplan ist ungültig")
    return data


def _locked_plan(ident: str):
    PLAN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = PLAN_DIR / f"{validate_test_id(ident)}.lock"
    handle = lock_path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _firewalld_rich(rule: dict[str, Any]) -> str:
    port = str(rule["port_start"]) if rule["port_start"] == rule["port_end"] else f"{rule['port_start']}-{rule['port_end']}"
    source = f' source address="{rule["source"]}"' if rule.get("source") else ""
    return f'rule priority="-100"{source} port port="{port}" protocol="{rule["protocol"]}" reject'


def _firewalld_zone(rule: dict[str, Any], snapshot: dict[str, Any]) -> str:
    if rule.get("zone"):
        return str(rule["zone"])
    zones = snapshot.get("active_zones", [])
    if len(zones) != 1:
        raise ValueError("Bei mehreren firewalld-Zonen muss die Zielzone ausdrücklich gewählt werden")
    return str(zones[0])


def _firewalld_apply_test(rule: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    zone = _firewalld_zone(rule, snapshot)
    changed = dict(rule); changed["zone"] = zone
    if rule["effect"] == "allow":
        spec = _port_spec(rule["port_start"], rule["port_end"], rule["protocol"])
        query = _run(["firewall-cmd", "--zone", zone, "--query-port", spec])
        if query["ok"]:
            changed["preexisting"] = True
            return changed
        result = _run(["firewall-cmd", "--zone", zone, "--add-port", spec])
    else:
        rich = _firewalld_rich(changed)
        query = _run(["firewall-cmd", "--zone", zone, "--query-rich-rule", rich])
        if query["ok"]:
            changed["preexisting"] = True
            changed["rich_rule"] = rich
            return changed
        result = _run(["firewall-cmd", "--zone", zone, "--add-rich-rule", rich])
        changed["rich_rule"] = rich
    if not result["ok"]:
        raise RuntimeError("firewalld-Testregel konnte nicht angewendet werden")
    changed["preexisting"] = False
    return changed


def _firewalld_remove_runtime(rule: dict[str, Any]) -> None:
    if rule.get("preexisting"):
        return
    zone = str(rule["zone"])
    if rule["effect"] == "allow":
        spec = _port_spec(rule["port_start"], rule["port_end"], rule["protocol"])
        result = _run(["firewall-cmd", "--zone", zone, "--remove-port", spec])
    else:
        result = _run(["firewall-cmd", "--zone", zone, "--remove-rich-rule", str(rule["rich_rule"])])
    if not result["ok"]:
        raise RuntimeError("firewalld-Testregel konnte nicht zurückgerollt werden")


def _firewalld_permanent_change(rule: dict[str, Any]) -> dict[str, Any]:
    if rule.get("preexisting"):
        return {"backend": "firewalld", "owned": False, "rule": rule}
    zone = str(rule["zone"])
    if rule["effect"] == "allow":
        spec = _port_spec(rule["port_start"], rule["port_end"], rule["protocol"])
        query = _run(["firewall-cmd", "--permanent", "--zone", zone, "--query-port", spec])
    else:
        query = _run(["firewall-cmd", "--permanent", "--zone", zone, "--query-rich-rule", str(rule["rich_rule"])])
    return {"backend": "firewalld", "owned": not query["ok"], "rule": rule}


def _firewalld_add_permanent(change: dict[str, Any]) -> None:
    if not change.get("owned"):
        return
    rule = change["rule"]
    zone = str(rule["zone"])
    if rule["effect"] == "allow":
        spec = _port_spec(rule["port_start"], rule["port_end"], rule["protocol"])
        result = _run(["firewall-cmd", "--permanent", "--zone", zone, "--add-port", spec])
    else:
        result = _run(["firewall-cmd", "--permanent", "--zone", zone, "--add-rich-rule", str(rule["rich_rule"])])
    if not result["ok"]:
        raise RuntimeError("firewalld-Regel konnte nicht dauerhaft gespeichert werden")


def _firewalld_remove_permanent(change: dict[str, Any]) -> None:
    if not change.get("owned"):
        return
    rule = change["rule"]
    zone = str(rule["zone"])
    if rule["effect"] == "allow":
        spec = _port_spec(rule["port_start"], rule["port_end"], rule["protocol"])
        query = _run(["firewall-cmd", "--permanent", "--zone", zone, "--query-port", spec])
        if not query["ok"]:
            return
        result = _run(["firewall-cmd", "--permanent", "--zone", zone, "--remove-port", spec])
    else:
        rich = str(rule["rich_rule"])
        query = _run(["firewall-cmd", "--permanent", "--zone", zone, "--query-rich-rule", rich])
        if not query["ok"]:
            return
        result = _run(["firewall-cmd", "--permanent", "--zone", zone, "--remove-rich-rule", rich])
    if not result["ok"]:
        raise RuntimeError("Teilweise bestätigte firewalld-Regel konnte nicht bereinigt werden")


def _ufw_command(rule: dict[str, Any], marker: str) -> list[str]:
    action = "allow" if rule["effect"] == "allow" else "deny"
    # Insert at the front so the test has deterministic precedence over an
    # existing opposite rule. Confirmation keeps the same precedence.
    prefix = ["ufw", "--force", "insert", "1", action]
    if rule.get("source"):
        port = str(rule["port_start"]) if rule["port_start"] == rule["port_end"] else f"{rule['port_start']}:{rule['port_end']}"
        return [*prefix, "proto", rule["protocol"], "from", rule["source"], "to", "any", "port", port, "comment", marker]
    return [*prefix, _port_spec(rule["port_start"], rule["port_end"], rule["protocol"], ufw=True), "comment", marker]


def _ufw_apply_test(rule: dict[str, Any], marker: str) -> dict[str, Any]:
    result = _run(_ufw_command(rule, marker))
    if not result["ok"]:
        raise RuntimeError("UFW-Testregel konnte nicht angewendet werden")
    return {**rule, "marker": marker, "preexisting": False}


def _ufw_marker_numbers(marker: str) -> list[int]:
    result = _run(["ufw", "status", "numbered"])
    if not result["ok"]:
        raise RuntimeError("UFW-Regeln konnten nicht gelesen werden")
    numbers = []
    for line in str(result["stdout"]).splitlines():
        match = re.match(r"^\[\s*(\d+)\].*#\s*(.+?)\s*$", line.strip())
        if match and match.group(2).strip() == marker:
            numbers.append(int(match.group(1)))
    return sorted(numbers, reverse=True)


def _ufw_delete_marker(marker: str) -> None:
    for number in _ufw_marker_numbers(marker):
        # The number is used only after verifying the unique marker on the same
        # current status line; foreign rules are never deleted by a bare index.
        result = _run(["ufw", "--force", "delete", str(number)])
        if not result["ok"]:
            raise RuntimeError("Markierte UFW-Testregel konnte nicht entfernt werden")


def _ufw_managed_change(rule: dict[str, Any], ident: str, index: int) -> dict[str, Any]:
    return {
        "backend": "ufw",
        "owned": True,
        "rule": rule,
        "marker": f"simpleoffice-managed-{ident[:12]}-{index}",
    }


def _ufw_add_managed(change: dict[str, Any]) -> None:
    result = _run(_ufw_command(change["rule"], str(change["marker"])))
    if not result["ok"]:
        raise RuntimeError("UFW-Regel konnte nicht dauerhaft bestätigt werden")


def _remove_confirmation_change(change: dict[str, Any]) -> None:
    if not change.get("owned"):
        return
    if change.get("backend") == "firewalld":
        _firewalld_remove_permanent(change)
    elif change.get("backend") == "ufw":
        _ufw_delete_marker(str(change["marker"]))


def _schedule_rollback(ident: str, delay: int = 20) -> str:
    unit = f"simpleoffice-firewall-rollback-{ident}"
    if shutil.which("systemd-run") and Path("/run/systemd/system").is_dir():
        result = _run(["systemd-run", f"--unit={unit}", f"--on-active={delay}s", "--property=Type=oneshot", sys.executable, "-m", "simpleoffice_firewall_agent", "--rollback-plan", ident], timeout=5)
        if result["ok"]:
            return "systemd"
    try:
        subprocess.Popen([sys.executable, "-m", "simpleoffice_firewall_agent", "--rollback-plan", ident, "--delay", str(delay)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True, env={"PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"), "SIMPLEOFFICE_FIREWALL_AGENT_STATE": str(STATE_ROOT)})
    except OSError as exc:
        raise RuntimeError("Rollback-Watchdog konnte nicht vorbereitet werden") from exc
    return "process"


def _cancel_systemd_watchdog(ident: str) -> None:
    if shutil.which("systemctl"):
        _run(["systemctl", "stop", f"simpleoffice-firewall-rollback-{ident}.timer"], timeout=3)


def test_rules(rules: list[dict[str, Any]]) -> dict[str, Any]:
    rules = normalize_rules(rules)
    snapshot = firewall_snapshot()
    backend = snapshot["backend"]
    if snapshot.get("conflict") or backend not in {"ufw", "firewalld"}:
        raise ValueError("Genau ein aktiver UFW- oder firewalld-Manager ist für Änderungen erforderlich")
    ident = os.urandom(16).hex()
    now = time.time()
    plan: dict[str, Any] = {"id": ident, "backend": backend, "status": "preparing", "created_at": now, "expires_at": now + 20, "rules": rules, "applied": [], "confirmation_changes": [], "watchdog": ""}
    _write_plan(plan)
    plan["watchdog"] = _schedule_rollback(ident, 20)
    _write_plan(plan)
    marker = f"simpleoffice-test-{ident}"
    try:
        for rule in rules:
            applied = _firewalld_apply_test(rule, snapshot) if backend == "firewalld" else _ufw_apply_test(rule, marker)
            plan["applied"].append(applied)
            _write_plan(plan)
        plan["status"] = "pending"
        _write_plan(plan)
    except Exception:
        try:
            _rollback_plan_locked(plan)
        finally:
            raise
    return {"test_id": ident, "backend": backend, "expires_at": plan["expires_at"], "rules": rules, "watchdog": plan["watchdog"]}


def _rollback_plan_locked(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("status") in {"rolled_back", "confirmed"}:
        return {"test_id": plan["id"], "state": plan["status"]}
    errors = []
    for change in reversed(plan.get("confirmation_changes", [])):
        try:
            _remove_confirmation_change(change)
        except Exception as exc:
            errors.append(type(exc).__name__)
    for rule in reversed(plan.get("applied", [])):
        try:
            if plan["backend"] == "firewalld":
                _firewalld_remove_runtime(rule)
            elif plan["backend"] == "ufw":
                _ufw_delete_marker(str(rule.get("marker") or f"simpleoffice-test-{plan['id']}"))
        except Exception as exc:
            errors.append(type(exc).__name__)
    if errors:
        plan["status"] = "rollback_failed"
        plan["rollback_error"] = "Firewall-Test konnte nicht vollständig zurückgerollt werden"
        _write_plan(plan)
        raise RuntimeError("Firewall-Rollback ist fehlgeschlagen")
    plan["status"] = "rolled_back"
    plan["rolled_back_at"] = time.time()
    _write_plan(plan)
    return {"test_id": plan["id"], "state": "rolled_back"}


def rollback_test(ident: str) -> dict[str, Any]:
    ident = validate_test_id(ident)
    lock = _locked_plan(ident)
    try:
        plan = _read_plan(ident)
        return _rollback_plan_locked(plan)
    finally:
        lock.close()


def confirm_test(ident: str) -> dict[str, Any]:
    ident = validate_test_id(ident)
    lock = _locked_plan(ident)
    try:
        plan = _read_plan(ident)
        if plan.get("status") != "pending":
            raise ValueError("Firewall-Test ist nicht mehr bestätigbar")
        if time.time() >= float(plan.get("expires_at", 0)):
            _rollback_plan_locked(plan)
            raise ValueError("Firewall-Test ist abgelaufen und wurde zurückgerollt")

        plan["status"] = "confirming"
        plan["confirmation_changes"] = []
        _write_plan(plan)
        try:
            for index, rule in enumerate(plan.get("applied", [])):
                if plan["backend"] == "firewalld":
                    change = _firewalld_permanent_change(rule)
                    plan["confirmation_changes"].append(change)
                    _write_plan(plan)
                    _firewalld_add_permanent(change)
                elif plan["backend"] == "ufw":
                    change = _ufw_managed_change(rule, ident, index)
                    plan["confirmation_changes"].append(change)
                    _write_plan(plan)
                    _ufw_add_managed(change)
        except Exception:
            cleanup_errors = []
            for change in reversed(plan.get("confirmation_changes", [])):
                try:
                    _remove_confirmation_change(change)
                except Exception as exc:
                    cleanup_errors.append(type(exc).__name__)
            if cleanup_errors:
                plan["status"] = "rollback_failed"
                plan["rollback_error"] = "Teilweise bestätigte Regeln konnten nicht vollständig bereinigt werden"
            else:
                plan["status"] = "pending"
                plan["confirmation_changes"] = []
            _write_plan(plan)
            raise

        # All permanent/managed rules exist before the watchdog is cancelled.
        # A crash before this write still leaves status=confirming, so recovery
        # removes the partially confirmed rules and the test rules.
        plan["status"] = "confirmed"
        plan["confirmed_at"] = time.time()
        plan["cleanup_pending"] = plan["backend"] == "ufw"
        _write_plan(plan)
        _cancel_systemd_watchdog(ident)

        if plan["backend"] == "ufw":
            try:
                _ufw_delete_marker(f"simpleoffice-test-{ident}")
                plan["cleanup_pending"] = False
                _write_plan(plan)
            except Exception:
                # The managed rules already carry the confirmed policy. A
                # remaining same-effect test marker is harmless and is retried
                # on the next agent start.
                pass
        return {"test_id": ident, "state": "confirmed", "backend": plan["backend"], "cleanup_pending": bool(plan.get("cleanup_pending"))}
    finally:
        lock.close()


def recover_pending() -> None:
    PLAN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in PLAN_DIR.glob("*.json"):
        ident = path.stem
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            continue
        try:
            plan = _read_plan(ident)
            if plan.get("status") == "confirmed" and plan.get("cleanup_pending") and plan.get("backend") == "ufw":
                _ufw_delete_marker(f"simpleoffice-test-{ident}")
                plan["cleanup_pending"] = False
                _write_plan(plan)
                continue
            if plan.get("status") not in {"pending", "preparing", "confirming", "rollback_failed"}:
                continue
            remaining = int(float(plan.get("expires_at", 0)) - time.time())
            if remaining <= 0:
                rollback_test(ident)
            else:
                plan["watchdog"] = _schedule_rollback(ident, remaining)
                _write_plan(plan)
        except Exception:
            continue


def rollback_all_pending() -> dict[str, int]:
    """Immediately roll back every unfinished test, e.g. before package removal."""
    PLAN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    rolled_back = 0
    failed = 0
    for path in sorted(PLAN_DIR.glob("*.json")):
        ident = path.stem
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            continue
        try:
            plan = _read_plan(ident)
            if plan.get("status") not in {"pending", "preparing", "confirming", "rollback_failed"}:
                continue
            rollback_test(ident)
            rolled_back += 1
        except Exception:
            failed += 1
    return {"rolled_back": rolled_back, "failed": failed}


def handle_request(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"action", "rules", "test_id"}:
        raise ValueError("Ungültige Firewall-Agent-Anfrage")
    action = str(payload.get("action") or "")
    if action == "snapshot" and set(payload) == {"action"}:
        return {"ok": True, **firewall_snapshot()}
    if action == "test" and set(payload) == {"action", "rules"}:
        return {"ok": True, **test_rules(payload.get("rules"))}
    if action == "confirm" and set(payload) == {"action", "test_id"}:
        return {"ok": True, **confirm_test(validate_test_id(payload.get("test_id")))}
    if action == "rollback" and set(payload) == {"action", "test_id"}:
        return {"ok": True, **rollback_test(validate_test_id(payload.get("test_id")))}
    raise ValueError("Unbekannte oder unvollständige Firewall-Agent-Aktion")


def _serve_connection(connection: socket.socket) -> None:
    connection.settimeout(8)
    data = bytearray()
    try:
        while b"\n" not in data and len(data) < 32768:
            part = connection.recv(4096)
            if not part:
                break
            data.extend(part)
        if not data or len(data) >= 32768:
            raise ValueError("Firewall-Agent-Anfrage ist leer oder zu groß")
        payload = json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))
        response = handle_request(payload)
    except Exception as exc:
        response = {"ok": False, "error": _safe_error(exc), "error_type": type(exc).__name__}
    try:
        connection.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))
    finally:
        connection.close()


def serve(socket_path: str | Path = AGENT_SOCKET) -> None:
    recover_pending()
    if int(os.environ.get("LISTEN_FDS", "0") or 0) > 0:
        listener = socket.fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)
    else:
        path = Path(socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous_umask = os.umask(0o117)
        try:
            listener.bind(str(path))
        finally:
            os.umask(previous_umask)
        listener.listen(16)
    listener.settimeout(2)
    while True:
        try:
            connection, _ = listener.accept()
        except socket.timeout:
            continue
        threading.Thread(target=_serve_connection, args=(connection,), daemon=True).start()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me privilegierter Firewall-Agent")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--rollback-plan")
    parser.add_argument("--rollback-pending", action="store_true")
    parser.add_argument("--delay", type=int, default=0)
    args = parser.parse_args(argv)
    if args.rollback_plan:
        ident = validate_test_id(args.rollback_plan)
        if args.delay:
            time.sleep(max(0, min(args.delay, 60)))
        try:
            rollback_test(ident)
        except ValueError:
            pass
        return
    if args.rollback_pending:
        result = rollback_all_pending()
        if result["failed"]:
            raise SystemExit(1)
        return
    if not args.serve:
        parser.error("--serve, --rollback-plan oder --rollback-pending erforderlich")
    serve()


if __name__ == "__main__":
    main()
