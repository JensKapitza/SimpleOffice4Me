"""Reproducible listener lifecycle measurements on loopback; no dependencies.

Run from the repository root: python -m tools.mini_services_benchmark
This measures listener readiness/cleanup, not LAN protocol throughput or hardware.
"""
import argparse
import copy
import json
import platform
import statistics
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

from simpleoffice_mini_core import DEFAULT_CONFIG
from simpleoffice_mini_runtime import DhcpService, DnsService
from simpleoffice_network_boot import DEFAULT_BOOT_SETTINGS, TftpService
from simpleoffice_service_lifecycle import service_health
from simpleoffice_sip_runtime import SipRegistrarService


def _service_factories(path):
    def dhcp():
        return DhcpService({**copy.deepcopy(DEFAULT_CONFIG["dhcp"]), "bind": "127.0.0.1", "interface": "", "port": 0}, path)

    def dns():
        return DnsService({**copy.deepcopy(DEFAULT_CONFIG["dns"]), "bind": ["127.0.0.1"], "port": 0}, path)

    def tftp():
        service = TftpService(copy.deepcopy(DEFAULT_BOOT_SETTINGS), path)
        service.settings.update(tftp_bind="127.0.0.1", tftp_port=0)
        return service

    def sip():
        service = SipRegistrarService(path)
        service.settings.update(bind_host="127.0.0.1", registrar_port=0)
        return service

    return {"dhcp": dhcp, "dns": dns, "tftp": tftp, "sip": sip}


def _construction_samples(factory, iterations):
    elapsed = []
    peak_kib = []
    for _ in range(iterations):
        tracemalloc.start()
        before = time.perf_counter()
        service = factory()
        elapsed.append((time.perf_counter() - before) * 1000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_kib.append(peak / 1024)
        del service
    return elapsed, peak_kib


def measure(iterations=5):
    if type(iterations) is not int or not 1 <= iterations <= 50:
        raise ValueError("iterations must be an integer between 1 and 50")
    baseline = set(threading.enumerate())
    report = {"python": platform.python_version(), "platform": platform.platform(),
              "iterations": iterations,
              "scope": "loopback listener lifecycle and service-object construction; no process cold start, protocol load, LAN throughput or total RSS",
              "services": {}}
    with tempfile.TemporaryDirectory(prefix="mini-benchmark-") as temporary:
        path = Path(temporary) / "config.json"
        factories = _service_factories(path)
        for name, factory in factories.items():
            construct_ms, python_heap_peak_kib = _construction_samples(factory, iterations)
            service = factory()
            samples = {
                "construct_ms": construct_ms,
                "python_heap_peak_kib": python_heap_peak_kib,
                "start_ms": [],
                "stop_ms": [],
                "duplicate_start_ms": [],
                "cpu_ms": [],
            }
            for _ in range(iterations):
                cpu = time.process_time()
                try:
                    before = time.perf_counter()
                    service.start()
                    samples["start_ms"].append((time.perf_counter() - before) * 1000)
                    if not service_health(service):
                        raise RuntimeError(f"{name}: listener not healthy after start")
                    before = time.perf_counter()
                    service.start()
                    samples["duplicate_start_ms"].append((time.perf_counter() - before) * 1000)
                finally:
                    before = time.perf_counter()
                    service.stop()
                    samples["stop_ms"].append((time.perf_counter() - before) * 1000)
                service.stop()
                if service_health(service):
                    raise RuntimeError(f"{name}: listener still healthy after stop")
                samples["cpu_ms"].append((time.process_time() - cpu) * 1000)
            report["services"][name] = {
                metric: {"median": round(statistics.median(values), 3), "max": round(max(values), 3)}
                for metric, values in samples.items()
            }
    report["remaining_threads"] = [thread.name for thread in threading.enumerate() if thread not in baseline]
    if report["remaining_threads"]:
        raise RuntimeError("Benchmark left listener threads running")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(measure(args.iterations), indent=2))


if __name__ == "__main__":
    main()
