"""Safe Linux firewall control transport and Mini Services port diagnostics.

The web application never executes privileged firewall commands. It writes
validated requests to a private SQLite mailbox. The existing Mini Services
worker forwards those requests to the root-owned firewall agent over a Unix
socket which is accessible only through the worker's supplementary group.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from simpleoffice_connection_relay import load_relay_settings
from simpleoffice_media_renderer import load_media_renderer_settings
from simpleoffice_mini_core import default_config_path, load_config, read_status, state_dir
from simpleoffice_network_boot import load_boot_settings
from simpleoffice_sip_runtime import effective_sip_settings

AGENT_SOCKET = Path(os.environ.get("SIMPLEOFFICE_FIREWALL_SOCKET", "/run/simpleoffice4me/firewall.sock"))
ACTIONS = frozenset({"snapshot", "test", "confirm", "rollback"})
EFFECTS = frozenset({"allow", "deny"})
PROTOCOLS = frozenset({"tcp", "udp"})
RULE_LIMIT = 16
_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_ZONE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if type(value) is bool:
        raise ValueError(f"{label} ist ungültig")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} muss eine ganze Zahl sein") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{label} muss zwischen {minimum} und {maximum} liegen")
    return number


def normalize_rule(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Firewallregel muss ein Objekt sein")
    allowed = {"effect", "protocol", "port", "port_start", "port_end", "zone", "source", "label"}
    if set(value) - allowed:
        raise ValueError("Firewallregel enthält unbekannte Felder")
    effect = str(value.get("effect", "")).strip().lower()
    protocol = str(value.get("protocol", "")).strip().lower()
    if effect not in EFFECTS or protocol not in PROTOCOLS:
        raise ValueError("Firewallregel benötigt allow/deny und tcp/udp")
    start = _integer(value.get("port_start", value.get("port")), "Startport", 1, 65535)
    end = _integer(value.get("port_end", start), "Endport", start, 65535)
    zone = str(value.get("zone") or "").strip()
    if zone and not _ZONE_RE.fullmatch(zone):
        raise ValueError("Firewall-Zone ist ungültig")
    source = str(value.get("source") or "").strip()
    if source:
        try:
            source = str(ipaddress.ip_network(source, strict=False))
        except ValueError as exc:
            raise ValueError("Firewall-Quelle muss eine IP oder ein CIDR-Netz sein") from exc
    label = " ".join(str(value.get("label") or "").split())[:120]
    return {
        "effect": effect,
        "protocol": protocol,
        "port_start": start,
        "port_end": end,
        "zone": zone,
        "source": source,
        "label": label,
    }


def normalize_rules(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not 1 <= len(values) <= RULE_LIMIT:
        raise ValueError(f"Es sind 1 bis {RULE_LIMIT} Firewallregeln erlaubt")
    rules = [normalize_rule(value) for value in values]
    unique: list[dict[str, Any]] = []
    seen = set()
    for rule in rules:
        key = tuple(rule[k] for k in ("effect", "protocol", "port_start", "port_end", "zone", "source"))
        if key not in seen:
            seen.add(key)
            unique.append(rule)
    return unique


def validate_test_id(value: Any) -> str:
    ident = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(ident):
        raise ValueError("Ungültige Firewall-Test-ID")
    return ident


class FirewallControlStore:
    """Private worker mailbox for firewall operations and the last agent snapshot."""

    def __init__(self, config_path: str | Path | None = None):
        self.path = state_dir(config_path or default_config_path()) / "firewall-control.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("Firewall-Steuerdatei darf kein symbolischer Link sein")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, result TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS cache (name TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValueError("Unbekannte Firewall-Aktion")
        payload = dict(payload or {})
        if action == "test":
            payload = {"rules": normalize_rules(payload.get("rules"))}
        elif action in {"confirm", "rollback"}:
            payload = {"test_id": validate_test_id(payload.get("test_id"))}
        elif payload:
            raise ValueError("Snapshot akzeptiert keine Parameter")
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE operations SET state='failed', result=?, updated=? WHERE state='queued' AND created<?", (json.dumps({"error": "Firewall-Auftrag ist abgelaufen."}), now, now - 60))
            if db.execute("SELECT COUNT(*) FROM operations WHERE state IN ('queued','running')").fetchone()[0] >= 16:
                raise ValueError("Zu viele offene Firewall-Aktionen")
            ident = uuid.uuid4().hex
            db.execute("INSERT INTO operations VALUES (?,?,?,'queued',?,?,NULL)", (ident, action, json.dumps(payload), now, now))
        return {"id": ident, "action": action, "state": "queued"}

    def claim(self) -> dict[str, Any] | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.execute("UPDATE operations SET state='failed', result=?, updated=? WHERE state='queued' AND created<?", (json.dumps({"error": "Firewall-Auftrag ist abgelaufen."}), now, now - 60))
            row = db.execute("SELECT * FROM operations WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE operations SET state='running', updated=? WHERE id=?", (now, row["id"]))
        if not row:
            return None
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        return value

    def finish(self, ident: str, ok: bool, result: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("UPDATE operations SET state=?, updated=?, result=? WHERE id=?", ("completed" if ok else "failed", time.time(), json.dumps(result), ident))

    def operation(self, ident: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (str(ident)[:64],)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    def recover_interrupted(self) -> None:
        with self.connect() as db:
            db.execute("UPDATE operations SET state='failed', updated=?, result=? WHERE state IN ('running','queued')", (time.time(), json.dumps({"error": "Worker wurde neu gestartet. Ein bereits laufender Firewall-Test wird weiterhin vom Agenten automatisch zurückgerollt."})))

    def set_snapshot(self, value: dict[str, Any]) -> None:
        now = time.time()
        data = dict(value)
        data["cached_at"] = now
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES ('snapshot',?,?)", (json.dumps(data), now))

    def snapshot(self) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT data,updated FROM cache WHERE name='snapshot'").fetchone()
        if not row:
            return {"backend": "unavailable", "active": False, "writable": False, "message": "Firewall-Agent wurde noch nicht abgefragt.", "rules": [], "tests": []}
        try:
            data = json.loads(row["data"])
        except json.JSONDecodeError:
            return {"backend": "unavailable", "active": False, "writable": False, "message": "Firewall-Status ist beschädigt.", "rules": [], "tests": []}
        data["cache_age_seconds"] = max(0, round(time.time() - float(row["updated"]), 1))
        return data


def agent_request(payload: dict[str, Any], *, socket_path: str | Path = AGENT_SOCKET, timeout: float = 6.0) -> dict[str, Any]:
    """Send one bounded JSON request to the privileged local agent."""
    raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > 32768:
        raise ValueError("Firewall-Auftrag ist zu groß")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(socket_path))
        client.sendall(raw)
        chunks = bytearray()
        while b"\n" not in chunks:
            part = client.recv(65536 - len(chunks))
            if not part:
                break
            chunks.extend(part)
            if len(chunks) >= 65536:
                raise RuntimeError("Firewall-Agent-Antwort ist zu groß")
    finally:
        client.close()
    if not chunks:
        raise RuntimeError("Firewall-Agent hat nicht geantwortet")
    try:
        result = json.loads(bytes(chunks).split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Firewall-Agent lieferte eine ungültige Antwort") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Firewall-Agent-Antwort ist ungültig")
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Firewall-Aktion fehlgeschlagen")[:300])
    return result


def _audio_receiver_settings(config_path: Path) -> dict[str, Any]:
    database = config_path.parent / "audio" / "audio-output.sqlite3"
    default = {"enabled": True, "autostart": False, "port": 5004, "bind": ""}
    if not database.is_file() or database.is_symlink():
        return default
    try:
        db = sqlite3.connect(database, timeout=1)
        row = db.execute("SELECT data_json FROM audio_service_setting WHERE service='receiver'").fetchone()
        db.close()
        data = json.loads(row[0]) if row else {}
    except (sqlite3.Error, OSError, json.JSONDecodeError, TypeError):
        return default
    if not isinstance(data, dict):
        return default
    result = {**default, **{key: data[key] for key in default if key in data}}
    try:
        result["port"] = _integer(result["port"], "RTP-Port", 1024, 65534)
    except ValueError:
        result["port"] = 5004
    return result


def _web_settings(config_path: Path) -> dict[str, Any]:
    path = config_path.parent / "simpleoffice.json"
    value = {"host": os.environ.get("SIMPLEOFFICE_HOST", "127.0.0.1"), "port": os.environ.get("SIMPLEOFFICE_PORT", "8080")}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            value = {**stored, **{key: os.environ[name] for key, name in (("host", "SIMPLEOFFICE_HOST"), ("port", "SIMPLEOFFICE_PORT")) if os.environ.get(name)}}
    except (OSError, json.JSONDecodeError):
        pass
    try:
        value["port"] = _integer(value.get("port", 8080), "Web-Port", 1, 65535)
    except ValueError:
        value["port"] = 8080
    return value


def service_port_inventory(config_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Build the expected inbound port matrix from the currently stored settings."""
    path = Path(config_path or default_config_path())
    status = read_status(path)
    states = status.get("services", {}) if isinstance(status.get("services"), dict) else {}
    rows: list[dict[str, Any]] = []

    def add(ident: str, name: str, enabled: bool, bind: str, ports: list[dict[str, Any]], *, critical: bool = False, note: str = ""):
        state = states.get(ident, {}).get("state", "unknown") if isinstance(states.get(ident), dict) else "unknown"
        rows.append({"id": ident, "name": name, "enabled": bool(enabled), "state": state, "bind": str(bind or ""), "ports": ports, "critical": critical, "note": note})

    try:
        config = load_config(path)
        add("dhcp", "DHCP", config["dhcp"]["enabled"], config["dhcp"].get("bind", ""), [{"protocol": "udp", "port_start": int(config["dhcp"]["port"]), "port_end": int(config["dhcp"]["port"])}])
        dns_port = int(config["dns"]["port"])
        add("dns", "DNS", config["dns"]["enabled"], ", ".join(config["dns"].get("bind", [])), [{"protocol": "udp", "port_start": dns_port, "port_end": dns_port}, {"protocol": "tcp", "port_start": dns_port, "port_end": dns_port}])
    except (OSError, ValueError, TypeError, KeyError):
        pass
    try:
        boot = load_boot_settings(path)
        port = int(boot["tftp_port"])
        add("tftp", "TFTP / PXE", boot["enabled"] and boot["tftp_enabled"], boot.get("tftp_bind", ""), [{"protocol": "udp", "port_start": port, "port_end": port}], note="TFTP verwendet zusätzlich dynamische Transfer-Sockets; der Listener-Port ist hier separat dargestellt.")
    except (OSError, ValueError, TypeError, KeyError):
        pass
    try:
        sip = effective_sip_settings(path)
        port = int(sip["registrar_port"])
        protocol = "tcp" if str(sip.get("transport", "udp")).lower() == "tcp" else "udp"
        add("sip", "SIP", True, sip.get("bind_host", ""), [{"protocol": protocol, "port_start": port, "port_end": port}])
    except (OSError, ValueError, TypeError, KeyError):
        pass
    receiver = _audio_receiver_settings(path)
    rtp = int(receiver["port"])
    add("audio-receiver", "RTP / RTCP Receiver", receiver.get("enabled", True), receiver.get("bind", ""), [{"protocol": "udp", "port_start": rtp, "port_end": rtp}, {"protocol": "udp", "port_start": rtp + 1, "port_end": rtp + 1}], note="RTP und RTCP werden getrennt geprüft.")
    try:
        relay = load_relay_settings(path)
        ports = [{"protocol": "udp", "port_start": int(relay["turn_port"]), "port_end": int(relay["turn_port"])}, {"protocol": "tcp", "port_start": int(relay["turn_port"]), "port_end": int(relay["turn_port"])}]
        if relay.get("tls_enabled"):
            ports.append({"protocol": "tcp", "port_start": int(relay["turn_tls_port"]), "port_end": int(relay["turn_tls_port"])})
        ports.append({"protocol": "udp", "port_start": int(relay["min_port"]), "port_end": int(relay["max_port"])})
        add("relay", "STUN / TURN Relay", relay.get("enabled", False), relay.get("listen_ip", ""), ports)
    except (OSError, ValueError, TypeError, KeyError):
        pass
    try:
        media = load_media_renderer_settings(path)
        add("media-renderer", "DLNA / Media Renderer", media.get("enabled", False), media.get("bind", ""), [{"protocol": "tcp", "port_start": int(media["port"]), "port_end": int(media["port"])}], note="SSDP/UPnP-Discovery nutzt zusätzlich Multicast und wird nicht als einfacher Listener-Port bewertet.")
    except (OSError, ValueError, TypeError, KeyError):
        pass
    web = _web_settings(path)
    add("http-boot", "SimpleOffice Web / HTTP-PXE", True, web.get("host", ""), [{"protocol": "tcp", "port_start": int(web["port"]), "port_end": int(web["port"])}], critical=True, note="Dieser Port kann gleichzeitig die aktuelle Administrationsoberfläche bereitstellen.")
    return rows


def _listener_snapshot() -> list[dict[str, Any]]:
    executable = shutil.which("ss")
    if not executable:
        return []
    import subprocess
    try:
        result = subprocess.run([executable, "-H", "-lntu"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3, check=False, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines()[:4096]:
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].lower()
        local = parts[4]
        if proto not in PROTOCOLS:
            continue
        if local.startswith("[") and "]:" in local:
            host, port_text = local.rsplit(":", 1)
            host = host.strip("[]")
        elif ":" in local:
            host, port_text = local.rsplit(":", 1)
        else:
            continue
        try:
            port = int(port_text)
        except ValueError:
            continue
        rows.append({"protocol": proto, "host": host, "port": port})
    return rows


def _is_loopback_bind(value: str) -> bool:
    binds = [item.strip() for item in str(value or "").split(",") if item.strip()]
    if not binds:
        return False
    try:
        return all(ipaddress.ip_address(item.split("%", 1)[0]).is_loopback for item in binds)
    except ValueError:
        return False


def _bind_families(value: str) -> set[str]:
    binds = [item.strip() for item in str(value or "").split(",") if item.strip()]
    if not binds:
        return {"ipv4", "ipv6"}
    families: set[str] = set()
    for item in binds:
        try:
            address = ipaddress.ip_address(item.split("%", 1)[0])
        except ValueError:
            return {"ipv4", "ipv6"}
        families.add("ipv4" if address.version == 4 else "ipv6")
    return families or {"ipv4", "ipv6"}


def _port_matches(rule: dict[str, Any], protocol: str, start: int, end: int) -> bool:
    if rule.get("protocol") not in {protocol, "any", None, ""}:
        return False
    try:
        rstart = int(rule.get("port_start"))
        rend = int(rule.get("port_end", rstart))
    except (TypeError, ValueError):
        return False
    return rstart <= start and rend >= end


def firewall_decision(snapshot: dict[str, Any], protocol: str, start: int, end: int, bind: str = "") -> dict[str, str]:
    if _is_loopback_bind(bind):
        return {"state": "local-only", "reason": "Dienst ist nur an Loopback gebunden; keine LAN-Freigabe erforderlich."}
    if snapshot.get("conflict"):
        return {"state": "unknown", "reason": "Mehrere Firewall-Manager sind gleichzeitig aktiv."}
    if not snapshot.get("active"):
        return {"state": "inactive", "reason": "Kein unterstützter aktiver Firewall-Manager."}
    rules = snapshot.get("rules", []) if isinstance(snapshot.get("rules"), list) else []
    matching = [rule for rule in rules if isinstance(rule, dict) and _port_matches(rule, protocol, start, end)]
    if snapshot.get("backend") == "ufw":
        default = str(snapshot.get("default_incoming") or "unknown").lower()
        decisions: dict[str, tuple[str, str]] = {}
        for family in _bind_families(bind):
            family_rules = [row for row in matching if row.get("family") in {None, "", family}]
            family_rules.sort(key=lambda row: int(row.get("order", 999999)))
            if family_rules:
                effect = family_rules[0].get("effect")
                decisions[family] = (
                    "allowed" if effect == "allow" else "blocked",
                    str(family_rules[0].get("summary") or "Passende UFW-Regel"),
                )
            elif default in {"deny", "reject"}:
                decisions[family] = ("blocked", f"UFW-Standard für eingehend: {default}.")
            elif default == "allow":
                decisions[family] = ("allowed", "UFW-Standard erlaubt eingehenden Verkehr.")
            else:
                decisions[family] = ("unknown", "UFW-Standardregel konnte nicht bestimmt werden.")
        states = {value[0] for value in decisions.values()}
        if len(states) > 1:
            details = ", ".join(f"{family}: {state}" for family, (state, _reason) in sorted(decisions.items()))
            return {"state": "unknown", "reason": f"UFW-Regeln unterscheiden sich nach IP-Familie ({details})."}
        state, reason = next(iter(decisions.values()))
        return {"state": state, "reason": reason}
    if snapshot.get("backend") == "firewalld":
        zones = snapshot.get("active_zones", []) if isinstance(snapshot.get("active_zones"), list) else []
        if len(zones) > 1:
            return {"state": "unknown", "reason": "Mehrere firewalld-Zonen sind aktiv; die Dienstzone ist ohne eindeutige Interface-Zuordnung nicht sicher bestimmbar."}
        relevant = matching
        zone = zones[0] if len(zones) == 1 else ""
        if zone:
            relevant = [rule for rule in matching if not rule.get("zone") or rule.get("zone") == zone]
        denies = [rule for rule in relevant if rule.get("effect") == "deny"]
        allows = [rule for rule in relevant if rule.get("effect") == "allow"]
        if denies:
            return {"state": "blocked", "reason": str(denies[0].get("summary") or "Passende firewalld-Sperrregel")}
        if allows:
            return {"state": "allowed", "reason": str(allows[0].get("summary") or "Passende firewalld-Freigabe")}
        zone_meta = next((item for item in snapshot.get("zones", []) if isinstance(item, dict) and item.get("zone") == zone), {})
        target = str(zone_meta.get("target") or "").upper()
        if target == "ACCEPT":
            return {"state": "allowed", "reason": f"firewalld-Zone {zone} hat Target ACCEPT."}
        if target in {"DROP", "REJECT"}:
            return {"state": "blocked", "reason": f"firewalld-Zone {zone} hat Target {target}."}
        return {"state": "blocked", "reason": "In der aktiven firewalld-Zone ist keine passende Freigabe vorhanden."}
    return {"state": "unknown", "reason": "Firewall-Backend ist nicht eindeutig auswertbar."}


def firewall_status(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path or default_config_path())
    store = FirewallControlStore(path)
    snapshot = store.snapshot()
    listeners = _listener_snapshot()
    services = service_port_inventory(path)
    for service in services:
        port_states = []
        for port in service["ports"]:
            protocol = port["protocol"]
            start, end = int(port["port_start"]), int(port["port_end"])
            listener = any(row["protocol"] == protocol and start <= int(row["port"]) <= end for row in listeners)
            decision = firewall_decision(snapshot, protocol, start, end, service.get("bind", ""))
            port_states.append({**port, "listening": listener, "firewall": decision})
        service["port_status"] = port_states
        states = {row["firewall"]["state"] for row in port_states}
        if "unknown" in states:
            service["firewall_state"] = "unknown"
        elif "blocked" in states and ("allowed" in states or "inactive" in states):
            service["firewall_state"] = "partial"
        elif "blocked" in states:
            service["firewall_state"] = "blocked"
        elif states == {"local-only"}:
            service["firewall_state"] = "local-only"
        elif "allowed" in states:
            service["firewall_state"] = "allowed"
        elif "inactive" in states:
            service["firewall_state"] = "inactive"
        else:
            service["firewall_state"] = "unknown"
        service["listening"] = any(row["listening"] for row in port_states)
    return {"snapshot": snapshot, "services": services, "agent_socket": str(AGENT_SOCKET), "listener_tool": bool(shutil.which("ss"))}


def rules_for_service(service: dict[str, Any], effect: str) -> list[dict[str, Any]]:
    if effect not in EFFECTS:
        raise ValueError("Aktion muss allow oder deny sein")
    if _is_loopback_bind(str(service.get("bind", ""))):
        raise ValueError("Nur an Loopback gebundene Dienste benötigen keine Firewallregel")
    return normalize_rules([{**port, "effect": effect, "label": str(service.get("name") or service.get("id") or "Dienst")} for port in service.get("ports", [])])
