"""Validation and pinned HTTP access for federation discovery targets.

Direct discovery deliberately accepts an administrator supplied host, but it must
never turn into an unrestricted server-side URL fetch. Only a canonical server
base URL is accepted. DNS is resolved once, every returned address is checked,
and the actual HTTP connection is pinned to one of those checked addresses to
avoid DNS-rebinding between validation and connect.
"""
import http.client
import ipaddress
import json
import os
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit


_TRUE_VALUES = {"1", "true", "yes", "on"}
_DISCOVERY_PATH = "/.well-known/simpleoffice-federation"
_MAX_DISCOVERY_RESPONSE = 1024 * 1024
_USER_AGENT = "SimpleOffice4Me-Federation-Discovery/1"


def _env_flag(name):
    return os.environ.get(name, "0").strip().casefold() in _TRUE_VALUES


def _hostname(value):
    host = str(value or "").strip().rstrip(".")
    if not host or "%" in host:
        raise ValueError("invalid peer endpoint host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("invalid peer endpoint host") from exc
        labels = host.split(".")
        if len(host) > 253 or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or any(not (char.isalnum() or char == "-") for char in label)
            for label in labels
        ):
            raise ValueError("invalid peer endpoint host")
        return host
    return address.compressed


def normalize_endpoint(value):
    """Return a canonical federation server base URL."""
    value = str(value or "").strip()
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("invalid peer endpoint")
    if parsed.username or parsed.password:
        raise ValueError("credentials are not allowed in peer endpoint")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("peer endpoint must be a server base URL without path, query or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid peer endpoint port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("invalid peer endpoint port")
    host = _hostname(parsed.hostname)
    netloc = _format_host(host)
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def _format_host(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    return f"[{address.compressed}]" if address.version == 6 else address.compressed


def _resolve_addresses(host, port):
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("peer endpoint cannot be resolved") from exc
    addresses = []
    for row in rows:
        try:
            address = ipaddress.ip_address(row[4][0].split("%", 1)[0])
        except (ValueError, IndexError, TypeError):
            continue
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ValueError("peer endpoint has no usable network address")
    return addresses


def _validate_address(address, allow_private, allow_loopback):
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if address.is_loopback:
        if allow_loopback:
            return
        raise ValueError("loopback federation discovery is disabled")
    if address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
        raise ValueError("peer endpoint resolves to a forbidden network address")
    if address.is_private and not allow_private:
        raise ValueError("private federation discovery requires SIMPLEOFFICE_FEDERATION_ALLOW_PRIVATE_TARGETS=1")
    if not address.is_global and not address.is_private:
        raise ValueError("peer endpoint resolves to a non-public network address")


def _validated_target(value):
    base_url = normalize_endpoint(value)
    parsed = urlsplit(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    allow_private = _env_flag("SIMPLEOFFICE_FEDERATION_ALLOW_PRIVATE_TARGETS")
    allow_loopback = _env_flag("SIMPLEOFFICE_FEDERATION_ALLOW_LOOPBACK")
    addresses = _resolve_addresses(parsed.hostname, port)
    for address in addresses:
        _validate_address(address, allow_private, allow_loopback)
    return base_url, parsed, port, addresses


def validate_discovery_endpoint(value):
    """Validate a direct-discovery destination without opening a connection."""
    base_url, _parsed, _port, _addresses = _validated_target(value)
    return base_url


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection to a checked IP while verifying TLS for the original host."""

    def __init__(self, connect_host, port, server_hostname, timeout):
        self._server_hostname = server_hostname
        super().__init__(
            connect_host,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )

    def connect(self):
        self.sock = self._create_connection(
            (self.host, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(
            self.sock, server_hostname=self._server_hostname
        )


def _connection_for(scheme, connect_host, port, server_hostname, timeout):
    if scheme == "https":
        return _PinnedHTTPSConnection(connect_host, port, server_hostname, timeout)
    return http.client.HTTPConnection(connect_host, port=port, timeout=timeout)


def _host_header(host, port, scheme):
    default_port = 443 if scheme == "https" else 80
    rendered = _format_host(host)
    return rendered if port == default_port else f"{rendered}:{port}"


def fetch_discovery_profile(value, timeout=8):
    """Fetch the fixed discovery document through an IP-pinned connection."""
    _base_url, parsed, port, addresses = _validated_target(value)
    connect_host = addresses[0].compressed
    connection = _connection_for(parsed.scheme, connect_host, port, parsed.hostname, timeout)
    try:
        connection.request(
            "GET",
            _DISCOVERY_PATH,
            headers={
                "Accept": "application/json",
                "Host": _host_header(parsed.hostname, port, parsed.scheme),
                "User-Agent": _USER_AGENT,
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("peer discovery returned an unexpected HTTP status")
        raw = response.read(_MAX_DISCOVERY_RESPONSE + 1)
        if len(raw) > _MAX_DISCOVERY_RESPONSE:
            raise ValueError("peer discovery response is too large")
    finally:
        connection.close()
    try:
        profile = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("peer discovery response is not valid JSON") from exc
    if not isinstance(profile, dict):
        raise ValueError("peer discovery response must be a JSON object")
    return profile
