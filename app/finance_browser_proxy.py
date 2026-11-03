"""Bounded local CONNECT proxy for the Santander browser session.

The browser keeps normal HTTPS certificate verification and SNI. DNS is resolved
once by this process, every answer must be globally routable, and the socket is
connected to the validated numeric address so a second DNS lookup cannot rebind
the connection to a local network target.
"""
from __future__ import annotations

import ipaddress
import queue
import select
import socket
import socketserver
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .settings_store import MAX_FINANCE_IMPORT_LIMIT_BYTES

_MAX_HEADER_BYTES = 16 * 1024
_MAX_CLIENTS = 16
_MAX_PENDING_BYTES_PER_DIRECTION = 256 * 1024
_MAX_DNS_LOOKUPS = 32
_DNS_LOOKUP_TIMEOUT_SECONDS = 5
_CONNECT_TIMEOUT_SECONDS = 10
_IDLE_TIMEOUT_SECONDS = 60
_MAX_SESSION_BYTES = 4 * MAX_FINANCE_IMPORT_LIMIT_BYTES
_DNS_LOOKUP_SLOTS = threading.BoundedSemaphore(_MAX_DNS_LOOKUPS)


def is_allowed_santander_host(host: str) -> bool:
    normalized = host.casefold().rstrip(".")
    return (
        len(normalized) <= 253
        and (normalized == "santander.de" or normalized.endswith(".santander.de"))
    )


def _public_addresses(host: str, port: int) -> list[tuple[int, int, int, tuple]]:
    if not is_allowed_santander_host(host) or port != 443:
        raise ValueError("proxy target is not an approved Santander HTTPS host")
    answers = _getaddrinfo_bounded(host, port)
    validated: list[tuple[int, int, int, tuple]] = []
    seen: set[tuple[int, str]] = set()
    for family, socktype, protocol, _canonical_name, sockaddr in answers:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise ValueError("proxy DNS returned an unsupported address family")
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            raise ValueError("proxy DNS returned a non-public address")
        key = (family, address.compressed)
        if key not in seen:
            seen.add(key)
            validated.append((family, socktype, protocol, sockaddr))
    if not validated:
        raise OSError("proxy DNS returned no usable address")
    return validated


def _getaddrinfo_bounded(host: str, port: int) -> list[tuple]:
    if not _DNS_LOOKUP_SLOTS.acquire(blocking=False):
        raise TimeoutError("DNS lookup concurrency limit reached")
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            answers = socket.getaddrinfo(
                host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
            result_queue.put((True, answers))
        except Exception as exc:
            result_queue.put((False, exc))
        finally:
            _DNS_LOOKUP_SLOTS.release()

    resolver = threading.Thread(
        target=resolve,
        name="santander-browser-dns",
        daemon=True,
    )
    try:
        resolver.start()
    except RuntimeError:
        _DNS_LOOKUP_SLOTS.release()
        raise
    try:
        succeeded, result = result_queue.get(timeout=_DNS_LOOKUP_TIMEOUT_SECONDS)
    except queue.Empty as exc:
        raise TimeoutError("DNS lookup exceeded its bounded timeout") from exc
    if not succeeded:
        if isinstance(result, Exception):
            raise result
        raise RuntimeError("DNS lookup failed")
    return result


def _connect_public_santander_host(host: str, port: int) -> socket.socket:
    addresses = _public_addresses(host, port)
    deadline = time.monotonic() + _CONNECT_TIMEOUT_SECONDS
    last_error: OSError | None = None
    for family, socktype, protocol, sockaddr in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        upstream: socket.socket | None = None
        try:
            upstream = socket.socket(family, socktype, protocol)
            upstream.settimeout(remaining)
            # sockaddr contains the numeric address returned by the validated DNS
            # lookup; socket.connect does not resolve the hostname again.
            upstream.connect(sockaddr)
            upstream.setblocking(False)
            return upstream
        except OSError as exc:
            last_error = exc
            if upstream is not None:
                upstream.close()
    if last_error is not None:
        raise last_error
    raise TimeoutError("proxy could not connect within its bounded timeout")


class _BoundedThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = _MAX_CLIENTS

    def __init__(self, address, handler, deadline: float):
        self.deadline = deadline
        self.stop_event = threading.Event()
        self._worker_slots = threading.BoundedSemaphore(_MAX_CLIENTS)
        self._byte_lock = threading.Lock()
        self._relayed_bytes = 0
        super().__init__(address, handler, bind_and_activate=True)

    def verify_request(self, request, client_address):
        try:
            return ipaddress.ip_address(client_address[0]).is_loopback
        except ValueError:
            return False

    def process_request(self, request, client_address):
        if not self._worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()

    def account_relayed_bytes(self, amount: int) -> bool:
        with self._byte_lock:
            if self._relayed_bytes + amount > _MAX_SESSION_BYTES:
                return False
            self._relayed_bytes += amount
            return True


class _ConnectHandler(socketserver.StreamRequestHandler):
    server: _BoundedThreadingServer

    def _reply(self, status: str) -> None:
        try:
            self.wfile.write(f"HTTP/1.1 {status}\r\nConnection: close\r\n\r\n".encode("ascii"))
            self.wfile.flush()
        except OSError:
            return

    def handle(self) -> None:
        if not self.client_address or not self.server.verify_request(self.request, self.client_address):
            self._reply("403 Forbidden")
            return
        try:
            first_line = self.rfile.readline(_MAX_HEADER_BYTES + 1)
            if len(first_line) > _MAX_HEADER_BYTES:
                self._reply("431 Request Header Fields Too Large")
                return
            fields = first_line.decode("ascii").rstrip("\r\n").split()
            if len(fields) != 3 or fields[0] != "CONNECT" or fields[2] not in {"HTTP/1.0", "HTTP/1.1"}:
                self._reply("405 Method Not Allowed")
                return
            total = len(first_line)
            while True:
                header = self.rfile.readline(_MAX_HEADER_BYTES - total + 1)
                total += len(header)
                if total > _MAX_HEADER_BYTES:
                    self._reply("431 Request Header Fields Too Large")
                    return
                if header in {b"\r\n", b"\n"}:
                    break
                if not header:
                    return
            target = urlsplit(f"//{fields[1]}")
            host = target.hostname or ""
            port = target.port
            if target.path or target.query or target.fragment or target.username or target.password:
                self._reply("400 Bad Request")
                return
            upstream = _connect_public_santander_host(host, port or 0)
        except (OSError, UnicodeError, ValueError, TimeoutError):
            self._reply("502 Bad Gateway")
            return

        try:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            self.request.setblocking(False)
            self._relay(upstream)
        except OSError:
            return
        finally:
            upstream.close()

    def _relay(self, upstream: socket.socket) -> None:
        client = self.request
        client_to_upstream = bytearray()
        upstream_to_client = bytearray()
        client_eof = False
        upstream_eof = False
        upstream_write_closed = False
        upstream.setblocking(False)
        idle_deadline = time.monotonic() + _IDLE_TIMEOUT_SECONDS
        while not self.server.stop_event.is_set() and time.monotonic() < self.server.deadline:
            remaining_idle = idle_deadline - time.monotonic()
            if remaining_idle <= 0:
                return
            readers = []
            if not client_eof and len(client_to_upstream) < _MAX_PENDING_BYTES_PER_DIRECTION:
                readers.append(client)
            if not upstream_eof and len(upstream_to_client) < _MAX_PENDING_BYTES_PER_DIRECTION:
                readers.append(upstream)
            writers = []
            if client_to_upstream:
                writers.append(upstream)
            if upstream_to_client:
                writers.append(client)
            readable, writable, _ = select.select(
                readers, writers, [], min(1.0, remaining_idle),
            )
            for source in readable:
                pending = client_to_upstream if source is client else upstream_to_client
                capacity = _MAX_PENDING_BYTES_PER_DIRECTION - len(pending)
                data = source.recv(min(64 * 1024, capacity))
                if not data:
                    if source is client:
                        client_eof = True
                    else:
                        upstream_eof = True
                    continue
                if not self.server.account_relayed_bytes(len(data)):
                    return
                pending.extend(data)
                idle_deadline = time.monotonic() + _IDLE_TIMEOUT_SECONDS
            for destination in writable:
                pending = client_to_upstream if destination is upstream else upstream_to_client
                try:
                    sent = destination.send(pending)
                except BlockingIOError:
                    continue
                if sent <= 0:
                    return
                del pending[:sent]
                idle_deadline = time.monotonic() + _IDLE_TIMEOUT_SECONDS
            if client_eof and not client_to_upstream and not upstream_write_closed:
                try:
                    upstream.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                upstream_write_closed = True
            if upstream_eof and not upstream_to_client:
                return
            if client_eof and upstream_eof and not client_to_upstream and not upstream_to_client:
                return


class SantanderConnectProxy:
    """Run a loopback-only, bounded HTTPS CONNECT proxy for one browser session."""

    def __init__(self, *, deadline: float):
        self._deadline = deadline
        self._server: _BoundedThreadingServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> SantanderConnectProxy:
        self._server = _BoundedThreadingServer(
            ("127.0.0.1", 0), _ConnectHandler, self._deadline,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="santander-browser-proxy",
            daemon=True,
        )
        self._thread.start()
        return self

    @property
    def browser_settings(self) -> dict[str, str]:
        if self._server is None:
            raise RuntimeError("proxy is not running")
        host, port = self._server.server_address
        return {"server": f"http://{host}:{port}", "bypass": "<-loopback>"}

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self._server is None:
            return
        self._server.stop_event.set()
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
