"""Explicit, bounded TCP inventory scan for an authorized private LAN."""
from __future__ import annotations

import ipaddress
import shlex
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
TOP_PORTS = (
    21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 443, 445, 993, 995, 1723,
    3306, 3389, 5900, 8080, 8443, 3000, 5432, 1433, 1521, 2049, 587, 465, 631, 8888, 8000, 8081,
)
TIMING = {0: (1.0, 1), 1: (0.8, 2), 2: (0.6, 6), 3: (0.35, 12), 4: (0.2, MAX_WORKERS)}


def parse_nmap_options(value: str) -> dict:
    """Parse a small, explicit Nmap-like allowlist; never invokes a shell or Nmap."""
    try:
        tokens = shlex.split(str(value or ""))
    except ValueError as exc:
        raise ValueError("Scanner-Optionen konnten nicht gelesen werden; Anführungszeichen prüfen") from exc
    if tokens and tokens[0].lower() in {"nmap", "nmap.exe"}:
        tokens.pop(0)
    ports: list[str] = []
    top_count = None
    replace_ports = False
    timing = 3
    index = 0
    while index < len(tokens):
        option = tokens[index]
        index += 1
        if option in {"-sT", "-n", "-Pn", "--open", "-r"}:
            continue  # These match the scanner's fixed behavior.
        if option == "-F":
            replace_ports = True
            ports.extend(str(port) for port in TOP_PORTS)
            continue
        if option.startswith("--top-ports="):
            raw_count = option.partition("=")[2]
        elif option == "--top-ports":
            if index >= len(tokens):
                raise ValueError("--top-ports benötigt eine Zahl von 1 bis 32")
            raw_count = tokens[index]
            index += 1
        else:
            raw_count = None
        if raw_count is not None:
            replace_ports = True
            try:
                top_count = int(raw_count)
            except ValueError as exc:
                raise ValueError("--top-ports benötigt eine Zahl von 1 bis 32") from exc
            if not 1 <= top_count <= MAX_PORTS:
                raise ValueError(f"--top-ports ist auf 1 bis {MAX_PORTS} Ports begrenzt")
            ports.extend(str(port) for port in TOP_PORTS[:top_count])
            continue
        if option == "-p" or (option.startswith("-p") and len(option) > 2):
            replace_ports = True
            if option == "-p":
                if index >= len(tokens):
                    raise ValueError("-p benötigt Portnummern, z. B. -p 80,443")
                raw_ports = tokens[index]
                index += 1
            else:
                raw_ports = option[2:]
            ports.append(raw_ports)
            continue
        if option.startswith("-T"):
            raw_timing = option[2:]
            if not raw_timing:
                if index >= len(tokens):
                    raise ValueError("-T benötigt ein Profil von 0 bis 4")
                raw_timing = tokens[index]
                index += 1
            try:
                timing = int(raw_timing)
            except ValueError as exc:
                raise ValueError("-T unterstützt nur Profile 0 bis 4") from exc
            if timing not in TIMING:
                raise ValueError("-T unterstützt nur Profile 0 bis 4; das höchste Profil bleibt begrenzt")
            continue
        raise ValueError(f"Nicht unterstützte Scanner-Option: {option}")
    timeout, workers = TIMING[timing]
    return {"ports": ",".join(ports), "replace_ports": replace_ports, "timing": timing, "timeout": timeout, "workers": workers}


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
            if "-" in raw:
                start_text, end_text = raw.split("-", 1)
                start, end = int(start_text.strip()), int(end_text.strip())
                if not 1 <= start <= end <= 65535:
                    raise ValueError
                if end - start + 1 > MAX_PORTS:
                    raise ValueError(f"Maximal {MAX_PORTS} Ports je Scan")
                values = range(start, end + 1)
            else:
                values = (int(raw),)
        except ValueError as exc:
            if str(exc).startswith("Maximal"):
                raise
            raise ValueError("TCP-Ports als Nummern oder kleiner Bereich, z. B. 80,443,8000-8005, angeben") from exc
        for value in values:
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


def scan_authorized_private_network(cidr: str, ports: str, approved: bool, *, profiles=(), options="", timeout: float | None = None) -> dict:
    """Probe selected TCP ports only; never performs service exploitation or DNS lookup."""
    parsed_options = parse_nmap_options(options)
    base_ports = "" if parsed_options["replace_ports"] else ports
    combined_ports = ",".join(value for value in (base_ports, parsed_options["ports"]) if value)
    network, selected_ports = parse_scan_request(cidr, combined_ports, approved, () if parsed_options["replace_ports"] else profiles)
    timeout = parsed_options["timeout"] if timeout is None else max(0.1, min(float(timeout), MAX_TIMEOUT))
    workers = parsed_options["workers"]
    addresses = [str(address) for address in network.hosts()]
    devices = []
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(addresses)))) as executor:
        futures = [executor.submit(_probe, address, selected_ports, timeout) for address in addresses]
        for future in as_completed(futures):
            result = future.result()
            if result:
                devices.append(result)
    devices.sort(key=lambda row: ipaddress.ip_address(row["ip"]))
    return {"cidr": str(network), "ports": selected_ports, "probed_hosts": len(addresses), "devices": devices, "scanned_at": datetime.now(timezone.utc).isoformat(), "scanner": f"TCP connect (-T{parsed_options['timing']})", "limitations": "Nur ausgewählte TCP-Ports; keine UDP-Erkennung, keine Betriebssystem-/Herstellererkennung und kein Nachweis für Kompromittierung oder Datenabfluss."}
