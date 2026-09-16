#!/usr/bin/env python3
"""Dedicated DHCP/DNS/TFTP/routing/SIP worker; no Flask dependency."""
from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import signal
import threading
import time
from pathlib import Path

from simpleoffice_mini_services import (
    DnsService, default_config_path, load_config, read_blocklist_meta,
    refresh_blocklists, write_status, read_status, state_dir,
)
from simpleoffice_network_boot import TftpService, boot_settings_path, load_boot_settings
from simpleoffice_network_boot_dhcp import BootAwareDhcpService
from simpleoffice_network_gateway_runtime import (
    apply_gateway, clear_gateway_ownership, disable_gateway, gateway_health,
    gateway_settings_path, load_gateway_ownership, load_gateway_settings,
    remember_gateway_ownership,
)
from simpleoffice_sip_runtime import SipRegistrarService, telephony_db_path, effective_sip_settings
from simpleoffice_service_lifecycle import ServiceState, error_detail, service_health
from simpleoffice_mini_control import ControlStore
from simpleoffice_network_gateway import interfaces_snapshot, binding_available, detect_interfaces

LOG = logging.getLogger("simpleoffice.mini_services")
SERVICE_NAMES = {"dhcp": "DHCP", "dns": "DNS", "tftp": "TFTP / Netzwerkboot", "sip": "SIP", "gateway": "Routing / NAT"}


class Worker:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.stop_event = threading.Event()
        self.dhcp = self.dns = self.tftp = self.sip = None
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
            gateway_settings_path(self.config_path), telephony_db_path(self.config_path)))

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
                factories = {"dhcp": BootAwareDhcpService, "dns": DnsService, "tftp": TftpService}
                service = SipRegistrarService(self.config_path, self.event) if name == "sip" else factories[name](settings, self.config_path, self.event)
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

    def _load_network_services(self) -> None:
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
        desired = {
            "dhcp": (bool(config["dhcp"]["enabled"]), {**config["dhcp"], "_boot": boot}),
            "dns": (bool(config["dns"]["enabled"]), config["dns"]),
            "tftp": (bool(boot["enabled"] and boot["tftp_enabled"]), boot),
            "sip": (True, sip),
            "gateway": (bool(gateway["enabled"] and gateway["mode"] != "off"), {**gateway, "_server_ip": config["dhcp"]["server_ip"]}),
        }
        for name, (enabled, settings) in desired.items():
            preference = self.preferences[name]
            requested = self.manual_states.get(name, preference["autostart"])
            desired[name] = (enabled and preference["enabled"] and requested, settings)
        self.config = config
        for name, specification in desired.items():
            if self.desired.get(name) == specification:
                continue
            if not self._stop_one(name):
                continue
            self.desired[name] = specification
            state = self.states[name]
            state.retry_count = 0
            state.config = {key: value for key, value in specification[1].items()
                            if key in {"enabled", "bind", "port", "interface", "tftp_bind", "tftp_port", "bind_host", "registrar_port", "mode", "timeout"}}
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
        addresses = {"dhcp": ("bind", "server_ip"), "dns": ("bind",), "tftp": ("tftp_bind",), "sip": ("bind_host",), "gateway": ()}[name]
        interfaces = ("internal_interface", "external_interface") if name == "gateway" else ("interface",)
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
                if action == "restart" and not self._stop_one(name):
                    raise RuntimeError("Stop fehlgeschlagen")
                self.manual_states[name] = True
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
            **{name + "_running": service_health(getattr(self, name)) for name in ("dhcp", "dns", "tftp", "sip")},
            "sip": self.sip.status() if self.sip is not None else self.sip_status,
            "gateway_running": self.gateway_active, "gateway": self.gateway_status,
            "services": services, "config_error": self.config_error,
            "config_signature": list(self.config_signature),
            "blocklist": read_blocklist_meta(self.config_path), "events": events,
        }, self.config_path)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me DHCP/DNS/TFTP/Routing/SIP Mini Services")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("command", choices=("start", "status", "stop", "restart"), default="start", nargs="?")
    args = parser.parse_args(argv)
    from tools import service_control
    config_path = Path(args.config).expanduser().resolve()
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
