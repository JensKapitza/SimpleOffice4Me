#!/usr/bin/env python3
"""Dedicated DHCP/DNS worker for SimpleOffice4Me Mini Services."""

from __future__ import annotations

import argparse
import os
import signal
import threading
import time
from pathlib import Path

from simpleoffice_mini_services import (
    DhcpService,
    DnsService,
    default_config_path,
    load_config,
    read_blocklist_meta,
    refresh_blocklists,
    write_status,
)


class Worker:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.stop_event = threading.Event()
        self.dhcp: DhcpService | None = None
        self.dns: DnsService | None = None
        self.events: list[dict[str, object]] = []
        self.started_at = time.time()
        self.config: dict[str, object] = {}
        self.config_mtime_ns = -1
        self.next_blocklist_refresh = time.monotonic() + 5

    def event(self, row: dict[str, object]) -> None:
        self.events.append({"at": time.time(), **row})
        self.events = self.events[-50:]

    def _mtime(self) -> int:
        try:
            return self.config_path.stat().st_mtime_ns
        except OSError:
            return -1

    def _stop_network_services(self) -> None:
        if self.dhcp is not None:
            self.dhcp.stop()
            self.dhcp = None
        if self.dns is not None:
            self.dns.stop()
            self.dns = None

    def _load_network_services(self) -> None:
        self._stop_network_services()
        config = load_config(self.config_path)
        dhcp = config["dhcp"]
        dns = config["dns"]
        try:
            if dns.get("enabled"):
                self.dns = DnsService(dns, self.config_path, self.event)
                self.dns.start()
            if dhcp.get("enabled"):
                self.dhcp = DhcpService(dhcp, self.config_path, self.event)
                self.dhcp.start()
        except Exception:
            self._stop_network_services()
            raise
        self.config = config
        self.config_mtime_ns = self._mtime()
        self.next_blocklist_refresh = time.monotonic() + 5
        self.event({"service": "worker", "action": "configuration_loaded"})

    def start(self) -> None:
        try:
            self._load_network_services()
        except Exception as exc:
            self.event({"service": "worker", "action": "configuration_failed", "error": type(exc).__name__, "message": str(exc)[:300]})
        self.write_status("running")
        while not self.stop_event.wait(2):
            current_mtime = self._mtime()
            if current_mtime != self.config_mtime_ns:
                try:
                    self._load_network_services()
                except Exception as exc:
                    self.config_mtime_ns = current_mtime
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
                "config_mtime_ns": self.config_mtime_ns,
                "blocklist": read_blocklist_meta(self.config_path),
                "events": self.events[-20:],
            },
            self.config_path,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me DHCP/DNS Mini Services")
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
