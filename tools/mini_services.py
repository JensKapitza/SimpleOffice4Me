#!/usr/bin/env python3
"""Dedicated DHCP/DNS/TFTP/routing worker for SimpleOffice4Me Mini Services."""

from __future__ import annotations

import argparse
import os
import signal
import threading
import time
from pathlib import Path

from simpleoffice_mini_services import (
    DnsService,
    default_config_path,
    load_config,
    read_blocklist_meta,
    refresh_blocklists,
    write_status,
)
from app.network_boot import TftpService, boot_settings_path, load_boot_settings
from app.network_boot_dhcp import BootAwareDhcpService
from app.network_gateway_runtime import (
    apply_gateway,
    disable_gateway,
    gateway_settings_path,
    load_gateway_settings,
)


class Worker:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.stop_event = threading.Event()
        self.dhcp: BootAwareDhcpService | None = None
        self.dns: DnsService | None = None
        self.tftp: TftpService | None = None
        self.gateway_active = False
        self.gateway_status: dict[str, object] = {}
        self.events: list[dict[str, object]] = []
        self.started_at = time.time()
        self.config: dict[str, object] = {}
        self.config_signature: tuple[int, int, int] = (-1, -1, -1)
        self.next_blocklist_refresh = time.monotonic() + 5

    def event(self, row: dict[str, object]) -> None:
        self.events.append({"at": time.time(), **row})
        self.events = self.events[-50:]

    @staticmethod
    def _mtime(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1

    def _signature(self) -> tuple[int, int, int]:
        return (
            self._mtime(self.config_path),
            self._mtime(boot_settings_path(self.config_path)),
            self._mtime(gateway_settings_path(self.config_path)),
        )

    def _stop_network_services(self, *, disable_routing: bool = True) -> None:
        if self.dhcp is not None:
            self.dhcp.stop(); self.dhcp = None
        if self.dns is not None:
            self.dns.stop(); self.dns = None
        if self.tftp is not None:
            self.tftp.stop(); self.tftp = None
        if disable_routing and self.gateway_active:
            try:
                disable_gateway(load_gateway_settings(self.config_path))
            except Exception as exc:
                self.event({"service": "gateway", "action": "disable_failed", "error": type(exc).__name__, "message": str(exc)[:300]})
            self.gateway_active = False

    def _load_network_services(self) -> None:
        self._stop_network_services()
        config = load_config(self.config_path)
        dhcp = config["dhcp"]
        dns = config["dns"]
        boot = load_boot_settings(self.config_path)
        gateway = load_gateway_settings(self.config_path)
        gateway["internal_network"] = dhcp["network"]
        started: list[str] = []

        try:
            if dns.get("enabled"):
                self.dns = DnsService(dns, self.config_path, self.event)
                self.dns.start(); started.append("dns")
            if dhcp.get("enabled"):
                self.dhcp = BootAwareDhcpService(dhcp, self.config_path, self.event)
                self.dhcp.start(); started.append("dhcp")
            if boot.get("enabled") and boot.get("tftp_enabled"):
                self.tftp = TftpService(boot, self.config_path, self.event)
                self.tftp.start(); started.append("tftp")
        except Exception:
            self._stop_network_services()
            raise

        try:
            if gateway.get("enabled") and gateway.get("mode") != "off":
                result = apply_gateway(gateway, server_ip=str(dhcp.get("server_ip") or ""))
                self.gateway_active = bool(result.get("ok"))
                self.gateway_status = result
                if self.gateway_active:
                    started.append("gateway")
            else:
                self.gateway_status = disable_gateway(gateway)
                self.gateway_active = False
        except Exception as exc:
            self.gateway_active = False
            self.gateway_status = {"error": str(exc)[:500], "error_type": type(exc).__name__}
            self.event({"service": "gateway", "action": "apply_failed", "error": type(exc).__name__, "message": str(exc)[:300]})

        self.config = config
        self.config_signature = self._signature()
        self.next_blocklist_refresh = time.monotonic() + 5
        self.event({"service": "worker", "action": "configuration_loaded", "started": started})

    def start(self) -> None:
        try:
            self._load_network_services()
        except Exception as exc:
            self.event({"service": "worker", "action": "configuration_failed", "error": type(exc).__name__, "message": str(exc)[:300]})
        self.write_status("running")
        while not self.stop_event.wait(2):
            signature = self._signature()
            if signature != self.config_signature:
                try:
                    self._load_network_services()
                except Exception as exc:
                    self.config_signature = signature
                    self.event({"service": "worker", "action": "reload_failed", "error": type(exc).__name__, "message": str(exc)[:300]})
            dns = self.config.get("dns", {}) if isinstance(self.config, dict) else {}
            if isinstance(dns, dict) and dns.get("enabled") and dns.get("blocklist_urls") and time.monotonic() >= self.next_blocklist_refresh:
                try:
                    refresh_blocklists(self.config, self.config_path)
                    if self.dns is not None:
                        self.dns.blocked = self.dns._load_blocked()
                    self.event({"service": "dns", "action": "blocklists_refreshed"})
                except Exception as exc:
                    self.event({"service": "dns", "action": "blocklist_refresh_failed", "error": type(exc).__name__, "message": str(exc)[:300]})
                interval = max(3600, int(dns.get("blocklist_refresh_hours", 24)) * 3600)
                self.next_blocklist_refresh = time.monotonic() + interval
            self.write_status("running")

    def stop(self) -> None:
        self.stop_event.set()
        self._stop_network_services()
        self.write_status("stopped")

    def write_status(self, state: str) -> None:
        write_status(
            {
                "state": state,
                "pid": os.getpid(),
                "started_at": self.started_at,
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
                "dhcp_running": self.dhcp is not None,
                "dns_running": self.dns is not None,
                "tftp_running": self.tftp is not None,
                "gateway_running": self.gateway_active,
                "gateway": self.gateway_status,
                "config_signature": list(self.config_signature),
                "blocklist": read_blocklist_meta(self.config_path),
                "events": self.events[-20:],
            },
            self.config_path,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me DHCP/DNS/TFTP/Routing Mini Services")
    parser.add_argument("--config", default=str(default_config_path()))
    args = parser.parse_args()
    worker = Worker(Path(args.config))

    def request_stop(_signum: int, _frame: object) -> None:
        worker.stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        worker.start()
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
