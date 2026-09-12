"""User-triggered federation discovery on the local IPv4 WLAN segment.

The scanner is deliberately narrow: it only probes RFC1918 addresses in the
same /24 as this instance and only asks the fixed SimpleOffice well-known
endpoint on configured application ports. It is not an Internet scanner and it
does not grant trust or data permissions.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

from .federation_discovery_endpoint import fetch_discovery_profile
from .federation_discovery_service import remember_discovered_peer
from .federation_local_profile import local_peer_id
from .federation_peer_profile import peer_profile

_DEFAULT_PORT = 8080
_MAX_PORTS = 4
_MAX_NETWORKS = 4
_MAX_HOSTS = 254 * _MAX_NETWORKS
_DEFAULT_TIMEOUT = 0.35
_MAX_TIMEOUT = 1.5
_MAX_WORKERS = 32
_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def is_private_lan_ipv4(value) -> bool:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    return address.version == 4 and any(address in network for network in _RFC1918)


def _configured_addresses() -> list[str]:
    raw = os.environ.get("SIMPLEOFFICE_FEDERATION_LAN_ADDRESS", "")
    return [value.strip() for value in raw.split(",") if value.strip()]


def local_lan_addresses() -> list[str]:
    """Return a small unique set of RFC1918 addresses owned by this host."""
    found: list[str] = []

    def add(value) -> None:
        text = str(value or "").split("%", 1)[0]
        if is_private_lan_ipv4(text) and text not in found:
            found.append(text)

    for value in _configured_addresses():
        add(value)

    try:
        for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            add(row[4][0])
    except socket.gaierror:
        pass

    # UDP connect selects the active route without sending application data.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    return found[:_MAX_NETWORKS]


def scan_ports(extra_port=None) -> list[int]:
    values = [
        os.environ.get("SIMPLEOFFICE_PORT", ""),
        os.environ.get("SIMPLEOFFICE_FEDERATION_LAN_PORTS", ""),
    ]
    if extra_port not in (None, ""):
        values.append(str(extra_port))
    ports: list[int] = []
    for raw in values:
        for item in str(raw or "").replace(";", ",").split(","):
            try:
                port = int(item.strip())
            except ValueError:
                continue
            if 1 <= port <= 65535 and port not in ports:
                ports.append(port)
            if len(ports) >= _MAX_PORTS:
                break
    if _DEFAULT_PORT not in ports and len(ports) < _MAX_PORTS:
        ports.append(_DEFAULT_PORT)
    return ports or [_DEFAULT_PORT]


def _targets(addresses, ports) -> list[str]:
    own = {ipaddress.ip_address(value) for value in addresses if is_private_lan_ipv4(value)}
    endpoints: list[str] = []
    networks = []
    for address in own:
        network = ipaddress.ip_network(f"{address}/24", strict=False)
        if network not in networks:
            networks.append(network)
    for network in networks[:_MAX_NETWORKS]:
        for address in network.hosts():
            if address in own:
                continue
            for port in ports:
                endpoints.append(f"http://{address.compressed}:{port}")
                if len(endpoints) >= _MAX_HOSTS * max(1, len(ports)):
                    return endpoints
    return endpoints


def _probe(endpoint: str, timeout: float):
    try:
        data = fetch_discovery_profile(endpoint, timeout=timeout, allow_private=True)
        data = dict(data)
        # The LAN address is the endpoint we just proved reachable and is more
        # useful for phone-to-phone transfer than an unrelated public URL.
        data["base_url"] = endpoint
        return peer_profile(data)
    except (OSError, ValueError):
        return None


def discover_lan(root, *, addresses=None, ports=None, timeout=_DEFAULT_TIMEOUT) -> dict:
    addresses = list(addresses) if addresses is not None else local_lan_addresses()
    addresses = [value for value in addresses if is_private_lan_ipv4(value)][:_MAX_NETWORKS]
    if not addresses:
        raise ValueError("Kein privates IPv4-WLAN/LAN gefunden")
    ports = scan_ports() if ports is None else [int(value) for value in ports if 1 <= int(value) <= 65535][:_MAX_PORTS]
    timeout = max(0.1, min(float(timeout), _MAX_TIMEOUT))
    targets = _targets(addresses, ports)
    found = {}
    own_peer = local_peer_id()

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, max(1, len(targets)))) as executor:
        futures = {executor.submit(_probe, endpoint, timeout): endpoint for endpoint in targets}
        for future in as_completed(futures):
            profile = future.result()
            if not profile or profile["peer_id"] == own_peer:
                continue
            source = "lan:" + profile["base_url"].split("//", 1)[-1].split(":", 1)[0]
            stored = remember_discovered_peer(root, profile, source)
            found[stored["peer_id"]] = stored

    networks = sorted({str(ipaddress.ip_network(f"{value}/24", strict=False)) for value in addresses})
    return {
        "peers": sorted(found.values(), key=lambda item: (item["label"].casefold(), item["peer_id"])),
        "networks": networks,
        "ports": ports,
        "probed": len(targets),
    }
