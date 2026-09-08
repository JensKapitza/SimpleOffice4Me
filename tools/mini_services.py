#!/usr/bin/env python3
"""Dedicated DHCP/DNS worker for SimpleOffice4Me Mini Services."""

from __future__ import annotations

import argparse
import json
import signal
import threading
import time
from pathlib import Path

from app.mini_services import (
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

    def event(self, row: dict[str, object]) -> None:
        self.events.append(row)
        self.events = self.events[-50:]

    def start(self) -> None:
        config = load_config(self.config_path)
        dhcp = config["dhcp"]
        dns = config["dns"]
        if dns.get("enabled"):
            self.dns = DnsService(dns, self.config_path, self.event)
            self.dns.start()
        if dhcp.get("enabled"):
            self.dhcp = DhcpService(dhcp, self.config_path, self.event)
            self.dhcp.start()
        self.write_status("running")
        refresh_interval = max(3600, int(dns.get("blocklist_refresh_hours", 24)) * 3600)
        next_refresh = time.monotonic() + 5
        while not self.stop_event.wait(2):
            if dns.get("enabled") and dns.get("blocklist_urls") and time.monotonic() >= next_refresh:
                try:
                    refresh_blocklists(config, self.config_path)
                    if self.dns is not None:
                        self.dns.blocked = self.dns._load_blocked()
                except Exception as exc:
                    self.event({"service": "dns", "action": "blocklist_refresh_failed", "error": type(exc).__name__})
                next_refresh = time.monotonic() + refresh_interval
            self.write_status("running")

    def stop(self) -> None:
        self.stop_event.set()
        if self.dhcp is not None:
            self.dhcp.stop()
        if self.dns is not None:
            self.dns.stop()
        self.write_status("stopped")

    def write_status(self, state: str) -> None:
        write_status(
            {
                "state": state,
                "pid": __import__("os").getpid(),
                "started_at": self.started_at,
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
                "dhcp_running": self.dhcp is not None,
                "dns_running": self.dns is not None,
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
