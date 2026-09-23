#!/usr/bin/env python3
"""Dedicated DHCP/DNS/TFTP/routing/SIP worker; no Flask dependency."""
from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import signal
import sqlite3
import threading
import time
from pathlib import Path

from tools.mini_services_web import WEB_SERVICES, ClientError, web_command

from simpleoffice_mini_services import (
    DnsService, default_config_path, load_config, read_blocklist_meta,
    refresh_blocklists, write_status, read_status, state_dir,
)
from simpleoffice_network_boot import TftpService, boot_settings_path, load_boot_settings
from simpleoffice_mini_runtime import detect_foreign_dhcp_servers
from simpleoffice_network_boot_dhcp import BootAwareDhcpService
from simpleoffice_network_gateway_runtime import (
    apply_gateway, clear_gateway_ownership, disable_gateway, gateway_health,
    gateway_settings_path, load_gateway_ownership, load_gateway_settings,
    remember_gateway_ownership,
)
from simpleoffice_sip_runtime import SipRegistrarService, telephony_db_path, effective_sip_settings
from simpleoffice_connection_relay import (
    TurnRelayService, load_relay_settings, relay_secrets_path, relay_settings_path,
)
from simpleoffice_service_lifecycle import (
    DhcpConflictDetected, DhcpConflictProbeUnavailable, ServiceState, error_detail, service_health,
)
from simpleoffice_mini_control import ControlStore
from simpleoffice_network_gateway import interfaces_snapshot, binding_available, detect_interfaces, platform_kind

LOG = logging.getLogger("simpleoffice.mini_services")
SERVICE_NAMES = {"dhcp": "DHCP", "dns": "DNS", "tftp": "TFTP / Netzwerkboot", "sip": "SIP", "gateway": "Routing / NAT", "relay": "STUN / TURN Relay"}


class Worker:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.stop_event = threading.Event()
        self.dhcp = self.dns = self.tftp = self.sip = self.relay = None
        self.sip_status = {}
        self.gateway_active = False
        self.gateway_status = {}
        self.gateway_health = {"ok": False, "message": "Gateway ist gestoppt."}
        self.next_gateway_health = 0
        self.events = []
        self.event_lock = threading.Lock()
        self.started_at = time.time()
        self.config = {}
        self.config_signature = (-1, -1, -1, -1)
        self.desired = {}
        self.states = {key: ServiceState(key, name) for key, name in SERVICE_NAMES.items()}
        self.config_error = None
        self.config_loaded = False
        self.control = ControlStore(config_path)
        self.preferences = self.control.preferences()
        self.manual_states = {}
        self.next_blocklist_refresh = time.monotonic() + 5
        self.blocklist_thread = None
        self.network_snapshot = {"available": False, "interfaces": []}
        self.next_network_scan = 0

    def event(self, row: dict[str, object]) -> None:
        # Exception strings may contain packets, URLs or credentials.
        clean = {key: row[key] for key in ("service", "action", "error", "operation_id") if key in row}
        clean.update(at=time.time(), severity="error" if row.get("error") else "info")
        clean.update(event=clean.get("action", "event"), timestamp=clean["at"])
        with self.event_lock:
            self.events.append(clean)
            self.events = self.events[-50:]
        LOG.log(logging.ERROR if row.get("error") else logging.INFO, "%s", json.dumps(clean))

    @staticmethod
    def _mtime(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1

    def _signature(self):
        return tuple(self._mtime(path) for path in (
            self.config_path, boot_settings_path(self.config_path),
            gateway_settings_path(self.config_path), telephony_db_path(self.config_path),
            relay_settings_path(self.config_path), relay_secrets_path(self.config_path)))

    def _stop_one(self, name: str) -> bool:
        state = self.states[name]
        state.state = "stopping"
        try:
            if name == "gateway":
                settings = self.desired.get(name, (False, None))[1] if self.gateway_active else None
                if settings is None:
                    settings = load_gateway_ownership(self.config_path)
                if settings is not None:
                    result = disable_gateway(settings)
                    if not result.get("ok"):
                        raise RuntimeError("Gateway konnte nicht deaktiviert werden")
                    clear_gateway_ownership(self.config_path)
                    self.gateway_status = result
                self.gateway_active = False
                self.gateway_health = {"ok": False, "message": "Eigene Gateway-Regeln sind entfernt. Globales Forwarding bleibt unverändert."}
            else:
                service = getattr(self, name)
                if service is not None:
                    service.stop()
                    setattr(self, name, None)
        except Exception as exc:
            if name == "gateway":
                self.gateway_health = {"ok": None, "message": "Gateway konnte nicht gestoppt werden. Diagnose und Dienstrechte prüfen."}
            state.failed(exc)
            self.event({"service": name, "action": "stop_failed", "error": type(exc).__name__})
            return False
        state.state = "stopped"
        state.retry_at = None
        return True

    def _stop_network_services(self, *, disable_routing: bool = True) -> None:
        for name in SERVICE_NAMES:
            if name != "gateway" or disable_routing:
                self._stop_one(name)

    def _start_one(self, name: str) -> None:
        state = self.states[name]
        enabled, settings = self.desired[name]
        if not enabled or self.stop_event.is_set():
            state.state = "disabled" if not enabled else "stopped"
            return
        if not self._network_available(name):
            state.state = "waiting"
            state.last_error = error_detail(OSError(errno.EADDRNOTAVAIL, ""))
            state.last_error_at = time.time()
            state.retry_at = None
            return
        state.state = "starting"
        service = None
        try:
            if name == "gateway":
                # Persist ownership before touching host networking. If the
                # process crashes during apply, the next worker can still
                # identify and remove only the resources SimpleOffice owns.
                remember_gateway_ownership(settings, self.config_path)
                result = apply_gateway(settings, server_ip=str(self.config["dhcp"]["server_ip"]))
                if not result.get("ok"):
                    raise RuntimeError("Routing-Konfiguration konnte nicht angewendet werden")
                self.gateway_status = result
                self.gateway_active = True
                self.next_gateway_health = 0
            else:
                if name == "dhcp":
                    try:
                        conflicts = detect_foreign_dhcp_servers(settings)
                    except (OSError, ValueError) as exc:
                        raise DhcpConflictProbeUnavailable() from exc
                    if conflicts:
                        raise DhcpConflictDetected()
                factories = {"dhcp": BootAwareDhcpService, "dns": DnsService, "tftp": TftpService}
                if name == "sip":
                    service = SipRegistrarService(self.config_path, self.event)
                elif name == "relay":
                    service = TurnRelayService(settings, self.config_path)
                else:
                    service = factories[name](settings, self.config_path, self.event)
                service.start()
                setattr(self, name, service)
            state.running()
            self.event({"service": name, "action": "started"})
        except Exception as exc:
            if name == "gateway":
                if not self._stop_one(name):
                    # Keep the ownership marker so a later retry can clean up.
                    state.retry_at = None
                    return
            elif service is not None:
                setattr(self, name, service)
                if not self._stop_one(name):
                    # Never create a replacement while ownership is unclear.
                    state.retry_at = None
                    return
            state.failed(exc)
            self.event({"service": name, "action": "start_failed", "error": type(exc).__name__})

    def _reload_gateway(self, specification) -> None:
        """Reload without deleting active rules when the backend can preserve them."""
        state = self.states["gateway"]
        try:
            # Linux uses fixed owned tables; Windows reload is limited to the
            # same NAT name/prefix. Persist before applying for crash cleanup.
            # A bounded wait may still leave the application outcome unknown.
            remember_gateway_ownership(specification[1], self.config_path)
            result = apply_gateway(specification[1], server_ip=str(specification[1].get("_server_ip", self.config["dhcp"]["server_ip"])))
            if not result.get("ok"):
                raise RuntimeError("Gateway-Reload fehlgeschlagen")
        except Exception as exc:
            state.state = "degraded"
            state.last_error = error_detail(exc)
            state.last_error_at = time.time()
            state.retry_at = None
            self.next_gateway_health = 0
            self.event({"service": "gateway", "action": "reload_failed", "error": type(exc).__name__})
            # Never delete retained resources after a reload failure. Linux
            # transactions preserve old rules on rejection; Windows can leave
            # partial forwarding changes. Recheck health after any timeout.
            raise
        self.desired["gateway"] = specification
        self.gateway_status = result
        state.config = {key: specification[1][key] for key in ("enabled", "mode")}
        state.retry_count = 0
        state.retry_at = None
        state.state = "running"
        state.last_error = None
        state.last_error_at = None
        self.next_gateway_health = 0
        self.event({"service": "gateway", "action": "reloaded"})

    def _replace_windows_gateway(self, specification):
        """Restore the previous owned NAT after a rejected prefix/mode change."""
        previous = self.desired["gateway"]
        if previous[1]["nat_name"] != specification[1]["nat_name"]:
            # A different name can refer to a foreign NAT. Never remove it as
            # cleanup for an unsuccessful attempt to take ownership.
            raise ValueError("NAT-Namenswechsel benötigt einen expliziten Stop")
        state = self.states["gateway"]
        if not self._stop_one("gateway"):
            state.retry_at = None
            raise RuntimeError("Vorheriges Gateway konnte nicht bestätigt gestoppt werden")
        self.desired["gateway"] = specification
        try:
            self._reload_gateway(specification)
            self.gateway_active = True
        except Exception as original:
            # Do not automatically repeat a disruptive switch on every tick.
            self.config_signature = self._signature()
            self.config_error = error_detail(original)
            try:
                # This also confirms removal after an ambiguous apply timeout.
                # If cleanup is uncertain, preserve its marker and stop here.
                if not self._stop_one("gateway"):
                    raise RuntimeError("Neue Gateway-Konfiguration konnte nicht bereinigt werden")
                self.desired["gateway"] = previous
                self._reload_gateway(previous)
                self.gateway_active = True
            except Exception as rollback_error:
                state.state = "failed"
                state.last_error = error_detail(rollback_error)
                state.last_error_at = time.time()
                state.retry_at = None
                self.event({"service": "gateway", "action": "rollback_failed", "error": type(rollback_error).__name__})
                raise RuntimeError("Gateway-Wiederherstellung fehlgeschlagen; Status und Dienstrechte prüfen") from rollback_error
            state.state = "degraded"
            state.last_error = error_detail(original)
            state.last_error_at = time.time()
            state.retry_at = None
            self.event({"service": "gateway", "action": "rollback_restored"})
            raise RuntimeError("Gateway-Umstellung fehlgeschlagen; vorherige Konfiguration wiederhergestellt") from original

    def _can_reload_gateway(self, specification):
        if not self.gateway_active or not specification[0]:
            return False
        if platform_kind() == "linux":
            return True
        old = self.desired.get("gateway", (False, {}))[1]
        return platform_kind() == "windows" and all(
            old.get(key) == specification[1].get(key)
            for key in ("mode", "nat_name", "internal_network"))

    def _load_network_services(self, *, force_gateway_reload=False) -> None:
        # Validate BEFORE touching listeners; compare effective settings, not DB
        # mtime, so a changed phone registration does not restart every service.
        signature = self._signature()
        config = load_config(self.config_path)
        boot = load_boot_settings(self.config_path)
        gateway = load_gateway_settings(self.config_path)
        if config["dhcp"]["enabled"]:
            gateway["internal_network"] = config["dhcp"]["network"]
        if gateway["auto_detect"] and self.network_snapshot.get("available"):
            found = detect_interfaces(gateway["internal_network"], server_ip=config["dhcp"]["server_ip"], snapshot=self.network_snapshot)
            gateway["internal_interface"] = gateway["internal_interface"] or found["internal_interface"]
            gateway["external_interface"] = gateway["external_interface"] or found["external_interface"]
        sip = effective_sip_settings(self.config_path)
        relay = load_relay_settings(self.config_path)
        desired = {
            "dhcp": (bool(config["dhcp"]["enabled"]), {**config["dhcp"], "_boot": boot}),
            "dns": (bool(config["dns"]["enabled"]), config["dns"]),
            "tftp": (bool(boot["enabled"] and boot["tftp_enabled"]), boot),
            "sip": (True, sip),
            "gateway": (bool(gateway["enabled"] and gateway["mode"] != "off"), {**gateway, "_server_ip": config["dhcp"]["server_ip"]}),
            "relay": (bool(relay["enabled"]), relay),
        }
        for name, (enabled, settings) in desired.items():
            preference = self.preferences[name]
            requested = self.manual_states.get(name, preference["autostart"])
            desired[name] = (enabled and preference["enabled"] and requested, settings)
        self.config = config
        for name, specification in desired.items():
            if self.desired.get(name) == specification and not (name == "gateway" and force_gateway_reload):
                continue
            if name == "gateway" and self._can_reload_gateway(specification):
                self._reload_gateway(specification)
                continue
            if name == "gateway" and self.gateway_active and specification[0] and platform_kind() == "windows":
                self._replace_windows_gateway(specification)
                continue
            if not self._stop_one(name):
                continue
            self.desired[name] = specification
            state = self.states[name]
            state.retry_count = 0
            state.config = {key: value for key, value in specification[1].items()
                            if key in {"enabled", "bind", "port", "interface", "tftp_bind", "tftp_port", "bind_host", "registrar_port", "mode", "timeout", "listen_ip", "public_host", "turn_port", "tls_enabled", "turn_tls_port", "min_port", "max_port"}}
            self._start_one(name)
        self.config_error = None
        self.config_signature = signature
        self.event({"service": "worker", "action": "configuration_loaded"})

    def _refresh_blocklists(self, config) -> None:
        try:
            result = refresh_blocklists(config, self.config_path, cancel_event=self.stop_event)
            if result.get("cancelled"):
                return
            service = self.dns
            if service is not None:
                service.blocked = service._load_blocked()
            self.event({"service": "dns", "action": "blocklists_refreshed"})
        except Exception as exc:
            self.event({"service": "dns", "action": "blocklist_refresh_failed", "error": type(exc).__name__})

    def _network_available(self, name):
        settings = self.desired.get(name, (False, {}))[1]
        addresses = {"dhcp": ("bind", "server_ip"), "dns": ("bind",), "tftp": ("tftp_bind",), "sip": ("bind_host",), "gateway": (), "relay": ("listen_ip",)}[name]
        interfaces = ("internal_interface", "external_interface") if name == "gateway" else (() if name == "relay" else ("interface",))
        return binding_available(settings, self.network_snapshot, addresses=addresses, interfaces=interfaces)

    def _refresh_network(self):
        if time.monotonic() < self.next_network_scan:
            return
        self.next_network_scan = time.monotonic() + 30
        snapshot = interfaces_snapshot()
        changed = snapshot != self.network_snapshot
        self.network_snapshot = snapshot
        if changed and snapshot.get("available") and not self.config_error:
            # Re-evaluate only effective bindings. Unchanged configurations do
            # not restart listeners; manually stopped services stay stopped.
            self._load_network_services()

    def tick(self) -> None:
        signature = self._signature()
        preferences = self.control.preferences()
        if preferences != self.preferences:
            self.preferences = preferences
            self.config_loaded = False
        if signature != self.config_signature or not self.config_loaded:
            self.config_loaded = True
            try:
                self._load_network_services()
            except Exception as exc:
                self.config_signature = signature
                self.config_error = error_detail(exc)
                self.event({"service": "worker", "action": "configuration_failed", "error": type(exc).__name__})
        command = self.control.claim()
        if command:
            self._execute(command)
        try:
            self._refresh_network()
        except (OSError, ValueError, RuntimeError) as exc:
            self.event({"service": "worker", "action": "network_scan_failed", "error": type(exc).__name__})
        if self.gateway_active and time.monotonic() >= self.next_gateway_health:
            self.gateway_health = gateway_health(self.gateway_status)
            self.next_gateway_health = time.monotonic() + 15
            state = self.states["gateway"]
            if state.state in {"running", "degraded"}:
                if self.gateway_health["ok"] is False:
                    # Only a confirmed failure schedules the existing bounded
                    # recovery. An unreadable status must not mutate host rules.
                    state.failed(RuntimeError("Gateway-Healthcheck fehlgeschlagen"))
                    self.event({"service": "gateway", "action": "health_failed", "error": "RuntimeError"})
                else:
                    state.state = "running" if self.gateway_health["ok"] is True else "degraded"
        for name, state in self.states.items():
            if state.state in {"running", "degraded"} and not self._network_available(name):
                if self._stop_one(name):
                    state.state = "waiting"
                    state.last_error = error_detail(OSError(errno.EADDRNOTAVAIL, ""))
                    state.last_error_at = time.time()
                    self.event({"service": name, "action": "network_waiting"})
            if state.state == "waiting" and self.network_snapshot.get("available") and self._network_available(name):
                state.retry_count = 0
                self._start_one(name)
            if name != "gateway" and state.state == "running" and not service_health(getattr(self, name)):
                if self._stop_one(name):
                    state.failed(RuntimeError("Listener ist nicht aktiv"))
            if state.state == "failed" and state.retry_at is not None and time.monotonic() >= state.retry_at:
                if self._stop_one(name):
                    self._start_one(name)
        dns = self.config.get("dns", {})
        if dns.get("enabled") and dns.get("blocklist_urls") and time.monotonic() >= self.next_blocklist_refresh:
            if self.blocklist_thread is None or not self.blocklist_thread.is_alive():
                self.blocklist_thread = threading.Thread(target=self._refresh_blocklists, args=(self.config,), name="mini-blocklists", daemon=True)
                self.blocklist_thread.start()
                self.next_blocklist_refresh = time.monotonic() + max(3600, int(dns["blocklist_refresh_hours"]) * 3600)
        self.write_status("running")

    def start(self) -> None:
        self.control.recover_interrupted()
        self.tick()
        while not self.stop_event.wait(2):
            self.tick()

    def stop(self) -> None:
        self.stop_event.set()
        self._stop_network_services()
        self.write_status("stopped")

    def _execute(self, command):
        name, action = command["service"], command["action"]
        try:
            if name not in self.states or action not in {"start", "stop", "restart"}:
                raise ValueError("Unbekannter Befehl")
            if action == "stop":
                self.manual_states[name] = False
                if name in self.desired:
                    self.desired[name] = (False, self.desired[name][1])
                if not self._stop_one(name):
                    raise RuntimeError("Stop fehlgeschlagen")
            else:
                if not self.preferences[name]["enabled"]:
                    raise ValueError("Dienst zuerst in den Einstellungen aktivieren")
                reload_gateway = name == "gateway" and action == "restart" and self.gateway_active and platform_kind() in {"linux", "windows"}
                if action == "restart" and not reload_gateway and not self._stop_one(name):
                    raise RuntimeError("Stop fehlgeschlagen")
                self.manual_states[name] = True
                if reload_gateway:
                    self._load_network_services(force_gateway_reload=True)
                else:
                    self._load_network_services()
                state = self.states[name]
                if not self.desired[name][0]:
                    raise ValueError("Dienst zuerst in der Fachkonfiguration aktivieren")
                if state.state != "running":
                    state.retry_count = 0
                    self._start_one(name)
                if self.states[name].state != "running":
                    raise RuntimeError("Start fehlgeschlagen")
            self.control.finish(command["id"], True, self.states[name].snapshot(os.getpid()))
            self.event({"service": name, "action": action, "operation_id": command["id"]})
        except Exception as exc:
            self.control.finish(command["id"], False, error_detail(exc))
            self.event({"service": name, "action": action, "operation_id": command["id"], "error": type(exc).__name__})

    def write_status(self, state: str) -> None:
        services = {name: item.snapshot(os.getpid()) for name, item in self.states.items()}
        for name, item in services.items():
            item["settings"] = self.preferences[name]
            item["targets"] = self.network_snapshot.get("interfaces", [])
            if item["state"] == "waiting":
                item["health"] = {"ok": False, "message": "Wartet auf konfigurierte Netzwerkschnittstelle oder IPv4-Adresse. Automatische Prüfung alle 30 Sekunden."}
        if services["gateway"]["state"] != "waiting":
            services["gateway"]["health"] = self.gateway_health
        with self.event_lock:
            events = list(self.events[-20:])
        failed = self.config_error or any(item.state in {"failed", "degraded"} for item in self.states.values())
        write_status({
            "state": "degraded" if state == "running" and failed else state,
            "pid": os.getpid(), "started_at": self.started_at,
            "uptime_seconds": max(0, int(time.time() - self.started_at)),
            **{name + "_running": service_health(getattr(self, name)) for name in ("dhcp", "dns", "tftp", "sip", "relay")},
            "sip": self.sip.status() if self.sip is not None else self.sip_status,
            "relay": self.relay.status() if self.relay is not None else {},
            "gateway_running": self.gateway_active, "gateway": self.gateway_status,
            "services": services, "config_error": self.config_error,
            "config_signature": list(self.config_signature),
            "blocklist": read_blocklist_meta(self.config_path), "events": events,
        }, self.config_path)


def service_command(config_path, service, action, *, wait_seconds=5, operation_id=None):
    """Use the existing private mailbox; never instantiate another service owner."""
    status = read_status(config_path)
    if operation_id:
        row = ControlStore(config_path).operation(operation_id)
        if row is None or row["service"] != service:
            return {"error": "Aktion für diesen Dienst nicht gefunden."}, 1
        return row, 0 if row["state"] == "completed" else 1 if row["state"] == "failed" else 3
    available = status.get("state") in {"running", "degraded"} and not status.get("stale")
    if action == "status":
        row = status.get("services", {}).get(service, {"id": service, "state": "unavailable"})
        if not available:
            row = {**row, "state": "unavailable", "health": {"ok": False, "message": "Mini-Services Worker nicht erreichbar."}}
        return row, 0 if row.get("state") in {"running", "degraded"} else 3
    if not available:
        return {"error": "Mini-Services Worker nicht erreichbar. Zuerst SimpleOffice oder den Worker starten."}, 3
    store = ControlStore(config_path)
    row = store.enqueue(service, action)
    deadline = time.monotonic() + wait_seconds
    while True:
        row = store.operation(row["id"]) or row
        if row["state"] in {"completed", "failed"}:
            return row, 0 if row["state"] == "completed" else 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**row, "message": "Aktion noch offen; mit status --operation ID prüfen. Warteende bricht den Auftrag nicht ab."}, 3
        time.sleep(min(0.1, remaining))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me Netzwerk-, Audio- und HTTP-Boot-Dienste")
    parser.add_argument("--config", help="Konfigurationsdatei des Netzwerk-Workers")
    parser.add_argument("command", choices=("start", "status", "stop", "restart", "scan"), default="start", nargs="?")
    parser.add_argument("--service", choices=tuple(SERVICE_NAMES) + WEB_SERVICES, help="Einzeldienst über seinen vorhandenen Worker/Webprozess steuern")
    parser.add_argument("--wait", type=int, choices=range(0, 61), default=None, metavar="0..60", help="Maximale Wartezeit auf Einzelaktionen (Standard: 5 Sekunden)")
    parser.add_argument("--operation", help="Aktions-ID mit status --service prüfen")
    parser.add_argument("--web-url", help="Webprozess für Audio/HTTP-Boot; HTTPS oder lokale Loopback-IP")
    parser.add_argument("--username", help="SimpleOffice-Administrator für Audio/HTTP-Boot")
    parser.add_argument("--password-stdin", action="store_true", help="Passwort einmalig von stdin statt verdeckt vom Terminal lesen")
    parser.add_argument("--web-timeout", type=int, choices=range(1, 61), default=None, metavar="1..60", help="HTTP-Timeout je Anfrage; keine automatische Wiederholung")
    args = parser.parse_args(argv)
    web_service = args.service in WEB_SERVICES or (args.command == "scan" and args.service in SERVICE_NAMES)
    if web_service and (not args.username or args.operation or args.config or args.wait is not None):
        parser.error("Webaktionen benötigen --username; --operation, --wait und --config gelten nur für Netzwerkdienste (Webinstanz über --web-url)")
    if not web_service and (args.username or args.password_stdin or args.web_url is not None or args.web_timeout is not None or args.command == "scan"):
        parser.error("Anmeldung benötigt einen Audio/HTTP-Boot-Einzeldienst oder scan --service DIENST")
    if args.operation and (not args.service or args.command != "status"):
        parser.error("--operation benötigt status und --service")
    from tools import service_control
    config_path = Path(args.config or default_config_path()).expanduser().resolve()
    if args.service:
        try:
            if web_service:
                result, code = web_command(args.service, args.command, base_url=args.web_url or "http://127.0.0.1:8080",
                                           username=args.username, password_stdin=args.password_stdin,
                                           timeout=args.web_timeout or 10)
            else:
                result, code = service_command(config_path, args.service, args.command, wait_seconds=5 if args.wait is None else args.wait, operation_id=args.operation)
        except ClientError as exc:
            result, code = {"error": str(exc)}, 1
        except (OSError, sqlite3.Error, ValueError) as exc:
            result, code = {"error": error_detail(exc)}, 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if code:
            raise SystemExit(code)
        return
    # Custom configurations have their own existing PID-record directory.
    service_control.RUN_DIR = config_path.parent / "run"
    if args.command == "status":
        status = read_status(config_path)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        if status.get("state") not in {"running", "degraded"}:
            raise SystemExit(3)
        return
    if args.command in {"stop", "restart"}:
        if not service_control.stop(roles=["mini"]):
            raise SystemExit(1)
        if args.command == "stop":
            return
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    worker = Worker(config_path)
    def request_stop(_signum: int, _frame: object) -> None:
        worker.stop_event.set()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with service_control.exclusive_lease(state_dir(config_path) / "worker.lock") as acquired:
        if not acquired:
            print("Mini-Services Worker läuft bereits. Kein zweiter Worker wird gestartet.")
            return
        service_control.register("mini", os.getpid(), "tools.mini_services")
        try:
            worker.start()
        finally:
            try:
                worker.stop()
            finally:
                service_control.unregister("mini", os.getpid())


if __name__ == "__main__":
    main()
