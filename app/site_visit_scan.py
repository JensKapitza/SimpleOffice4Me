"""Explicit, bounded TCP inventory scan for an authorized private LAN."""
from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

MAX_HOSTS = 254
MAX_PORTS = 32
MAX_WORKERS = 24
MAX_TIMEOUT = 1.0
RFC1918 = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
PORT_PROFILES = {
    "web": (80, 443, 8080, 8443),
    "mail": (25, 110, 143, 465, 587, 993, 995),
    "file_sharing": (139, 445, 548, 2049),
    "remote_access": (22, 23, 3389, 5900, 5985, 5986),
    "databases": (1433, 1521, 3306, 5432, 6379, 27017),
}


def parse_scan_request(cidr: str, ports: str, approved: bool, profiles=()) -> tuple[ipaddress.IPv4Network, list[int]]:
    if not approved:
        raise ValueError("Netzwerkscan erst nach bestätigter Freigabe starten")
    try:
        network = ipaddress.ip_network(str(cidr or "").strip(), strict=True)
    except ValueError as exc:
        raise ValueError("Bitte ein gültiges Netz mit CIDR-Präfix eingeben, zum Beispiel 192.168.1.0/24") from exc
    if network.version != 4 or not any(network.subnet_of(private) for private in RFC1918):
        raise ValueError("Nur explizit freigegebene private IPv4-Netze können gescannt werden")
    if network.prefixlen < 24 or network.prefixlen > 30 or network.num_addresses > MAX_HOSTS + 2:
        raise ValueError("Netz ist zu groß oder zu klein. Erlaubt sind maximal 254 IPv4-Geräte")
    if isinstance(profiles, str):
        profiles = (profiles,)
    selected_profiles = list(dict.fromkeys(str(value).strip() for value in profiles if str(value).strip()))
    unknown_profiles = set(selected_profiles) - PORT_PROFILES.keys()
    if unknown_profiles:
        raise ValueError("Unbekannte Portgruppe ausgewählt")
    parsed: list[int] = [port for profile in selected_profiles for port in PORT_PROFILES[profile]]
    for token in str(ports or "").replace(";", ",").split(","):
        raw = token.strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError("Ports müssen als einzelne TCP-Portnummern angegeben werden") from exc
        if not 1 <= value <= 65535:
            raise ValueError("TCP-Ports müssen zwischen 1 und 65535 liegen")
        if value not in parsed:
            parsed.append(value)
        if len(parsed) > MAX_PORTS:
            raise ValueError(f"Maximal {MAX_PORTS} Ports je Scan")
    if not parsed:
        raise ValueError("Mindestens einen TCP-Port eingeben")
    return network, parsed


def _probe(address: str, ports: list[int], timeout: float) -> dict | None:
    opened = []
    for port in ports:
        try:
            with socket.create_connection((address, port), timeout=timeout):
                opened.append({"port": port, "protocol": "tcp", "state": "open", "observed_at": datetime.now(timezone.utc).isoformat(), "source": "Ortstermin-TCP-Scan"})
        except (OSError, TimeoutError):
            continue
    if not opened:
        return None
    return {"ip": address, "ports": opened}


def scan_authorized_private_network(cidr: str, ports: str, approved: bool, *, profiles=(), timeout: float = 0.35) -> dict:
    """Probe selected TCP ports only; never performs service exploitation or DNS lookup."""
    network, selected_ports = parse_scan_request(cidr, ports, approved, profiles)
    timeout = max(0.1, min(float(timeout), MAX_TIMEOUT))
    addresses = [str(address) for address in network.hosts()]
    devices = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(addresses)))) as executor:
        futures = [executor.submit(_probe, address, selected_ports, timeout) for address in addresses]
        for future in as_completed(futures):
            result = future.result()
            if result:
                devices.append(result)
    devices.sort(key=lambda row: ipaddress.ip_address(row["ip"]))
    return {"cidr": str(network), "ports": selected_ports, "probed_hosts": len(addresses), "devices": devices, "scanned_at": datetime.now(timezone.utc).isoformat(), "scanner": "TCP connect", "limitations": "Nur ausgewählte TCP-Ports; keine UDP-Erkennung, keine Betriebssystem-/Herstellererkennung und kein Nachweis für Kompromittierung oder Datenabfluss."}
