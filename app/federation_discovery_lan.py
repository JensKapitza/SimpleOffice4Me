"""User-triggered federation discovery on private IPv4 LAN segments.

Automatic discovery stays narrow and uses local /24 networks. Administrators may
provide explicit RFC1918 or RFC6598 CIDRs for Docker/Podman/VPN deployments where
the container network is not the LAN that should be searched. Explicit ranges are bounded and
never allow public, link-local or metadata networks.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

from .federation_compatibility import compatibility
from .federation_discovery_endpoint import fetch_discovery_profile
from .federation_discovery_service import remember_discovered_peer
from .federation_local_profile import local_peer_id
from .federation_peer_profile import peer_profile

_DEFAULT_PORT = 8080
_MAX_PORTS = 4
_MAX_NETWORKS = 4
_MAX_SCAN_HOSTS = 1024
_DEFAULT_TIMEOUT = 0.35
_MAX_TIMEOUT = 1.5
_MAX_WORKERS = 32
_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_EXPLICIT_SCAN_RANGES = _RFC1918 + (ipaddress.ip_network("100.64.0.0/10"),)


def is_private_lan_ipv4(value) -> bool:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    return address.version == 4 and any(address in network for network in _RFC1918)


def _private_network(network) -> bool:
    return network.version == 4 and any(network.subnet_of(parent) for parent in _EXPLICIT_SCAN_RANGES)


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

    # Android supplies the active Wi-Fi/Ethernet addresses through the native
    # ConnectivityManager bridge. Do not fall back to generic socket routing on
    # Android: a cellular or VPN route may also use RFC1918 space and must not
    # be treated as a local federation scan network.
    if os.environ.get("SIMPLEOFFICE_ANDROID") == "1":
        return found[:_MAX_NETWORKS]

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


def scan_networks(value="", *, addresses=None) -> list[ipaddress.IPv4Network]:
    """Resolve explicit scan CIDRs or derive the legacy local /24 networks."""
    raw = str(value or "").strip()
    if raw:
        networks = []
        tokens = raw.replace(";", ",").replace("\n", ",").split(",")
        for token in tokens:
            text = token.strip()
            if not text:
                continue
            if "/" not in text:
                if not is_private_lan_ipv4(text):
                    try:
                        address = ipaddress.ip_address(text)
                    except ValueError as exc:
                        raise ValueError("Scan-Basis muss eine lokale IPv4-Adresse oder ein CIDR sein") from exc
                    if address.version != 4 or not any(address in parent for parent in _EXPLICIT_SCAN_RANGES):
                        raise ValueError("Scan-Basis muss RFC1918 oder RFC6598 (100.64/10) sein")
                text += "/24"
            try:
                network = ipaddress.ip_network(text, strict=False)
            except ValueError as exc:
                raise ValueError("Ungültiges Scan-Netz/CIDR") from exc
            if not _private_network(network):
                raise ValueError("Es dürfen nur RFC1918- oder RFC6598-Netze gescannt werden")
            if network.prefixlen < 22:
                raise ValueError("Scan-Netz ist zu groß; maximal /22")
            if network not in networks:
                networks.append(network)
            if len(networks) > _MAX_NETWORKS:
                raise ValueError("Maximal vier Scan-Netze sind erlaubt")
        if not networks:
            raise ValueError("Mindestens ein Scan-Netz angeben")
    else:
        local = list(addresses) if addresses is not None else local_lan_addresses()
        local = [value for value in local if is_private_lan_ipv4(value)][:_MAX_NETWORKS]
        if not local:
            raise ValueError("Kein privates IPv4-WLAN/LAN gefunden; bei Docker Scan-Netz explizit angeben")
        networks = []
        for value in local:
            network = ipaddress.ip_network(f"{value}/24", strict=False)
            if network not in networks:
                networks.append(network)

    host_count = sum(max(0, int(network.num_addresses) - 2) for network in networks)
    if host_count > _MAX_SCAN_HOSTS:
        raise ValueError(f"Scan-Bereich zu groß; maximal {_MAX_SCAN_HOSTS} Hosts")
    return networks


def _targets_for_networks(networks, addresses, ports) -> list[str]:
    own = {ipaddress.ip_address(value) for value in addresses if is_private_lan_ipv4(value)}
    endpoints: list[str] = []
    for network in networks[:_MAX_NETWORKS]:
        for address in network.hosts():
            if address in own:
                continue
            for port in ports:
                endpoints.append(f"http://{address.compressed}:{port}")
                if len(endpoints) >= _MAX_SCAN_HOSTS * max(1, len(ports)):
                    return endpoints
    return endpoints


def _targets(addresses, ports) -> list[str]:
    networks = scan_networks("", addresses=addresses)
    return _targets_for_networks(networks, addresses, ports)


def _probe(endpoint: str, timeout: float):
    candidates = [endpoint]
    if endpoint.startswith("http://"):
        candidates.append("https://" + endpoint[len("http://"):])
    for candidate in candidates:
        try:
            data = fetch_discovery_profile(candidate, timeout=timeout, allow_private=True)
            data = dict(data)
            # The address we just proved reachable is more useful for local
            # transfer than an unrelated public URL advertised by the peer.
            data["base_url"] = candidate
            return peer_profile(data)
        except (OSError, ValueError):
            continue
    return None


def discover_lan(root, *, addresses=None, ports=None, networks=None, timeout=_DEFAULT_TIMEOUT) -> dict:
    addresses = list(addresses) if addresses is not None else local_lan_addresses()
    addresses = [value for value in addresses if is_private_lan_ipv4(value)][:_MAX_NETWORKS]
    selected_networks = scan_networks(networks or "", addresses=addresses)
    ports = scan_ports() if ports is None else [int(value) for value in ports if 1 <= int(value) <= 65535][:_MAX_PORTS]
    timeout = max(0.1, min(float(timeout), _MAX_TIMEOUT))
    targets = _targets_for_networks(selected_networks, addresses, ports)
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
            found[stored["peer_id"]] = {**stored, "compatibility": compatibility(stored)}

    return {
        "peers": sorted(found.values(), key=lambda item: (item["label"].casefold(), item["peer_id"])),
        "networks": [str(network) for network in selected_networks],
        "ports": ports,
        "probed": len(targets),
    }
