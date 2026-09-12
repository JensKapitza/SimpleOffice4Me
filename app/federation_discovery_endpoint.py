"""Validation for federation discovery targets.

Direct discovery deliberately accepts an administrator supplied host, but it must
never turn into an unrestricted server-side URL fetch.  Only a canonical server
base URL is accepted and unsafe network destinations are rejected before a
request is made.
"""
import ipaddress
import os
import socket
from urllib.parse import urlsplit, urlunsplit


_TRUE_VALUES = {"1", "true", "yes", "on"}


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
        if len(host) > 253 or any(not label or len(label) > 63 for label in host.split(".")):
            raise ValueError("invalid peer endpoint host")
        return host
    return address.compressed


def normalize_endpoint(value):
    """Return a canonical federation server base URL.

    Paths, queries and fragments are rejected instead of being carried into an
    outbound request.  Discovery itself appends the one fixed well-known path.
    """
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
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        url_host = host
    else:
        url_host = f"[{host}]" if address.version == 6 else host
    netloc = url_host if port is None else f"{url_host}:{port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


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


def validate_discovery_endpoint(value):
    """Validate a direct-discovery destination immediately before network I/O.

    Private RFC1918/ULA peers remain possible for intentional LAN/VPN setups, but
    they require an explicit server-side opt-in.  Loopback requires its existing,
    separate test/development opt-in.  Link-local destinations (including common
    cloud metadata endpoints) are never accepted.
    """
    base_url = normalize_endpoint(value)
    parsed = urlsplit(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    allow_private = _env_flag("SIMPLEOFFICE_FEDERATION_ALLOW_PRIVATE_TARGETS")
    allow_loopback = _env_flag("SIMPLEOFFICE_FEDERATION_ALLOW_LOOPBACK")
    for address in _resolve_addresses(parsed.hostname, port):
        _validate_address(address, allow_private, allow_loopback)
    return base_url
