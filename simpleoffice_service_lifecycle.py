"""Shared, dependency-free lifecycle primitives; no second service manager."""
from __future__ import annotations

import errno
import json
import logging
import socket
import threading
import time
import traceback
from contextlib import contextmanager
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path


@lru_cache(maxsize=1)
def application_version():
    # Status must stay cheap and independent of external git subprocesses.
    from simpleoffice_version import _project_version, PROJECT_ROOT
    return _project_version(PROJECT_ROOT)


def log_service_event(service: str, event: str, *, exc: Exception | None = None, operation_id: str | None = None, request_id: str | None = None):
    """Shared event format without exception payloads, source text or locals."""
    row = {"service": service, "event": event, "severity": "error" if exc else "info", "timestamp": time.time()}
    if operation_id:
        row["operation_id"] = operation_id
    if request_id:
        row["request_id"] = request_id
    if exc is not None:
        row["diagnostic"] = error_detail(exc)
        row["trace"] = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                        for frame in traceback.extract_tb(exc.__traceback__)[-8:]]
    logging.getLogger("simpleoffice.mini_services").log(logging.ERROR if exc else logging.INFO, "%s", json.dumps(row, ensure_ascii=False))


def service_health(service: Any) -> bool:
    """A live owned listener/process is required, not object existence."""
    if service is None or service.stop_event.is_set():
        return False
    if hasattr(service, "process"):
        process = getattr(service, "process", None)
        return process is not None and process.poll() is None
    sockets = getattr(service, "sockets", None)
    threads = getattr(service, "threads", None)
    if sockets is None:
        sockets = [service.socket]
        threads = [service.thread]
    return bool(sockets and threads and all(s is not None and s.fileno() >= 0 for s in sockets)
                and all(t is not None and t.is_alive() for t in threads))


def error_detail(exc: Exception) -> dict[str, str]:
    """Do not expose exception strings: they can contain credentials or packets."""
    code, message, action = "runtime_error", "Dienst konnte nicht gestartet werden.", "Diagnose und Einstellungen prüfen."
    if isinstance(exc, PermissionError):
        code, message, action = "permission_denied", "Berechtigung für den Dienst fehlt.", "Port und eingerichtete Dienstrechte prüfen; keine automatische Rechteerhöhung."
    elif isinstance(exc, OSError) and exc.errno == errno.EADDRINUSE:
        code, message, action = "address_in_use", "Adresse oder Port wird bereits verwendet.", "Anderen Dienst prüfen oder Port ändern."
    elif isinstance(exc, OSError) and exc.errno in {errno.EADDRNOTAVAIL, errno.ENETDOWN, errno.ENETUNREACH}:
        code, message, action = "network_unavailable", "Netzwerkschnittstelle ist nicht verfügbar.", "Netzwerk verbinden oder Interface neu auswählen."
    elif isinstance(exc, FileNotFoundError):
        code, message, action = "dependency_missing", "Benötigte Datei oder Systemkomponente fehlt.", "Voraussetzungen in der Dokumentation prüfen."
    elif isinstance(exc, ValueError):
        code, message, action = "invalid_config", "Dienstkonfiguration ist ungültig.", "Einstellungen prüfen und erneut speichern."
    return {"code": code, "message": message, "action": action,
            "diagnostic": type(exc).__name__ + (f" (errno {exc.errno})" if isinstance(exc, OSError) else "")}


@dataclass
class ServiceState:
    id: str
    name: str
    state: str = "stopped"
    started_at: float | None = None
    last_error: dict[str, str] | None = None
    last_error_at: float | None = None
    retry_count: int = 0
    retry_at: float | None = None
    config: dict[str, Any] = field(default_factory=dict)

    def failed(self, exc: Exception) -> None:
        self.state = "failed"
        self.last_error = error_detail(exc)
        self.last_error_at = time.time()
        self.retry_count += 1
        log_service_event(self.id, "failed", exc=exc)
        # A configuration edit / explicit restart resets this finite budget.
        self.retry_at = time.monotonic() + min(60, 2 ** self.retry_count) if self.retry_count < 6 else None

    def running(self) -> None:
        self.state = "running"
        self.started_at = time.time()
        self.retry_at = None

    def snapshot(self, pid: int) -> dict[str, Any]:
        ports = [self.config[key] for key in ("port", "tftp_port", "registrar_port", "turn_port", "turn_tls_port") if key in self.config]
        return {"id": self.id, "name": self.name, "version": application_version(), "state": self.state,
                "started_at": self.started_at,
                "uptime_seconds": max(0, int(time.time() - self.started_at)) if self.state == "running" and self.started_at else 0,
                "worker_id": pid, "last_error": self.last_error, "last_error_at": self.last_error_at,
                "retry_count": self.retry_count,
                "retry_in_seconds": max(0, int(self.retry_at - time.monotonic())) if self.retry_at else None,
                "requires": ["mini-services-worker"], "optional_requires": [], "provides": [self.id],
                "config": self.config, "ports": ports, "updated_at": time.time(), "health": {"ok": self.state == "running",
                    "message": "Socket gebunden, Listener aktiv" if self.state == "running" else self.state}}


class DatagramLifecycle:
    """Idempotent, serialized lifecycle for the existing DHCP/TFTP/SIP listeners.

    Subclasses supply _bind_options() and _loop(); protocol code is unchanged.
    """
    def start(self) -> None:
        with self.lifecycle_lock:
            if service_health(self):
                return
            self.stop()
            if hasattr(self, "tasks"):
                self.tasks.reset()
            self.stop_event.clear()
            address, name, interface, broadcast = self._bind_options()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # UDP REUSEADDR would permit two workers to share one port.
                if broadcast:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                if interface:
                    if not hasattr(socket, "SO_BINDTODEVICE"):
                        raise ValueError("Interface-Bindung ist auf dieser Plattform nicht verfügbar")
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b"\0")
                sock.bind(address)
                sock.settimeout(1)
                self.socket = sock
                self.thread = threading.Thread(target=self._loop, name=name, daemon=True)
                self.thread.start()
            except Exception:
                sock.close()
                self.socket = None
                self.thread = None
                self.stop_event.set()
                raise

    def stop(self) -> None:
        with self.lifecycle_lock:
            self.stop_event.set()
            if self.socket is not None:
                self.socket.close()
            if self.thread is not None:
                self.thread.join(timeout=3)
                if self.thread.is_alive():
                    raise RuntimeError("Listener wurde nicht rechtzeitig beendet")
            self.socket = None
            self.thread = None
            if hasattr(self, "tasks"):
                self.tasks.stop()


class BoundedTasks:
    """Bound active requests without an unbounded executor submission queue."""
    def __init__(self, limit: int = 32):
        self.limit = limit
        self.lock = threading.RLock()
        self.threads = set()
        self.sockets = set()
        self.stopping = False

    def reset(self):
        with self.lock:
            if self.threads:
                raise RuntimeError("Vorherige Anfragen werden noch beendet")
            self.stopping = False

    def submit(self, target, *args) -> bool:
        with self.lock:
            if self.stopping or len(self.threads) >= self.limit:
                return False
            def run():
                try:
                    target(*args)
                finally:
                    with self.lock:
                        self.threads.discard(threading.current_thread())
            thread = threading.Thread(target=run, name="mini-request", daemon=True)
            self.threads.add(thread)
            try:
                thread.start()
            except Exception:
                self.threads.discard(thread)
                raise
            return True

    @contextmanager
    def track(self, sock):
        with self.lock:
            if self.stopping:
                sock.close()
                raise OSError("Dienst wird beendet")
            self.sockets.add(sock)
        try:
            yield sock
        finally:
            with self.lock:
                self.sockets.discard(sock)

    def stop(self):
        with self.lock:
            self.stopping = True
            threads = list(self.threads)
            for sock in self.sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # UDP/unconnected sockets have no shutdown direction.
                sock.close()
        deadline = time.monotonic() + 3
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        with self.lock:
            if self.threads:
                raise RuntimeError("Anfragen werden noch beendet; kein zweiter Worker wird erzeugt")
