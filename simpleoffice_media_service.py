"""Bounded local HTTP/SSDP service for the SimpleOffice UPnP MediaRenderer."""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from simpleoffice_media_upnp import (
    RendererState,
    SERVICE_PATHS,
    UpnpActionError,
    device_description,
    dispatch_action,
    parse_soap_action,
    service_description,
    soap_fault,
    soap_response,
)

SSDP_GROUP = "239.255.255.250"
SSDP_PORT = 1900
MAX_HTTP_BODY = 64 * 1024
MAX_SSDP_PACKET = 2048
SSDP_MAX_AGE = 1800


def _private_controller(address: str) -> bool:
    try:
        value = ipaddress.ip_address(address)
    except ValueError:
        return False
    return value.is_loopback or (
        value.is_private
        and not value.is_link_local
        and not value.is_multicast
        and not value.is_unspecified
        and not value.is_reserved
    )


def _service_from_control_path(path: str) -> str | None:
    for key, (_, control_path, _) in SERVICE_PATHS.items():
        if path == control_path:
            return key
    return None


class MediaRendererHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, settings: dict[str, Any], state: RendererState, playback=None):
        super().__init__(address, MediaRendererHandler)
        self.settings = settings
        self.renderer_state = state
        self.playback = playback


class MediaRendererHandler(BaseHTTPRequestHandler):
    server_version = "SimpleOfficeMediaRenderer/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def _allowed(self) -> bool:
        return _private_controller(str(self.client_address[0]))

    def _send(self, status: int, body: bytes = b"", content_type: str = "text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def do_GET(self):
        if not self._allowed():
            self._send(403)
            return
        path = urlsplit(self.path).path
        if path == "/upnp/device.xml":
            host = self.server.settings["bind"]
            port = self.server.server_address[1]
            body = device_description(self.server.settings, f"http://{host}:{port}")
            self._send(200, body, "text/xml; charset=utf-8")
            return
        for key, (_, _, scpd) in SERVICE_PATHS.items():
            if path == scpd:
                self._send(200, service_description(key), "text/xml; charset=utf-8")
                return
        self._send(404)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if not self._allowed():
            self._send(403)
            return
        path = urlsplit(self.path).path
        service = _service_from_control_path(path)
        if service is None:
            self._send(404)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send(400)
            return
        if not 0 <= length <= MAX_HTTP_BODY:
            self._send(413)
            return
        body = self.rfile.read(length)
        try:
            action, arguments = parse_soap_action(
                service, self.headers.get("SOAPAction", ""), body
            )
            values = dispatch_action(
                self.server.renderer_state, service, action, arguments
            )
            if self.server.playback is not None:
                self.server.playback.sync(action)
            payload = soap_response(service, action, values)
            self._send(200, payload, 'text/xml; charset="utf-8"')
        except UpnpActionError as exc:
            self._send(500, soap_fault(exc), 'text/xml; charset="utf-8"')
        except (ValueError, OSError):
            self._send(
                500,
                soap_fault(UpnpActionError(501, "Action Failed")),
                'text/xml; charset="utf-8"',
            )

    def do_SUBSCRIBE(self):
        # Eventing will be added only with a bounded subscriber store; never
        # accept arbitrary callback URLs by default.
        self._send(501)

    def do_UNSUBSCRIBE(self):
        self._send(501)


class SsdpAdvertiser:
    def __init__(self, settings: dict[str, Any], stop_event: threading.Event):
        self.settings = settings
        self.stop_event = stop_event
        self.socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.last_reply: dict[str, float] = {}

    @property
    def bind(self) -> str:
        return str(self.settings["bind"])

    @property
    def location(self) -> str:
        return f"http://{self.bind}:{int(self.settings['port'])}/upnp/device.xml"

    @property
    def udn(self) -> str:
        return "uuid:" + str(self.settings["udn"])

    def start(self) -> None:
        if ipaddress.ip_address(self.bind).is_loopback:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.bind))
            membership = socket.inet_aton(SSDP_GROUP) + socket.inet_aton(self.bind)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.bind(("", SSDP_PORT))
            sock.settimeout(0.5)
        except Exception:
            sock.close()
            raise
        self.socket = sock
        self._notify("ssdp:alive")
        self.thread = threading.Thread(target=self._loop, name="media-ssdp", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.socket is not None:
            try:
                self._notify("ssdp:byebye")
            except OSError:
                pass
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        self.thread = None

    def _targets(self) -> tuple[tuple[str, str], ...]:
        rows = [
            ("upnp:rootdevice", self.udn + "::upnp:rootdevice"),
            (self.udn, self.udn),
            (
                "urn:schemas-upnp-org:device:MediaRenderer:1",
                self.udn + "::urn:schemas-upnp-org:device:MediaRenderer:1",
            ),
        ]
        for service_type, _, _ in SERVICE_PATHS.values():
            rows.append((service_type, self.udn + "::" + service_type))
        return tuple(rows)

    def _notify(self, nts: str) -> None:
        if self.socket is None:
            return
        for nt, usn in self._targets():
            lines = [
                "NOTIFY * HTTP/1.1",
                f"HOST: {SSDP_GROUP}:{SSDP_PORT}",
                f"NT: {nt}",
                f"NTS: {nts}",
                f"USN: {usn}",
            ]
            if nts == "ssdp:alive":
                lines.extend(
                    [
                        f"CACHE-CONTROL: max-age={SSDP_MAX_AGE}",
                        f"LOCATION: {self.location}",
                        "SERVER: SimpleOffice4Me/1.0 UPnP/1.0",
                    ]
                )
            payload = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
            self.socket.sendto(payload, (SSDP_GROUP, SSDP_PORT))

    def _loop(self) -> None:
        next_alive = time.monotonic() + SSDP_MAX_AGE / 2
        while not self.stop_event.is_set() and self.socket is not None:
            if time.monotonic() >= next_alive:
                try:
                    self._notify("ssdp:alive")
                except OSError:
                    break
                next_alive = time.monotonic() + SSDP_MAX_AGE / 2
            try:
                payload, peer = self.socket.recvfrom(MAX_SSDP_PACKET)
            except socket.timeout:
                continue
            except OSError:
                break
            self._handle_search(payload, peer)

    def _handle_search(self, payload: bytes, peer) -> None:
        address = str(peer[0])
        if not _private_controller(address) or len(payload) >= MAX_SSDP_PACKET:
            return
        try:
            text = payload.decode("ascii", "strict")
        except UnicodeDecodeError:
            return
        lines = text.replace("\r\n", "\n").split("\n")
        if not lines or lines[0].strip().upper() != "M-SEARCH * HTTP/1.1":
            return
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        if headers.get("man", "").strip('"').lower() != "ssdp:discover":
            return
        requested = headers.get("st", "")
        candidates = self._targets()
        if requested == "ssdp:all":
            selected = candidates
        else:
            selected = tuple(row for row in candidates if row[0] == requested)
        if not selected:
            return
        now = time.monotonic()
        if now - self.last_reply.get(address, 0) < 0.5:
            return
        self.last_reply[address] = now
        if len(self.last_reply) > 256:
            self.last_reply = {address: now}
        for st, usn in selected:
            self._reply(peer, st, usn)

    def _reply(self, peer, st: str, usn: str) -> None:
        if self.socket is None:
            return
        payload = (
            "HTTP/1.1 200 OK\r\n"
            f"CACHE-CONTROL: max-age={SSDP_MAX_AGE}\r\n"
            "EXT:\r\n"
            f"LOCATION: {self.location}\r\n"
            "SERVER: SimpleOffice4Me/1.0 UPnP/1.0\r\n"
            f"ST: {st}\r\n"
            f"USN: {usn}\r\n\r\n"
        ).encode("ascii")
        self.socket.sendto(payload, peer)


class MediaRendererService:
    def __init__(self, settings: dict[str, Any], config_path=None, event=None, state=None, playback=None):
        self.settings = dict(settings)
        self.config_path = config_path
        self.event = event or (lambda row: None)
        self.state = state or RendererState(
            allow_remote_media=bool(self.settings.get("allow_remote_media"))
        )
        if playback is None:
            from simpleoffice_media_playback import RendererPlayback
            playback = RendererPlayback(self.state, self.settings)
        self.playback = playback
        self.stop_event = threading.Event()
        self.httpd: MediaRendererHttpServer | None = None
        self.http_thread: threading.Thread | None = None
        self.socket = None
        self.thread = None
        self.ssdp = SsdpAdvertiser(self.settings, self.stop_event)

    def start(self) -> None:
        if self.httpd is not None:
            return
        self.stop_event.clear()
        httpd = MediaRendererHttpServer(
            (self.settings["bind"], int(self.settings["port"])),
            self.settings,
            self.state,
            self.playback,
        )
        self.httpd = httpd
        try:
            self.ssdp.start()
            self.http_thread = threading.Thread(
                target=httpd.serve_forever,
                kwargs={"poll_interval": 0.25},
                name="media-upnp-http",
                daemon=True,
            )
            self.http_thread.start()
            self.socket = httpd.socket
            self.thread = self.http_thread
        except Exception:
            httpd.server_close()
            self.httpd = None
            self.ssdp.stop()
            raise

    def stop(self) -> None:
        self.stop_event.set()
        self.ssdp.stop()
        httpd = self.httpd
        self.httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if self.http_thread is not None and self.http_thread is not threading.current_thread():
            self.http_thread.join(timeout=2)
        self.http_thread = None
        self.socket = None
        self.thread = None
        self.playback.close()

    def is_alive(self) -> bool:
        return bool(
            self.httpd is not None
            and self.http_thread is not None
            and self.http_thread.is_alive()
        )
