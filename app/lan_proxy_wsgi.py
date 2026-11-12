"""Standard-library HTTPS proxy fallback for runtimes without aiohttp."""

from __future__ import annotations

import http.client
import ipaddress
import logging
import re
import socket
import threading
from threading import BoundedSemaphore
from socketserver import ThreadingMixIn
from urllib.parse import quote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server


LOG = logging.getLogger(__name__)
HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
    "forwarded",
    "expect",
}
METHOD_RE = re.compile(r"^[A-Z0-9!#$%&'*+.^_`|~-]{1,32}$")


def _private_client(value: str | None) -> bool:
    try:
        address = ipaddress.ip_address(str(value or "").split("%", 1)[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback or address.is_private or address.is_link_local
    except ValueError:
        return False


def _header_pairs(environ: dict[str, object]) -> list[tuple[str, str]]:
    blocked = set(HOP_HEADERS)
    for value in str(environ.get("HTTP_CONNECTION", "")).split(","):
        if value.strip():
            blocked.add(value.strip().casefold())
    pairs = []
    for key, raw_value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            lowered = name.casefold()
            if (
                lowered in blocked
                or lowered == "host"
                or lowered == "x-real-ip"
                or lowered.startswith("x-forwarded-")
                or lowered == "x-simpleoffice-lan-proxy"
            ):
                continue
            pairs.append((name, str(raw_value)))
    for key, name in (("CONTENT_TYPE", "Content-Type"), ("CONTENT_LENGTH", "Content-Length")):
        value = str(environ.get(key, ""))
        if value:
            pairs.append((name, value))
    return pairs


def _response_headers(headers) -> list[tuple[str, str]]:
    blocked = set(HOP_HEADERS)
    for name, value in headers:
        if name.casefold() == "connection":
            blocked.update(item.strip().casefold() for item in value.split(",") if item.strip())
    result = []
    for name, value in headers:
        lowered = name.casefold()
        if (
            lowered in blocked
            or lowered.startswith("x-forwarded-")
            or lowered in {"x-real-ip", "x-simpleoffice-lan-proxy"}
        ):
            continue
        if lowered == "set-cookie":
            attributes = [item.strip().casefold() for item in value.split(";")[1:]]
            if "secure" not in attributes:
                value += "; Secure"
        result.append((name, value))
    return result


def _text_response(start_response, status: str, message: str):
    body = message.encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [body]


def _request_path(environ: dict[str, object]) -> str:
    target = str(environ.get("RAW_URI", ""))
    if not target:
        path = quote(str(environ.get("PATH_INFO", "/")), safe="/%:@!$&'()*+,;=-._~")
        query = str(environ.get("QUERY_STRING", ""))
        target = path + (f"?{query}" if query else "")
    if not target.startswith("/") or target.startswith("//") or any(ord(ch) < 32 for ch in target):
        raise ValueError("invalid request target")
    return target


def _make_application(
    *,
    backend_host: str,
    backend_port: int,
    public_host: str,
    token: str,
    max_body_bytes: int,
):
    def forward(environ, start_response):
        if not _private_client(environ.get("REMOTE_ADDR")):
            return _text_response(start_response, "403 Forbidden", "LAN access only")
        method = str(environ.get("REQUEST_METHOD", "")).upper()
        if not METHOD_RE.fullmatch(method) or method in {"CONNECT", "TRACE"}:
            return _text_response(start_response, "501 Not Implemented", "HTTP method is not supported")
        if environ.get("HTTP_UPGRADE") or environ.get("HTTP_TRANSFER_ENCODING"):
            return _text_response(
                start_response, "501 Not Implemented", "HTTP upgrades and chunked requests are not supported"
            )
        try:
            target = _request_path(environ)
            raw_length = str(environ.get("CONTENT_LENGTH", "") or "0")
            if not raw_length.isascii() or not raw_length.isdigit():
                raise ValueError("invalid request length")
            body_length = int(raw_length)
        except (TypeError, ValueError):
            return _text_response(start_response, "400 Bad Request", "Invalid proxy request")
        if body_length > max_body_bytes:
            return _text_response(start_response, "413 Request Entity Too Large", "Request body too large")

        connection = http.client.HTTPConnection(backend_host, backend_port, timeout=120)
        try:
            connection.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
            for name, value in _header_pairs(environ):
                connection.putheader(name, value)
            connection.putheader("Host", public_host)
            connection.putheader("X-Forwarded-For", str(environ.get("REMOTE_ADDR", "")))
            connection.putheader("X-Forwarded-Host", public_host)
            connection.putheader("X-Forwarded-Proto", "https")
            connection.putheader("X-SimpleOffice-LAN-Proxy", token)
            connection.putheader("Connection", "close")
            connection.endheaders()
            remaining = body_length
            input_stream = environ["wsgi.input"]
            while remaining:
                chunk = input_stream.read(min(64 * 1024, remaining))
                if not chunk:
                    connection.close()
                    return _text_response(start_response, "400 Bad Request", "Incomplete request body")
                connection.send(chunk)
                remaining -= len(chunk)
            upstream = connection.getresponse()
        except (http.client.HTTPException, OSError, socket.timeout, ValueError) as exc:
            connection.close()
            LOG.warning("LAN proxy upstream failed (%s)", type(exc).__name__)
            return _text_response(start_response, "502 Bad Gateway", "SimpleOffice backend unavailable")

        try:
            headers = _response_headers(upstream.getheaders())
            start_response(f"{upstream.status} {upstream.reason}", headers)
        except (OSError, ValueError):
            upstream.close()
            connection.close()
            raise

        def response_body():
            try:
                if method != "HEAD":
                    while True:
                        chunk = upstream.read(64 * 1024)
                        if not chunk:
                            break
                        yield chunk
            finally:
                upstream.close()
                connection.close()

        return response_body()

    return forward


def start_wsgi_proxy(
    *,
    bind_host: str,
    port: int,
    tls_context,
    backend_host: str,
    backend_port: int,
    public_host: str,
    token: str,
    max_body_bytes: int,
):
    """Start a bounded-thread WSGI listener with stdlib TLS and HTTP clients."""

    class ThreadedServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True
        allow_reuse_address = True
        request_queue_size = 64

        def __init__(self, *args, **kwargs):
            self._request_slots = BoundedSemaphore(64)
            super().__init__(*args, **kwargs)

        def process_request(self, request, client_address):
            if not self._request_slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except Exception:
                self._request_slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self._request_slots.release()

    class QuietHandler(WSGIRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(120)

        def get_environ(self):
            environ = super().get_environ()
            environ["RAW_URI"] = self.path
            return environ

        def log_message(self, format, *args):
            return

    application = _make_application(
        backend_host=backend_host,
        backend_port=backend_port,
        public_host=public_host,
        token=token,
        max_body_bytes=max_body_bytes,
    )
    server = make_server(
        bind_host,
        port,
        application,
        server_class=ThreadedServer,
        handler_class=QuietHandler,
    )
    try:
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    except (OSError, ValueError):
        server.server_close()
        raise
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.25},
        name="simpleoffice-lan-proxy-wsgi",
        daemon=True,
    )
    thread.start()
    return server, thread
