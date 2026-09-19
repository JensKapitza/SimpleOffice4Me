"""Safe media fetch proxy and ffplay-backed renderer playback.

Remote media URLs are never passed to ffplay. A loopback-only proxy connects to
an address pinned by the media URI policy and revalidates every redirect.
"""
from __future__ import annotations

import http.client
import os
import platform
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urljoin

from simpleoffice_media_renderer import resolve_media_uri
from simpleoffice_media_upnp import RendererState

MAX_REDIRECTS = 3
CONNECT_TIMEOUT = 8
READ_CHUNK = 64 * 1024
_RANGE_RE = re.compile(r"^bytes=(?:\d+-\d*|-\d+)$")
_REDIRECTS = {301, 302, 303, 307, 308}
_PASSTHROUGH_HEADERS = {
    "content-type": "Content-Type",
    "content-length": "Content-Length",
    "content-range": "Content-Range",
    "accept-ranges": "Accept-Ranges",
    "etag": "ETag",
    "last-modified": "Last-Modified",
}


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float):
        super().__init__(
            hostname,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_address = address

    def connect(self):
        raw = socket.create_connection(
            (self._pinned_address, self.port), self.timeout
        )
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _host_header(hostname: str, port: int, scheme: str) -> str:
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    default = 443 if scheme == "https" else 80
    return host if port == default else f"{host}:{port}"


class SafeMediaFetcher:
    def __init__(self, *, allow_remote: bool, resolver=resolve_media_uri):
        self.allow_remote = bool(allow_remote)
        self.resolver = resolver

    def _connection(self, target: dict[str, Any]):
        address = target["addresses"][0]
        if target["scheme"] == "https":
            return _PinnedHttpsConnection(
                target["hostname"], address, target["port"], CONNECT_TIMEOUT
            )
        return http.client.HTTPConnection(address, target["port"], timeout=CONNECT_TIMEOUT)

    def open(self, target: dict[str, Any], *, method: str = "GET", range_header: str = ""):
        current = target
        for redirect_count in range(MAX_REDIRECTS + 1):
            connection = self._connection(current)
            headers = {
                "Host": _host_header(current["hostname"], current["port"], current["scheme"]),
                "User-Agent": "SimpleOffice4Me-MediaRenderer/1.0",
                "Accept": "*/*",
                "Connection": "close",
            }
            if range_header:
                if not _RANGE_RE.fullmatch(range_header):
                    connection.close()
                    raise ValueError("Ungültiger Media-Range-Header")
                headers["Range"] = range_header
            connection.request(method, current["path"], headers=headers)
            response = connection.getresponse()
            if response.status not in _REDIRECTS:
                if response.status not in {200, 206}:
                    response.close()
                    connection.close()
                    raise OSError("Media-Server hat den Abruf abgelehnt")
                return connection, response, current

            location = response.getheader("Location", "")
            response.close()
            connection.close()
            if redirect_count >= MAX_REDIRECTS or not location:
                raise ValueError("Zu viele oder ungültige Media-Redirects")
            redirected = urljoin(current["url"], location)
            current = self.resolver(
                redirected,
                allow_remote=self.allow_remote,
            )
        raise ValueError("Zu viele Media-Redirects")


class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, fetcher: SafeMediaFetcher):
        super().__init__(address, _ProxyHandler)
        self.fetcher = fetcher
        self.targets: dict[str, dict[str, Any]] = {}
        self.targets_lock = threading.Lock()

    def register(self, target: dict[str, Any]) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        with self.targets_lock:
            self.targets[token] = dict(target)
            if len(self.targets) > 16:
                self.targets = {token: dict(target)}
        return token, f"http://127.0.0.1:{self.server_address[1]}/media/{token}"

    def unregister(self, token: str) -> None:
        with self.targets_lock:
            self.targets.pop(token, None)

    def target(self, token: str):
        with self.targets_lock:
            value = self.targets.get(token)
            return dict(value) if value else None


class _ProxyHandler(BaseHTTPRequestHandler):
    server_version = "SimpleOfficeMediaProxy/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def _serve(self, *, head_only: bool):
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            self.send_error(403)
            return
        prefix = "/media/"
        if not self.path.startswith(prefix):
            self.send_error(404)
            return
        token = self.path[len(prefix):].split("?", 1)[0]
        if not token or "/" in token or len(token) > 128:
            self.send_error(404)
            return
        target = self.server.target(token)
        if target is None:
            self.send_error(404)
            return
        range_header = self.headers.get("Range", "")
        try:
            connection, response, _ = self.server.fetcher.open(
                target,
                method="HEAD" if head_only else "GET",
                range_header=range_header,
            )
        except (OSError, ValueError, http.client.HTTPException):
            self.send_error(502)
            return
        try:
            self.send_response(response.status)
            for source, destination in _PASSTHROUGH_HEADERS.items():
                value = response.getheader(source)
                if value is not None and len(value) <= 2048:
                    self.send_header(destination, value)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            if head_only:
                return
            while True:
                block = response.read(READ_CHUNK)
                if not block:
                    break
                try:
                    self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    break
        finally:
            response.close()
            connection.close()

    def do_GET(self):
        self._serve(head_only=False)

    def do_HEAD(self):
        self._serve(head_only=True)


class MediaProxy:
    def __init__(self, *, allow_remote: bool, resolver=resolve_media_uri):
        self.server = _ProxyServer(
            ("127.0.0.1", 0),
            SafeMediaFetcher(allow_remote=allow_remote, resolver=resolver),
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="media-safe-proxy",
            daemon=True,
        )
        self.thread.start()

    def register(self, target: dict[str, Any]) -> tuple[str, str]:
        return self.server.register(target)

    def unregister(self, token: str) -> None:
        self.server.unregister(token)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=2)


class RendererPlayback:
    def __init__(self, state: RendererState, settings: dict[str, Any]):
        self.state = state
        self.settings = settings
        self.proxy = MediaProxy(
            allow_remote=bool(settings.get("allow_remote_media")),
            resolver=state.uri_resolver,
        )
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.monitor: threading.Thread | None = None
        self.token = ""
        self.generation = 0
        self.closed = False
        self.last_error = ""

    def _player(self) -> str:
        player = shutil.which("ffplay")
        if not player:
            raise RuntimeError("FFplay ist nicht installiert")
        return player

    def _command(self, proxy_url: str, position: float, volume: int, muted: bool) -> list[str]:
        command = [
            self._player(),
            "-autoexit",
            "-loglevel",
            "error",
            "-hwaccel",
            "auto",
            "-volume",
            str(0 if muted else volume),
        ]
        if self.settings.get("video_mode") == "fullscreen":
            command.append("-fs")
        if position > 0:
            command.extend(["-ss", f"{position:.3f}"])
        command.append(proxy_url)
        return command

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        device = str(self.settings.get("audio_output") or "default")
        if device != "default":
            if platform.system().lower() == "windows":
                raise RuntimeError("Gezielte Windows-Audioausgabe ist für DLNA noch nicht verfügbar")
            environment["PULSE_SINK"] = device
        return environment

    def _terminate_locked(self) -> None:
        process = self.process
        self.process = None
        self.generation += 1
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self.token:
            self.proxy.unregister(self.token)
            self.token = ""

    def _start_locked(self) -> None:
        snapshot = self.state.snapshot()
        target = self.state.uri_target
        if snapshot.state != "PLAYING" or target is None:
            return
        self._terminate_locked()
        token, proxy_url = self.proxy.register(target)
        self.token = token
        command = self._command(
            proxy_url,
            snapshot.position_seconds,
            snapshot.volume,
            snapshot.muted,
        )
        generation = self.generation
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._environment(),
            )
        except Exception:
            self.proxy.unregister(token)
            self.token = ""
            raise
        self.process = process
        self.monitor = threading.Thread(
            target=self._monitor_process,
            args=(process, generation),
            name="media-playback-monitor",
            daemon=True,
        )
        self.monitor.start()

    def _monitor_process(self, process: subprocess.Popen, generation: int) -> None:
        return_code = process.wait()
        with self.lock:
            if (
                self.closed
                or generation != self.generation
                or self.process is not process
            ):
                return
            self.process = None
            if self.token:
                self.proxy.unregister(self.token)
                self.token = ""
            if return_code != 0:
                self.last_error = "player_failed"
                self.state.stop()
                return
            self.state.media_finished()
            if self.state.snapshot().state == "PLAYING":
                try:
                    self._start_locked()
                except (OSError, RuntimeError, ValueError):
                    self.last_error = "next_playback_failed"
                    self.state.stop()

    def sync(self, action: str) -> None:
        with self.lock:
            if self.closed:
                raise RuntimeError("Media-Wiedergabe ist beendet")
            try:
                if action in {"Play", "Seek", "SetVolume", "SetMute"}:
                    if self.state.snapshot().state == "PLAYING":
                        self._start_locked()
                elif action in {"Pause", "Stop", "SetAVTransportURI"}:
                    self._terminate_locked()
                elif action == "SetNextAVTransportURI":
                    return
                self.last_error = ""
            except (OSError, RuntimeError, ValueError):
                self.last_error = "playback_failed"
                raise

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "available": shutil.which("ffplay") is not None,
                "running": bool(self.process is not None and self.process.poll() is None),
                "last_error": self.last_error,
                "player": "ffplay",
            }

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self._terminate_locked()
        self.proxy.close()
