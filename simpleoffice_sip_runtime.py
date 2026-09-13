"""Small local-only SIP registrar/redirect service for SimpleOffice Mini Services.

The service deliberately does not provide an Internet SIP proxy or PSTN trunk.
It authenticates configured local extensions, keeps short-lived registrations in
memory and redirects authenticated local INVITEs to the registered LAN endpoint.
RTP/audio/video then flows directly between the SIP user agents.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

_MAX_PACKET = 64 * 1024
_MAX_HEADERS = 100
_MAX_LINE = 4096
_MAX_REGISTRATION = 3600
_NONCE_TTL = 300
_DIGEST_PART = re.compile(r"\s*([A-Za-z0-9_-]+)\s*=\s*(?:\"([^\"]*)\"|([^,\s]+))\s*(?:,|$)")
_SIP_URI = re.compile(r"^sip:([0-9]{1,12})@([^;>]+)(?:;.*)?$", re.I)
_HEADER_USER = re.compile(r"(?:<\s*)?sip:([0-9]{1,12})@", re.I)
_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def _md5_hex(value: str) -> str:
    data = value.encode("utf-8")
    try:
        return hashlib.md5(data, usedforsecurity=False).hexdigest()
    except TypeError:  # pragma: no cover
        return hashlib.md5(data).hexdigest()  # nosec B324 - SIP Digest compatibility


def _is_local_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    return address.version == 4 and any(address in network for network in _RFC1918)


def auto_sip_bind_host() -> str:
    """Select one private IPv4 address without ever falling back to 0.0.0.0."""
    configured = os.environ.get("SIMPLEOFFICE_SIP_BIND", "").strip()
    if configured:
        if not _is_local_address(configured):
            raise ValueError("SIMPLEOFFICE_SIP_BIND muss Loopback oder eine private IPv4-Adresse sein")
        return configured

    found: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").split("%", 1)[0]
        if _is_local_address(value) and not ipaddress.ip_address(value).is_loopback and value not in found:
            found.append(value)

    try:
        for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM):
            add(row[4][0])
    except socket.gaierror:
        pass
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    return found[0] if found else "127.0.0.1"


def telephony_db_path(config_path: str | Path) -> Path:
    return Path(config_path).parent / "telephony" / "telephony-profiles.sqlite3"


def _settings(db_path: Path) -> dict[str, str]:
    values = {
        "registrar_host": "",
        "registrar_port": "5060",
        "transport": "udp",
        "realm": "simpleoffice.local",
        "stun_server": "",
    }
    if not db_path.is_file():
        return values
    try:
        with sqlite3.connect(db_path, timeout=5) as db:
            rows = db.execute("SELECT key,value FROM telephony_setting").fetchall()
    except sqlite3.Error:
        return values
    values.update({str(key): str(value) for key, value in rows})
    return values


def effective_sip_settings(config_path: str | Path) -> dict[str, Any]:
    settings = _settings(telephony_db_path(config_path))
    bind_host = auto_sip_bind_host()
    configured_host = str(settings.get("registrar_host") or "").strip()
    try:
        port = int(settings.get("registrar_port") or 5060)
    except ValueError:
        port = 5060
    if not 1 <= port <= 65535:
        port = 5060
    return {
        **settings,
        "bind_host": bind_host,
        "advertised_host": configured_host or bind_host,
        "registrar_port": port,
        "transport": "udp",
    }


def _parse_digest(value: str) -> dict[str, str]:
    text = str(value or "").strip()
    if text.lower().startswith("digest "):
        text = text[7:]
    result: dict[str, str] = {}
    position = 0
    while position < len(text):
        match = _DIGEST_PART.match(text, position)
        if not match:
            raise ValueError("Ungültiger SIP-Digest")
        result[match.group(1).casefold()] = match.group(2) if match.group(2) is not None else match.group(3)
        position = match.end()
    return result


def _split_packet(data: bytes) -> tuple[str, str, dict[str, list[str]]]:
    if not data or len(data) > _MAX_PACKET or b"\0" in data:
        raise ValueError("Ungültiges SIP-Paket")
    text = data.decode("latin-1")
    head = text.split("\r\n\r\n", 1)[0]
    lines = head.split("\r\n")
    if not lines or len(lines) > _MAX_HEADERS + 1 or any(len(line) > _MAX_LINE for line in lines):
        raise ValueError("Ungültige SIP-Header")
    start = lines[0].split()
    if len(start) != 3 or start[2].upper() != "SIP/2.0":
        raise ValueError("Ungültige SIP-Startzeile")
    method, uri = start[0].upper(), start[1]
    if not uri.lower().startswith("sip:") or any(ch in uri for ch in ("\r", "\n")):
        raise ValueError("Ungültige SIP-URI")
    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        if not line or line[0].isspace() or ":" not in line:
            raise ValueError("Ungültiger SIP-Header")
        name, value = line.split(":", 1)
        key = name.strip().casefold()
        if not key:
            raise ValueError("Ungültiger SIP-Header")
        headers.setdefault(key, []).append(value.strip())
    for required in ("via", "from", "to", "call-id", "cseq"):
        if not headers.get(required):
            raise ValueError(f"SIP-Header fehlt: {required}")
    return method, uri, headers


def _first(headers: dict[str, list[str]], name: str) -> str:
    rows = headers.get(name.casefold(), [])
    return rows[0] if rows else ""


def _extension_from_uri(uri: str) -> str:
    match = _SIP_URI.match(str(uri or "").strip())
    if not match:
        raise ValueError("SIP-Ziel ist keine lokale Nebenstelle")
    return match.group(1)


def _extension_from_header(value: str) -> str:
    match = _HEADER_USER.search(str(value or ""))
    if not match:
        raise ValueError("SIP-Header enthält keine lokale Nebenstelle")
    return match.group(1)


def _response(code: int, reason: str, headers: dict[str, list[str]], *, extra: list[tuple[str, str]] | None = None) -> bytes:
    rows = [f"SIP/2.0 {code} {reason}"]
    for value in headers.get("via", []):
        rows.append(f"Via: {value}")
    rows.append(f"From: {_first(headers, 'from')}")
    to_value = _first(headers, "to")
    if code >= 200 and ";tag=" not in to_value.casefold():
        to_value += ";tag=" + secrets.token_hex(6)
    rows.append(f"To: {to_value}")
    rows.append(f"Call-ID: {_first(headers, 'call-id')}")
    rows.append(f"CSeq: {_first(headers, 'cseq')}")
    rows.append("Server: SimpleOffice4Me-MiniSIP/1")
    for name, value in extra or []:
        rows.append(f"{name}: {value}")
    rows.extend(("Content-Length: 0", "", ""))
    return "\r\n".join(rows).encode("latin-1")


class SipRegistrarService:
    """Authenticated RFC3261-style registrar plus local INVITE redirector."""

    def __init__(self, config_path: str | Path, event: Callable[[dict[str, Any]], None] | None = None):
        self.config_path = Path(config_path)
        self.db_path = telephony_db_path(config_path)
        self.event = event or (lambda _row: None)
        self.stop_event = threading.Event()
        self.socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.registrations: dict[str, dict[str, Any]] = {}
        self.nonces: dict[str, tuple[float, str]] = {}
        self.nonce_counts: dict[tuple[str, str], int] = {}
        self.lock = threading.RLock()
        self.settings = effective_sip_settings(config_path)

    @property
    def realm(self) -> str:
        return str(self.settings.get("realm") or "simpleoffice.local")

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((str(self.settings["bind_host"]), int(self.settings["registrar_port"])))
        sock.settimeout(1.0)
        self.socket = sock
        self.thread = threading.Thread(target=self._loop, name="simpleoffice-sip-udp", daemon=True)
        self.thread.start()
        self.event({"service": "sip", "action": "started", "bind": self.settings["bind_host"], "port": self.settings["registrar_port"]})

    def stop(self) -> None:
        self.stop_event.set()
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
        if self.thread is not None:
            self.thread.join(timeout=3)
        self.socket = None
        self.thread = None

    def status(self) -> dict[str, Any]:
        self._cleanup()
        now = time.time()
        with self.lock:
            detail = [
                {
                    "extension": extension,
                    "host": str(item.get("host") or "")[:64],
                    "port": int(item.get("port") or 0),
                    "user_agent": str(item.get("user_agent") or "")[:160],
                    "registered_at": int(item.get("registered_at") or 0),
                    "expires_in": max(0, int(float(item.get("expires_at") or 0) - now)),
                }
                for extension, item in sorted(self.registrations.items())
            ]
        return {
            "running": self.socket is not None and not self.stop_event.is_set(),
            "bind_host": self.settings["bind_host"],
            "advertised_host": self.settings["advertised_host"],
            "port": int(self.settings["registrar_port"]),
            "transport": "udp",
            "realm": self.realm,
            "registrations": len(detail),
            "registrations_detail": detail,
        }

    def _loop(self) -> None:
        assert self.socket is not None
        while not self.stop_event.is_set():
            try:
                packet, source = self.socket.recvfrom(_MAX_PACKET + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            response = self.handle_datagram(packet, (str(source[0]), int(source[1])))
            if response:
                try:
                    self.socket.sendto(response, source)
                except OSError:
                    pass

    def _cleanup(self) -> None:
        now = time.time()
        with self.lock:
            self.registrations = {key: value for key, value in self.registrations.items() if float(value["expires_at"]) > now}
            self.nonces = {key: value for key, value in self.nonces.items() if value[0] > now}
            valid = set(self.nonces)
            self.nonce_counts = {key: value for key, value in self.nonce_counts.items() if key[0] in valid}

    def _profile(self, extension: str) -> dict[str, Any] | None:
        if not self.db_path.is_file():
            return None
        try:
            with sqlite3.connect(self.db_path, timeout=5) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    "SELECT extension,auth_user,digest_ha1,enabled FROM telephony_profile WHERE extension=?",
                    (extension,),
                ).fetchone()
        except sqlite3.Error:
            return None
        return dict(row) if row else None

    def _challenge(self, headers: dict[str, list[str]], source_ip: str, *, stale: bool = False) -> bytes:
        nonce = secrets.token_urlsafe(24)
        with self.lock:
            self.nonces[nonce] = (time.time() + _NONCE_TTL, source_ip)
        value = f'Digest realm="{self.realm}", nonce="{nonce}", algorithm=MD5, qop="auth"'
        if stale:
            value += ", stale=true"
        return _response(401, "Unauthorized", headers, extra=[("WWW-Authenticate", value)])

    def _authorized(self, method: str, uri: str, headers: dict[str, list[str]], source_ip: str, expected_user: str | None = None) -> str | None:
        authorization = _first(headers, "authorization")
        if not authorization:
            return None
        try:
            values = _parse_digest(authorization)
        except ValueError:
            return None
        username = str(values.get("username") or "")
        profile = self._profile(username)
        if not profile or not bool(profile.get("enabled")) or not str(profile.get("digest_ha1") or ""):
            return None
        if expected_user is not None and username != expected_user:
            return None
        nonce = str(values.get("nonce") or "")
        with self.lock:
            nonce_row = self.nonces.get(nonce)
        if not nonce_row or nonce_row[0] <= time.time() or nonce_row[1] != source_ip:
            return None
        if values.get("realm") != self.realm or values.get("uri") != uri:
            return None
        if str(values.get("qop") or "") != "auth":
            return None
        nc_text = str(values.get("nc") or "")
        cnonce = str(values.get("cnonce") or "")
        try:
            nc = int(nc_text, 16)
        except ValueError:
            return None
        if nc <= 0 or not cnonce:
            return None
        key = (nonce, username)
        with self.lock:
            if nc <= self.nonce_counts.get(key, 0):
                return None
        ha2 = _md5_hex(f"{method}:{uri}")
        expected = _md5_hex(f"{profile['digest_ha1']}:{nonce}:{nc_text}:{cnonce}:auth:{ha2}")
        if not secrets.compare_digest(expected, str(values.get("response") or "").casefold()):
            return None
        with self.lock:
            self.nonce_counts[key] = nc
        return username

    def _register(self, uri: str, headers: dict[str, list[str]], source: tuple[str, int]) -> bytes:
        # RFC 3261 REGISTER commonly targets sip:registrar-host without a user;
        # the Address-of-Record lives in To and determines the extension.
        extension = _extension_from_header(_first(headers, "to"))
        user = self._authorized("REGISTER", uri, headers, source[0], expected_user=extension)
        if user is None:
            return self._challenge(headers, source[0])
        contact = _first(headers, "contact")
        expires_text = _first(headers, "expires") or "3600"
        if ";expires=" in contact.casefold():
            try:
                expires_text = re.split(r";expires=", contact, flags=re.I)[-1].split(";", 1)[0].strip()
            except (IndexError, AttributeError):
                pass
        try:
            expires = int(expires_text)
        except ValueError:
            expires = 3600
        if contact == "*" and expires == 0:
            with self.lock:
                self.registrations.pop(extension, None)
            self.event({"service": "sip", "action": "unregistered", "extension": extension, "source": source[0]})
            return _response(200, "OK", headers, extra=[("Expires", "0")])
        if not contact:
            self._cleanup()
            with self.lock:
                registration = self.registrations.get(extension)
            extra: list[tuple[str, str]] = []
            if registration:
                left = max(0, int(float(registration["expires_at"]) - time.time()))
                extra.extend((("Contact", f"{registration['contact']};expires={left}"), ("Expires", str(left))))
            return _response(200, "OK", headers, extra=extra)
        expires = max(60, min(expires, _MAX_REGISTRATION))
        host = source[0]
        safe_contact = f"<sip:{extension}@{host}:{source[1]}>"
        user_agent = " ".join(_first(headers, "user-agent").split())[:160]
        registered_at = int(time.time())
        with self.lock:
            self.registrations[extension] = {
                "contact": safe_contact,
                "host": host,
                "port": source[1],
                "user_agent": user_agent,
                "registered_at": registered_at,
                "expires_at": time.time() + expires,
            }
        self.event({"service": "sip", "action": "registered", "extension": extension, "source": host, "expires": expires, "user_agent": user_agent})
        return _response(200, "OK", headers, extra=[("Contact", f"{safe_contact};expires={expires}"), ("Expires", str(expires))])

    def _invite(self, uri: str, headers: dict[str, list[str]], source: tuple[str, int]) -> bytes:
        caller = self._authorized("INVITE", uri, headers, source[0])
        if caller is None:
            return self._challenge(headers, source[0])
        try:
            from_user = _extension_from_header(_first(headers, "from"))
        except ValueError:
            return _response(403, "Forbidden", headers)
        if from_user != caller:
            return _response(403, "Forbidden", headers)
        target = _extension_from_uri(uri)
        self._cleanup()
        with self.lock:
            registration = self.registrations.get(target)
        if not registration:
            return _response(480, "Temporarily Unavailable", headers, extra=[("Retry-After", "5")])
        self.event({"service": "sip", "action": "invite_redirect", "from": caller, "to": target})
        return _response(302, "Moved Temporarily", headers, extra=[("Contact", registration["contact"])])

    def handle_datagram(self, packet: bytes, source: tuple[str, int]) -> bytes | None:
        if not _is_local_address(source[0]):
            return None
        try:
            method, uri, headers = _split_packet(packet)
            if method == "OPTIONS":
                return _response(
                    200,
                    "OK",
                    headers,
                    extra=[("Allow", "REGISTER, INVITE, OPTIONS"), ("Accept", "application/sdp")],
                )
            if method == "REGISTER":
                return self._register(uri, headers, source)
            if method == "INVITE":
                return self._invite(uri, headers, source)
            return _response(405, "Method Not Allowed", headers, extra=[("Allow", "REGISTER, INVITE, OPTIONS")])
        except ValueError:
            return None
