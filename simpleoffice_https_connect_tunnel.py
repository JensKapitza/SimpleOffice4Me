#!/usr/bin/env python3
"""Narrow HTTPS-CONNECT client for explicitly allowed Mini Services targets."""
from __future__ import annotations

import argparse
import base64
import socket
import ssl
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from simpleoffice_connection_relay import (
    load_relay_settings,
    normalize_tunnel_target,
    proxy_password,
    split_tunnel_target,
)


_MAX_HEADER = 64 * 1024


def _connect(config_path: Path, target: str, timeout: float = 15.0) -> socket.socket:
    settings = load_relay_settings(config_path)
    normalized = normalize_tunnel_target(target)
    if not settings["https_proxy_enabled"]:
        raise ValueError("HTTPS-CONNECT ist deaktiviert")
    if normalized not in settings["tunnel_targets"]:
        raise ValueError("Ziel ist nicht für den HTTPS-Tunnel freigegeben")

    parsed = urlsplit(settings["https_proxy_url"])
    proxy_host = str(parsed.hostname or "")
    proxy_port = int(parsed.port or 443)
    target_host, target_port = split_tunnel_target(normalized)
    target_authority = f"[{target_host}]:{target_port}" if ":" in target_host else f"{target_host}:{target_port}"

    context = ssl.create_default_context(cafile=settings["https_proxy_ca_file"] or None)
    raw = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        tunnel = context.wrap_socket(raw, server_hostname=proxy_host)
    except Exception:
        raw.close()
        raise

    username = settings["https_proxy_username"]
    password = proxy_password(config_path)
    if not password:
        tunnel.close()
        raise ValueError("Proxy-Passwort ist nicht konfiguriert")
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = (
        f"CONNECT {target_authority} HTTP/1.1\r\n"
        f"Host: {target_authority}\r\n"
        f"Proxy-Authorization: Basic {token}\r\n"
        "Proxy-Connection: Keep-Alive\r\n"
        "User-Agent: SimpleOffice4Me-HTTPS-Tunnel/1\r\n"
        "\r\n"
    ).encode("ascii")
    tunnel.sendall(request)

    response = bytearray()
    while b"\r\n\r\n" not in response:
        chunk = tunnel.recv(4096)
        if not chunk:
            tunnel.close()
            raise ConnectionError("HTTPS-Proxy hat die Verbindung vor der Antwort beendet")
        response.extend(chunk)
        if len(response) > _MAX_HEADER:
            tunnel.close()
            raise ValueError("HTTPS-Proxy-Antwort ist zu groß")
    status_line = bytes(response).split(b"\r\n", 1)[0].decode("latin-1", "replace")
    parts = status_line.split()
    if len(parts) < 2 or parts[1] != "200":
        tunnel.close()
        code = parts[1] if len(parts) > 1 and parts[1].isdigit() else "unbekannt"
        raise ConnectionError(f"HTTPS-CONNECT wurde vom Proxy abgewiesen (Status {code})")
    tunnel.settimeout(None)
    return tunnel


def _stdin_to_socket(sock: socket.socket, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            chunk = sys.stdin.buffer.read(65536)
            if not chunk:
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                return
            sock.sendall(chunk)
    except (BrokenPipeError, OSError):
        pass
    finally:
        stop.set()


def tunnel(config_path: Path, target: str) -> int:
    sock = _connect(config_path, target)
    stop = threading.Event()
    writer = threading.Thread(target=_stdin_to_socket, args=(sock, stop), daemon=True)
    writer.start()
    try:
        while not stop.is_set():
            data = sock.recv(65536)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        return 1
    finally:
        stop.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
        writer.join(timeout=1)
    return 0


def check(config_path: Path, target: str) -> int:
    sock = _connect(config_path, target)
    sock.close()
    print("HTTPS-CONNECT erfolgreich.")
    return 0


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice HTTPS-CONNECT Tunnel für freigegebene Ziele")
    parser.add_argument("--config", required=True, help="Mini-Services-Konfigurationsdatei")
    parser.add_argument("--target", required=True, help="Explizit freigegebenes Ziel Host:Port")
    parser.add_argument("--check", action="store_true", help="Nur CONNECT-Handshake prüfen")
    args = parser.parse_args(argv)
    try:
        code = check(Path(args.config), args.target) if args.check else tunnel(Path(args.config), args.target)
    except (OSError, ValueError, ConnectionError, ssl.SSLError) as exc:
        print(f"Tunnel fehlgeschlagen: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
