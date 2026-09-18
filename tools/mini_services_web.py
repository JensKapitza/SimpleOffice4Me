"""Standard-library client for services owned by the running Flask application."""
from __future__ import annotations

import getpass
import ipaddress
import json
import sys
import warnings
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener

from simpleoffice_mini_control import NETWORK_SERVICES

WEB_SERVICES = ("audio-sender", "audio-receiver", "audio-output", "http-boot")
MAX_RESPONSE = 2 * 1024 * 1024


class ClientError(ValueError):
    """A safe, user-facing failure, without response bodies or credentials."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = ""

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("name") == "csrf-token":
            self.token = values.get("content", "")


def validate_url(value):
    try:
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment or url.path not in {"", "/"}
                or any(ord(c) <= 32 for c in value) or (url.port is not None and not 1 <= url.port <= 65535)):
            raise ValueError
        if url.scheme == "http":
            # Literal loopback only: no DNS rebinding or accidental LAN credentials.
            if not ipaddress.ip_address(url.hostname).is_loopback:
                raise ValueError
    except ValueError:
        raise ClientError("Web-URL muss HTTPS verwenden; HTTP ist nur mit einer Loopback-IP erlaubt (z. B. http://127.0.0.1:8080).") from None
    return value.rstrip("/")


def read_password(from_stdin=False):
    if from_stdin:
        password = sys.stdin.readline(1025).rstrip("\r\n")
    else:
        if not sys.stdin.isatty():
            raise ClientError("Passwort benötigt ein Terminal oder ausdrücklich --password-stdin.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("SimpleOffice-Passwort: ")
        except (getpass.GetPassWarning, EOFError):
            raise ClientError("Verdeckte Passworteingabe nicht verfügbar. Terminal prüfen oder --password-stdin verwenden.") from None
    if not password or len(password) > 128:
        raise ClientError("Passwort fehlt oder ist zu lang.")
    return password


class WebClient:
    def __init__(self, base_url, timeout=10):
        self.base = validate_url(base_url)
        self.timeout = timeout
        # Session stays in memory; no ambient proxy, redirects or cookie files.
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(CookieJar()))
        self.token = ""

    def request(self, path, data=None, *, form=False, login=False):
        headers = {"Accept": "text/html" if path == "/auth/login" else "application/json"}
        if data is not None:
            data = (urlencode(data) if form else json.dumps(data)).encode("utf-8")
            headers.update({"Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
                            "X-CSRF-Token": self.token})
        try:
            with self.opener.open(Request(self.base + path, data=data, headers=headers), timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE + 1)
                content_type = response.headers.get_content_type()
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if login and code == 302:
                return None
            if code in {301, 302, 303, 307, 308, 401, 403}:
                raise ClientError("Anmeldung oder Admin-Berechtigung fehlt; Web-URL und Zugang prüfen. Weiterleitungen werden nicht verfolgt.") from None
            if code == 429:
                raise ClientError("Anmeldung vorübergehend gesperrt. Später erneut versuchen.") from None
            raise ClientError(f"Webdienst meldet HTTP {code}. Status und Diagnose in SimpleOffice prüfen.") from None
        except (OSError, URLError, HTTPException):
            raise ClientError("Webdienst nicht erreichbar oder TLS-Prüfung fehlgeschlagen. Adresse, Zertifikat und laufenden Webprozess prüfen; eine Aktion kann bereits ausgeführt worden sein.") from None
        if len(body) > MAX_RESPONSE:
            raise ClientError("Antwort des Webdienstes ist zu groß.")
        if path == "/auth/login":
            if content_type != "text/html":
                raise ClientError("Keine gültige SimpleOffice-Anmeldeseite erhalten.")
            return body.decode("utf-8", errors="replace")
        if content_type != "application/json":
            raise ClientError("Webdienst hat keine JSON-Antwort geliefert. Anmeldung und Web-URL prüfen.")
        try:
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeError, RecursionError):
            raise ClientError("Ungültige JSON-Antwort des Webdienstes.") from None

    def load_token(self):
        parser = TokenParser()
        parser.feed(self.request("/auth/login"))
        if not 32 <= len(parser.token) <= 256 or not parser.token.isascii() or not all(c.isalnum() or c in "-_" for c in parser.token):
            raise ClientError("Sicherheitstoken fehlt auf der Anmeldeseite.")
        self.token = parser.token

    def login(self, username, password):
        self.load_token()
        result = self.request("/auth/login", {"username": username, "password": password, "_csrf_token": self.token}, form=True, login=True)
        if result is not None:
            raise ClientError("Anmeldung fehlgeschlagen. Benutzername, Passwort und Kontostatus prüfen.")
        self.load_token()  # Successful login rotates the session and CSRF token.

    def command(self, service, action):
        if (service not in WEB_SERVICES and not (service in NETWORK_SERVICES and action == "scan")) or action not in {"start", "stop", "restart", "status", "scan"}:
            raise ClientError("Unbekannter Webdienst oder unbekannte Aktion.")
        path = "/api/mini-services/" + service
        result = self.request(path if action == "status" else path + "/" + action,
                              None if action == "status" else {})
        state = result.get("state")
        if state is not None and not isinstance(state, str):
            raise ClientError("Ungültiger Dienststatus in der Webantwort.")
        if result.get("error") or state == "failed":
            return result, 1
        if action == "status":
            return result, 0 if state in {"running", "degraded"} else 3
        if action == "stop":
            return result, 0 if state == "stopped" or result.get("stopped") is True else 3
        if action == "scan":
            return result, 0 if state == "completed" else 3
        return result, 0 if state in {"running", "degraded"} else 3


def web_command(service, action, *, base_url, username, password_stdin=False, timeout=10):
    client = WebClient(base_url, timeout)
    password = read_password(password_stdin)
    client.login(username, password)
    del password
    return client.command(service, action)
