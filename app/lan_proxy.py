"""Optional, authenticated HTTPS entry point for the local SimpleOffice server."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import os
import re
import secrets
import stat
import ssl
import threading
from pathlib import Path
from typing import Any

from werkzeug.utils import safe_join

from .lan_proxy_config import (
    container_proxy_mode,
    lan_proxy_tls_directory,
    validate_lan_proxy_tls_directory,
    validate_lan_proxy_settings,
)


LOG = logging.getLogger(__name__)
PROXY_TOKEN_HEADER = "X-SimpleOffice-LAN-Proxy"
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
}


class _RequestBodyTooLarge(Exception):
    """Raised while streaming a chunked request after crossing the size limit."""


def _client_is_private(value: str | None) -> bool:
    try:
        address = ipaddress.ip_address(str(value or "").split("%", 1)[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return bool(address.is_loopback or address.is_private or address.is_link_local)
    except ValueError:
        return False


def _clean_headers(headers) -> list[tuple[str, str]]:
    blocked = set(HOP_HEADERS)
    for value in headers.getall("Connection", []):
        blocked.update(item.strip().casefold() for item in value.split(",") if item.strip())
    result = []
    for name, value in headers.items():
        lowered = name.casefold()
        if (
            lowered in blocked
            or lowered.startswith("x-forwarded-")
            or lowered == "x-real-ip"
            or lowered == PROXY_TOKEN_HEADER.casefold()
        ):
            continue
        result.append((name, value))
    return result


def _secure_response_cookies(headers) -> list[tuple[str, str]]:
    """Ensure cookies returned through this HTTPS-only listener are Secure."""
    result = []
    for name, value in headers:
        if name.casefold() == "set-cookie":
            attributes = [item.strip().casefold() for item in value.split(";")[1:]]
            if "secure" not in attributes:
                value += "; Secure"
        result.append((name, value))
    return result


def _tls_file_path(directory: Path, filename: str) -> Path:
    joined = safe_join(os.fspath(directory), filename)
    if joined is None:
        raise ValueError("TLS-Dateiname liegt außerhalb des freigegebenen TLS-Ordners.")
    trusted_directory = directory.resolve(strict=True)
    path = Path(joined).resolve(strict=True)
    if not path.is_relative_to(trusted_directory):
        raise ValueError("TLS-Datei muss im freigegebenen TLS-Ordner liegen.")
    return path


def _tls_context(
    certificate: str,
    private_key: str,
    document_root: str | Path | None,
    tls_directory: Path | None = None,
) -> ssl.SSLContext:
    tls_directory = validate_lan_proxy_tls_directory(tls_directory or lan_proxy_tls_directory())
    cert_path = _tls_file_path(tls_directory, certificate)
    key_path = _tls_file_path(tls_directory, private_key)
    if not cert_path.is_file() or not key_path.is_file() or cert_path == key_path:
        raise ValueError("TLS-Zertifikat und Privatschlüssel müssen lesbare Dateien sein.")
    if document_root:
        root = Path(document_root).expanduser().resolve()
        if root == tls_directory or root in tls_directory.parents:
            raise ValueError("Der TLS-Ordner darf nicht im Dokumentordner liegen.")
        if root == key_path or root in key_path.parents:
            raise ValueError("Der TLS-Privatschlüssel darf nicht im Dokumentordner liegen.")
    if os.name == "posix" and stat.S_IMODE(key_path.stat().st_mode) & 0o077:
        raise ValueError("Der TLS-Privatschlüssel ist für Gruppe oder andere Benutzer zu weit freigegeben.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert_path), str(key_path))
    return context


async def _proxy_application(
    *,
    backend_host: str,
    backend_port: int,
    public_host: str,
    token: str,
    max_body_bytes: int,
    client_session,
):
    import aiohttp
    from aiohttp import web
    from multidict import CIMultiDict

    async def forward(request):
        if not _client_is_private(request.remote):
            raise web.HTTPForbidden(text="LAN access only")
        if request.method in {"CONNECT", "TRACE"} or request.headers.get("Upgrade"):
            raise web.HTTPNotImplemented(text="HTTP upgrades and tunnels are not supported")
        if request.content_length is not None and request.content_length > max_body_bytes:
            raise web.HTTPRequestEntityTooLarge(max_size=max_body_bytes, actual_size=request.content_length)
        raw_path = request.rel_url.raw_path_qs
        if not raw_path.startswith("/") or raw_path.startswith("//"):
            raise web.HTTPBadRequest(text="Invalid proxy request target")

        outgoing = CIMultiDict(_clean_headers(request.headers))
        outgoing["Host"] = public_host
        outgoing["X-Forwarded-For"] = request.remote or ""
        outgoing["X-Forwarded-Host"] = public_host
        outgoing["X-Forwarded-Proto"] = "https"
        outgoing[PROXY_TOKEN_HEADER] = token
        upstream_url = f"http://{backend_host}:{backend_port}{raw_path}"
        body_too_large = False

        async def limited_body():
            nonlocal body_too_large
            total = 0
            async for chunk in request.content.iter_chunked(64 * 1024):
                total += len(chunk)
                if total > max_body_bytes:
                    body_too_large = True
                    raise _RequestBodyTooLarge
                yield chunk

        try:
            upstream_context = client_session.request(
                request.method,
                upstream_url,
                headers=outgoing,
                data=limited_body(),
                allow_redirects=False,
            )
            upstream = await upstream_context
        except web.HTTPRequestEntityTooLarge:
            return web.Response(status=413, text="Request body too large", content_type="text/plain")
        except _RequestBodyTooLarge:
            return web.Response(status=413, text="Request body too large", content_type="text/plain")
        except asyncio.TimeoutError:
            return web.Response(status=504, text="SimpleOffice backend timeout", content_type="text/plain")
        except aiohttp.ClientError as exc:
            if body_too_large:
                return web.Response(status=413, text="Request body too large", content_type="text/plain")
            LOG.warning("LAN proxy upstream failed (%s)", type(exc).__name__)
            return web.Response(status=502, text="SimpleOffice backend unavailable", content_type="text/plain")

        async with upstream:
            response_headers = CIMultiDict(_secure_response_cookies(_clean_headers(upstream.headers)))
            try:
                response = web.StreamResponse(
                    status=upstream.status,
                    reason=upstream.reason,
                    headers=response_headers,
                )
                await response.prepare(request)
                if request.method != "HEAD":
                    async for chunk in upstream.content.iter_chunked(64 * 1024):
                        await response.write(chunk)
                await response.write_eof()
                return response
            except (ConnectionResetError, asyncio.CancelledError):
                raise

    proxy_app = web.Application(client_max_size=max_body_bytes)
    proxy_app.router.add_route("*", "/{path:.*}", forward)
    return proxy_app


class LanProxyRuntime:
    """Own one HTTPS listener in a managed asyncio thread of the app process."""

    def __init__(self):
        self._operation_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._loop = None
        self._thread = None
        self._runner = None
        self._session = None
        self._server = None
        self._app = None
        self._profile = None
        self._status = {"state": "unknown", "message": "No LAN proxy status available", "origin": ""}

    def status(self) -> dict[str, str]:
        with self._state_lock:
            result = dict(self._status)
            if result["state"] == "running" and (self._thread is None or not self._thread.is_alive()):
                result.update(state="error", message="LAN proxy process stopped unexpectedly", origin="")
            return result

    def _set_status(self, state: str, message: str, origin: str = "") -> dict[str, str]:
        with self._state_lock:
            self._status = {"state": state, "message": message[:240], "origin": origin[:255]}
            return dict(self._status)

    def start(
        self,
        flask_app,
        settings: dict[str, Any],
        *,
        backend_host: str,
        backend_port: int,
        document_root: str | Path,
    ) -> dict[str, str]:
        with self._operation_lock:
            try:
                config = validate_lan_proxy_settings(settings)
                if not config["enabled"]:
                    self.stop(state="disabled", message="LAN proxy is disabled")
                    return self.status()
                backend_address = ipaddress.ip_address(backend_host)
                if not backend_address.is_loopback or not 1 <= int(backend_port) <= 65535:
                    raise ValueError("LAN proxy backend must be a fixed loopback endpoint.")
                if ipaddress.ip_address(config["bind_host"]).is_unspecified and not container_proxy_mode():
                    raise ValueError("Wildcard LAN proxy binding is only allowed inside the Docker network namespace.")
                tls_directory = lan_proxy_tls_directory()
                tls = _tls_context(config["tls_cert"], config["tls_key"], document_root, tls_directory)
                token = secrets.token_urlsafe(32)
                profile = (config, str(backend_address), int(backend_port), str(document_root), str(tls_directory))
                if self._profile == profile and self.status()["state"] == "running":
                    return self.status()
                self.stop(state="stopped", message="LAN proxy configuration changed")
                max_body_bytes = max(1, int(flask_app.config.get("MAX_CONTENT_LENGTH") or 16 * 1024 * 1024))
                return self._start_thread(flask_app, config, tls, token, profile, max_body_bytes)
            except Exception as exc:
                code = getattr(exc, "errno", None)
                if code == 98 or code == 48 or code == 10048:
                    message = "LAN proxy port is already in use"
                elif code in {99, 49, 10049}:
                    message = "LAN proxy bind address is not available on this host"
                else:
                    message = f"LAN proxy could not start ({type(exc).__name__})"
                LOG.warning("LAN proxy startup failed (%s)", type(exc).__name__)
                if self._loop is not None:
                    self.stop(state="error", message=message)
                self._set_status("error", message)
                return self.status()

    def _start_thread(self, flask_app, config, tls, token, profile, max_body_bytes):
        try:
            import aiohttp
        except ImportError:
            return self._start_wsgi_thread(
                flask_app,
                config,
                tls,
                token,
                profile,
                max_body_bytes,
            )
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        outcome: dict[str, Any] = {}

        def run_loop():
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

        thread = threading.Thread(target=run_loop, name="simpleoffice-lan-proxy", daemon=True)
        flask_app.config["LAN_PROXY_TOKEN"] = token
        thread.start()
        ready.wait(timeout=5)
        with self._state_lock:
            self._loop = loop
            self._thread = thread
            self._app = flask_app

        async def bind():
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=120),
                auto_decompress=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                connector=aiohttp.TCPConnector(limit=100),
            )
            proxy_app = await _proxy_application(
                backend_host=profile[1],
                backend_port=profile[2],
                public_host=self._public_host(config),
                token=token,
                max_body_bytes=max_body_bytes,
                client_session=session,
            )
            runner = aiohttp.web.AppRunner(proxy_app, access_log=None, shutdown_timeout=5)
            try:
                await runner.setup()
                site = aiohttp.web.TCPSite(
                    runner,
                    host=config["bind_host"],
                    port=config["port"],
                    ssl_context=tls,
                )
                await site.start()
            except Exception:
                await runner.cleanup()
                await session.close()
                raise
            outcome.update(session=session, runner=runner)

        try:
            asyncio.run_coroutine_threadsafe(bind(), loop).result(timeout=15)
        except Exception:
            flask_app.config["LAN_PROXY_TOKEN"] = ""
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            with self._state_lock:
                self._loop = None
                self._thread = None
                self._app = None
            raise
        with self._state_lock:
            self._session = outcome["session"]
            self._runner = outcome["runner"]
            self._profile = profile
            flask_app.config["LAN_PROXY_TOKEN"] = token
        origin = f"https://{self._public_host(config)}"
        return self._set_status("running", f"HTTPS proxy listening on {config['bind_host']}:{config['port']}", origin)

    def _start_wsgi_thread(self, flask_app, config, tls, token, profile, max_body_bytes):
        from .lan_proxy_wsgi import start_wsgi_proxy

        flask_app.config["LAN_PROXY_TOKEN"] = token
        try:
            server, thread = start_wsgi_proxy(
                bind_host=config["bind_host"],
                port=config["port"],
                tls_context=tls,
                backend_host=profile[1],
                backend_port=profile[2],
                public_host=self._public_host(config),
                token=token,
                max_body_bytes=max_body_bytes,
            )
        except Exception:
            flask_app.config["LAN_PROXY_TOKEN"] = ""
            raise
        with self._state_lock:
            self._server = server
            self._thread = thread
            self._app = flask_app
            self._profile = profile
        origin = f"https://{self._public_host(config)}"
        return self._set_status(
            "running",
            f"HTTPS proxy listening on {config['bind_host']}:{config['port']}",
            origin,
        )

    @staticmethod
    def _public_host(config: dict[str, Any]) -> str:
        host = config["bind_host"]
        public_port = int(config["port"])
        if ipaddress.ip_address(host).is_unspecified:
            advertised = os.environ.get("SIMPLEOFFICE_LAN_PROXY_PUBLIC_HOST", "").strip()
            if not advertised:
                raise ValueError("Docker LAN proxy requires SIMPLEOFFICE_LAN_PROXY_PUBLIC_HOST")
            try:
                public_port = int(os.environ.get("SIMPLEOFFICE_LAN_PROXY_PUBLIC_PORT", str(public_port)))
            except ValueError as exc:
                raise ValueError("Docker LAN proxy public port must be numeric") from exc
            if not 1 <= public_port <= 65535:
                raise ValueError("Docker LAN proxy public port is outside the TCP port range")
            try:
                address = ipaddress.ip_address(advertised)
            except ValueError:
                if not re.fullmatch(
                    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*",
                    advertised,
                ):
                    raise ValueError("Docker LAN proxy public host must be an IP address or DNS name")
                host = advertised.lower()
            else:
                if address.is_unspecified or address.is_multicast or not (address.is_private or address.is_loopback):
                    raise ValueError("Docker LAN proxy public IP must be private")
                host = str(address)
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{public_port}"

    def stop(self, *, state: str = "stopped", message: str = "LAN proxy stopped") -> dict[str, str]:
        with self._operation_lock:
            with self._state_lock:
                loop, thread, runner, session, flask_app = (
                    self._loop,
                    self._thread,
                    self._runner,
                    self._session,
                    self._app,
                )
                server = self._server
                self._loop = self._thread = self._runner = self._session = self._app = None
                self._server = None
                self._profile = None
                if flask_app is not None:
                    flask_app.config["LAN_PROXY_TOKEN"] = ""
            if loop and loop.is_running():

                async def cleanup():
                    if runner is not None:
                        await runner.cleanup()
                    if session is not None and not session.closed:
                        await session.close()

                try:
                    asyncio.run_coroutine_threadsafe(cleanup(), loop).result(timeout=10)
                except Exception as exc:
                    LOG.warning("LAN proxy cleanup failed (%s)", type(exc).__name__)
                loop.call_soon_threadsafe(loop.stop)
            if server is not None:
                try:
                    server.shutdown()
                except OSError as exc:
                    LOG.warning("LAN proxy WSGI shutdown failed (%s)", type(exc).__name__)
                server.server_close()
            if thread is not None:
                thread.join(timeout=10)
            return self._set_status(state, message)


lan_proxy_runtime = LanProxyRuntime()


class LanProxyHeadersMiddleware:
    """Trust forwarded origin data only from the token-authenticated loopback proxy."""

    def __init__(self, flask_app, application):
        self.flask_app = flask_app
        self.application = application
        self._startup_lock = threading.Lock()
        self._startup_checked = False

    def _ensure_started(self, environ):
        with self._startup_lock:
            if self._startup_checked:
                return
            self._startup_checked = True
        try:
            document_root = self.flask_app.config.get("DOCUMENT_ROOT")
            if not document_root:
                return
            from .settings_store import SettingsStore

            settings = SettingsStore(document_root).settings()["lan_proxy"]
            if not settings["enabled"]:
                return
            configured_port = self.flask_app.config.get("LAN_PROXY_BACKEND_PORT")
            backend_port = int(configured_port or environ.get("SERVER_PORT", ""))
            if not 1 <= backend_port <= 65535:
                raise ValueError("The WSGI server did not provide a valid backend port")
            self.flask_app.config["LAN_PROXY_BACKEND_HOST"] = "127.0.0.1"
            self.flask_app.config["LAN_PROXY_BACKEND_PORT"] = backend_port
            result = lan_proxy_runtime.start(
                self.flask_app,
                settings,
                backend_host="127.0.0.1",
                backend_port=backend_port,
                document_root=document_root,
            )
            if result["state"] == "error":
                LOG.warning("LAN proxy auto-start failed (%s)", result["message"])
        except (OSError, TypeError, ValueError, KeyError, RuntimeError) as exc:
            LOG.warning("LAN proxy auto-start failed (%s)", type(exc).__name__)

    def __call__(self, environ, start_response):
        self._ensure_started(environ)
        provided = str(environ.get("HTTP_X_SIMPLEOFFICE_LAN_PROXY", ""))
        expected = str(self.flask_app.config.get("LAN_PROXY_TOKEN", ""))
        peer = str(environ.get("REMOTE_ADDR", ""))
        forwarded_for = str(environ.get("HTTP_X_FORWARDED_FOR", "")).strip()
        forwarded_host = str(environ.get("HTTP_X_FORWARDED_HOST", "")).strip()
        if (
            expected
            and provided
            and _client_is_loopback(peer)
            and hmac.compare_digest(provided, expected)
            and _client_is_private(forwarded_for)
            and forwarded_host
            and len(forwarded_host) <= 255
            and not any(character.isspace() or ord(character) < 32 for character in forwarded_host)
        ):
            environ["wsgi.url_scheme"] = "https"
            environ["HTTP_HOST"] = forwarded_host
            environ["REMOTE_ADDR"] = forwarded_for
            for name in list(environ):
                if (
                    name.upper().startswith("HTTP_X_FORWARDED_")
                    or name.upper().startswith("HTTP_X_FORWARDED-")
                    or name in {"HTTP_FORWARDED", "HTTP_X_SIMPLEOFFICE_LAN_PROXY"}
                ):
                    environ.pop(name, None)
        else:
            environ.pop("HTTP_X_SIMPLEOFFICE_LAN_PROXY", None)
        return self.application(environ, start_response)


def _client_is_loopback(value: str) -> bool:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback
    except ValueError:
        return False


def configure_lan_proxy(flask_app, document_root: str | Path, backend_host: str, backend_port: int):
    from .settings_store import SettingsStore

    settings = SettingsStore(document_root).settings()["lan_proxy"]
    return lan_proxy_runtime.start(
        flask_app,
        settings,
        backend_host=backend_host,
        backend_port=backend_port,
        document_root=document_root,
    )
