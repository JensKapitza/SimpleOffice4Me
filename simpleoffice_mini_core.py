"""Dependency-free configuration and protocol helpers for mini network services."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import struct
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DHCP_MAGIC = b"\x63\x82\x53\x63"
DHCP_HEADER = struct.Struct("!BBBBIHH4s4s4s4s16s64s128s")
DHCP_DISCOVER = 1
DHCP_OFFER = 2
DHCP_REQUEST = 3
DHCP_DECLINE = 4
DHCP_ACK = 5
DHCP_NAK = 6
DHCP_RELEASE = 7
DHCP_INFORM = 8

DNS_TYPES = {
    "A": 1,
    "NS": 2,
    "CNAME": 5,
    "SOA": 6,
    "PTR": 12,
    "MX": 15,
    "TXT": 16,
    "AAAA": 28,
    "SRV": 33,
    "ANY": 255,
}
DNS_TYPE_NAMES = {value: key for key, value in DNS_TYPES.items()}
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9_.-]+\.?$")
_MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$", re.I)
LOOPBACK_IPV4 = str(ipaddress.IPv4Address("127.0.0.1"))
UNSPECIFIED_IPV4 = str(ipaddress.IPv4Address(0))

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "dhcp": {
        "enabled": False,
        "bind": LOOPBACK_IPV4,
        "port": 67,
        "interface": "",
        "server_ip": "192.168.178.1",
        "network": "192.168.178.0/24",
        "pool_start": "192.168.178.20",
        "pool_end": "192.168.178.200",
        "routers": ["192.168.178.1"],
        "dns_servers": ["192.168.178.1"],
        "domain": "home.arpa",
        "domain_search": ["home.arpa"],
        "ntp_servers": [],
        "lease_time": 86400,
        "renewal_time": 43200,
        "rebinding_time": 75600,
        "authoritative": True,
        "ping_check": False,
        "decline_hold_seconds": 600,
        "mtu": 1500,
        "next_server": "",
        "tftp_server": "",
        "boot_file": "",
        "reservations": [],
        "exclusions": [],
        "static_routes": [],
        "custom_options": {},
    },
    "dns": {
        "enabled": False,
        "bind": ["127.0.0.1"],
        "port": 53,
        "upstreams": ["1.1.1.1", "9.9.9.9"],
        "timeout": 2.0,
        "cache_enabled": True,
        "cache_max_entries": 10000,
        "query_log": True,
        "query_log_max_bytes": 10 * 1024 * 1024,
        "records": [],
        "manual_blocks": [],
        "allowlist": [],
        "block_mode": "zero",
        "blocklist_urls": [],
        "blocklist_refresh_hours": 24,
    },
}


def _https_open(request: urllib.request.Request, timeout: float):
    parsed = urlsplit(request.full_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Blocklisten-URL muss eine HTTPS-Adresse ohne Zugangsdaten sein")
    response = urllib.request.build_opener().open(request, timeout=timeout)
    final = urlsplit(response.geturl())
    if final.scheme != "https" or not final.hostname or final.username or final.password:
        response.close()
        raise ValueError("Redirect auf unsichere Blocklisten-Adresse wurde abgelehnt")
    return response


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config_path() -> Path:
    configured = os.environ.get("SIMPLEOFFICE_MINI_SERVICES_CONFIG", "").strip()
    return Path(configured).expanduser() if configured else project_root() / "instance" / "mini-services.json"


def state_dir(config_path: str | Path | None = None) -> Path:
    path = Path(config_path or default_config_path())
    return path.parent / "mini-services"


def status_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path) / "status.json"


def leases_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path) / "dhcp-leases.json"


def dns_log_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path) / "dns-queries.jsonl"


def blocklist_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path) / "dns-blocklist.txt"


def blocklist_meta_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path) / "dns-blocklist-meta.json"


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    temporary.replace(path)


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(fallback)


def normalize_domain(value: str, *, wildcard: bool = False) -> str:
    value = str(value or "").strip().rstrip(".").casefold()
    if wildcard and value.startswith("*."):
        suffix = normalize_domain(value[2:])
        return "*." + suffix
    if not value or len(value) > 253 or not _DOMAIN_RE.fullmatch(value):
        raise ValueError(f"Ungültiger DNS-Name: {value!r}")
    labels = value.split(".")
    if any(not label or len(label.encode("idna")) > 63 for label in labels):
        raise ValueError(f"Ungültiger DNS-Name: {value!r}")
    return value


def normalize_mac(value: str) -> str:
    mac = str(value or "").strip().replace("-", ":").casefold()
    if not _MAC_RE.fullmatch(mac):
        raise ValueError(f"Ungültige MAC-Adresse: {value!r}")
    return mac


def _ip(value: Any, version: int | None = None) -> ipaddress._BaseAddress:
    address = ipaddress.ip_address(str(value).strip())
    if version is not None and address.version != version:
        raise ValueError(f"IP-Version passt nicht: {value}")
    return address


def _ipv4_list(values: Any, field: str) -> list[str]:
    result: list[str] = []
    for value in values or []:
        try:
            result.append(str(_ip(value, 4)))
        except ValueError as exc:
            raise ValueError(f"{field}: {exc}") from exc
    return result


def parse_upstream(value: str) -> tuple[str, int]:
    value = str(value or "").strip()
    if not value:
        raise ValueError("Leerer DNS-Upstream")
    host = value
    port = 53
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            raise ValueError(f"Ungültiger DNS-Upstream: {value}")
        host = value[1:closing]
        rest = value[closing + 1 :]
        if rest:
            if not rest.startswith(":"):
                raise ValueError(f"Ungültiger DNS-Upstream: {value}")
            port = int(rest[1:])
    elif value.count(":") == 1:
        maybe_host, maybe_port = value.rsplit(":", 1)
        try:
            _ip(maybe_host)
            host, port = maybe_host, int(maybe_port)
        except (ValueError, TypeError):
            host = value
            port = 53
    _ip(host)
    if not 1 <= int(port) <= 65535:
        raise ValueError(f"Ungültiger DNS-Port: {port}")
    return host, int(port)


def validate_config(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("Mini-Services-Konfiguration muss ein JSON-Objekt sein")
    config = deepcopy(DEFAULT_CONFIG)
    for section in ("dhcp", "dns"):
        supplied = candidate.get(section, {})
        if not isinstance(supplied, dict):
            raise ValueError(f"{section} muss ein Objekt sein")
        config[section].update(deepcopy(supplied))
    config["version"] = 1

    dhcp = config["dhcp"]
    dhcp["enabled"] = bool(dhcp.get("enabled"))
    dhcp["authoritative"] = bool(dhcp.get("authoritative", True))
    dhcp["ping_check"] = bool(dhcp.get("ping_check", False))
    dhcp["port"] = int(dhcp.get("port", 67))
    if not 1 <= dhcp["port"] <= 65535:
        raise ValueError("DHCP-Port muss zwischen 1 und 65535 liegen")
    dhcp["bind"] = str(_ip(dhcp.get("bind", LOOPBACK_IPV4), 4))
    dhcp["interface"] = str(dhcp.get("interface", "")).strip()[:64]
    network = ipaddress.ip_network(str(dhcp.get("network", "")), strict=False)
    if network.version != 4 or network.prefixlen > 30:
        raise ValueError("DHCP-Netz muss ein nutzbares IPv4-Netz sein")
    dhcp["network"] = str(network)
    server_ip = _ip(dhcp.get("server_ip"), 4)
    if server_ip not in network:
        raise ValueError("DHCP-Server-IP liegt nicht im DHCP-Netz")
    dhcp["server_ip"] = str(server_ip)
    start = _ip(dhcp.get("pool_start"), 4)
    end = _ip(dhcp.get("pool_end"), 4)
    if start not in network or end not in network or int(start) > int(end):
        raise ValueError("DHCP-Pool liegt nicht vollständig im Netz oder ist vertauscht")
    if start in {network.network_address, network.broadcast_address} or end in {network.network_address, network.broadcast_address}:
        raise ValueError("Netz- und Broadcast-Adresse dürfen nicht im DHCP-Pool liegen")
    dhcp["pool_start"], dhcp["pool_end"] = str(start), str(end)
    dhcp["routers"] = _ipv4_list(dhcp.get("routers"), "Router")
    dhcp["dns_servers"] = _ipv4_list(dhcp.get("dns_servers"), "DNS-Server")
    dhcp["ntp_servers"] = _ipv4_list(dhcp.get("ntp_servers"), "NTP-Server")
    for field, minimum, maximum in (
        ("lease_time", 60, 31_536_000),
        ("renewal_time", 30, 31_536_000),
        ("rebinding_time", 30, 31_536_000),
        ("decline_hold_seconds", 30, 86_400),
        ("mtu", 576, 9000),
    ):
        dhcp[field] = int(dhcp.get(field, DEFAULT_CONFIG["dhcp"][field]))
        if not minimum <= dhcp[field] <= maximum:
            raise ValueError(f"DHCP {field} liegt außerhalb des erlaubten Bereichs")
    if dhcp["renewal_time"] >= dhcp["rebinding_time"] or dhcp["rebinding_time"] >= dhcp["lease_time"]:
        raise ValueError("DHCP-Zeiten müssen T1 < T2 < Lease-Zeit erfüllen")
    dhcp["domain"] = normalize_domain(dhcp["domain"]) if str(dhcp.get("domain", "")).strip() else ""
    dhcp["domain_search"] = [normalize_domain(value) for value in dhcp.get("domain_search", []) if str(value).strip()]
    dhcp["next_server"] = str(_ip(dhcp["next_server"], 4)) if str(dhcp.get("next_server", "")).strip() else ""
    dhcp["tftp_server"] = str(dhcp.get("tftp_server", "")).strip()[:255]
    dhcp["boot_file"] = str(dhcp.get("boot_file", "")).strip()[:127]

    exclusions: list[str] = []
    for value in dhcp.get("exclusions", []):
        address = _ip(value, 4)
        if address not in network:
            raise ValueError(f"DHCP-Ausschluss liegt nicht im Netz: {address}")
        exclusions.append(str(address))
    dhcp["exclusions"] = sorted(set(exclusions), key=lambda value: int(ipaddress.ip_address(value)))

    reservations: list[dict[str, str]] = []
    reserved_ips: set[str] = set()
    for item in dhcp.get("reservations", []):
        if not isinstance(item, dict):
            raise ValueError("DHCP-Reservierung muss ein Objekt sein")
        mac = normalize_mac(item.get("mac", "")) if item.get("mac") else ""
        client_id = str(item.get("client_id", "")).strip().casefold()[:512]
        if not mac and not client_id:
            raise ValueError("DHCP-Reservierung benötigt MAC oder Client-ID")
        address = _ip(item.get("ip"), 4)
        if address not in network or address in {network.network_address, network.broadcast_address}:
            raise ValueError(f"Reservierte IP ist für dieses Netz ungültig: {address}")
        if str(address) in reserved_ips:
            raise ValueError(f"IP ist mehrfach reserviert: {address}")
        reserved_ips.add(str(address))
        hostname = str(item.get("hostname", "")).strip()[:253]
        reservations.append({"mac": mac, "client_id": client_id, "ip": str(address), "hostname": hostname})
    dhcp["reservations"] = reservations

    routes: list[dict[str, str]] = []
    for item in dhcp.get("static_routes", []):
        if not isinstance(item, dict):
            raise ValueError("Statische DHCP-Route muss ein Objekt sein")
        route_network = ipaddress.ip_network(str(item.get("network", "")), strict=False)
        if route_network.version != 4:
            raise ValueError("DHCP Classless Routes unterstützen hier nur IPv4")
        gateway = _ip(item.get("gateway"), 4)
        routes.append({"network": str(route_network), "gateway": str(gateway)})
    dhcp["static_routes"] = routes
    custom = dhcp.get("custom_options", {})
    if not isinstance(custom, dict):
        raise ValueError("DHCP custom_options muss ein Objekt sein")
    clean_custom: dict[str, str] = {}
    for key, value in custom.items():
        code = int(key)
        if code <= 0 or code >= 255 or code in {53, 54}:
            raise ValueError(f"DHCP-Option {code} ist nicht als benutzerdefinierte Option erlaubt")
        text = str(value)
        if len(text) > 2048:
            raise ValueError(f"DHCP-Option {code} ist zu lang")
        try:
            encoded = _custom_option_value(text)
        except (ValueError, TypeError, struct.error, UnicodeError) as exc:
            raise ValueError(f"DHCP-Option {code} hat eine ungültige Kodierung") from exc
        if len(encoded) > 255:
            raise ValueError(f"DHCP-Option {code} überschreitet 255 Bytes")
        clean_custom[str(code)] = text
    dhcp["custom_options"] = clean_custom

    dns = config["dns"]
    dns["enabled"] = bool(dns.get("enabled"))
    dns["cache_enabled"] = bool(dns.get("cache_enabled", True))
    dns["query_log"] = bool(dns.get("query_log", True))
    dns["port"] = int(dns.get("port", 53))
    if not 1 <= dns["port"] <= 65535:
        raise ValueError("DNS-Port muss zwischen 1 und 65535 liegen")
    binds = dns.get("bind", ["127.0.0.1"])
    if isinstance(binds, str):
        binds = [binds]
    clean_binds: list[str] = []
    for value in binds:
        if not str(value).strip():
            continue
        address = _ip(value)
        if address.is_unspecified:
            raise ValueError("DNS-Bind-Adresse darf nicht alle Netzwerkinterfaces umfassen")
        clean_binds.append(str(address))
    dns["bind"] = clean_binds
    if not dns["bind"]:
        raise ValueError("Mindestens eine DNS-Bind-Adresse ist erforderlich")
    dns["upstreams"] = [str(value).strip() for value in dns.get("upstreams", []) if str(value).strip()]
    for upstream in dns["upstreams"]:
        parse_upstream(upstream)
    if dns["enabled"] and not dns["upstreams"]:
        raise ValueError("Aktiver DNS-Dienst benötigt mindestens einen Upstream")
    dns["timeout"] = float(dns.get("timeout", 2.0))
    if not 0.2 <= dns["timeout"] <= 30:
        raise ValueError("DNS-Timeout muss zwischen 0,2 und 30 Sekunden liegen")
    dns["cache_max_entries"] = int(dns.get("cache_max_entries", 10000))
    if not 0 <= dns["cache_max_entries"] <= 200_000:
        raise ValueError("DNS-Cachegröße ist ungültig")
    dns["query_log_max_bytes"] = int(dns.get("query_log_max_bytes", 10 * 1024 * 1024))
    if not 0 <= dns["query_log_max_bytes"] <= 500 * 1024 * 1024:
        raise ValueError("DNS-Loggröße ist ungültig")
    dns["blocklist_refresh_hours"] = int(dns.get("blocklist_refresh_hours", 24))
    if not 1 <= dns["blocklist_refresh_hours"] <= 720:
        raise ValueError("Blocklisten-Intervall muss zwischen 1 und 720 Stunden liegen")
    if dns.get("block_mode") not in {"zero", "nxdomain", "refused"}:
        raise ValueError("DNS-Blockmodus muss zero, nxdomain oder refused sein")

    records: list[dict[str, Any]] = []
    for item in dns.get("records", []):
        if not isinstance(item, dict):
            raise ValueError("DNS-Eintrag muss ein Objekt sein")
        name = normalize_domain(item.get("name", ""), wildcard=True)
        record_type = str(item.get("type", "A")).upper()
        if record_type not in {"A", "AAAA", "CNAME", "TXT", "PTR", "MX", "SRV"}:
            raise ValueError(f"Nicht unterstützter lokaler DNS-Typ: {record_type}")
        value = str(item.get("value", "")).strip()
        _validate_record_value(record_type, value)
        ttl = int(item.get("ttl", 300))
        if not 0 <= ttl <= 604800:
            raise ValueError("DNS-TTL muss zwischen 0 und 604800 liegen")
        records.append({"name": name, "type": record_type, "value": value, "ttl": ttl})
    dns["records"] = records
    dns["manual_blocks"] = sorted({normalize_domain(value, wildcard=True) for value in dns.get("manual_blocks", []) if str(value).strip()})
    dns["allowlist"] = sorted({normalize_domain(value, wildcard=True) for value in dns.get("allowlist", []) if str(value).strip()})
    urls: list[str] = []
    for value in dns.get("blocklist_urls", []):
        url = str(value).strip()
        if not url:
            continue
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Blocklisten-URLs müssen HTTPS-URLs ohne Zugangsdaten/Fragment sein")
        urls.append(url)
    dns["blocklist_urls"] = urls[:20]
    return config


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path or default_config_path())
    if not target.exists():
        return deepcopy(DEFAULT_CONFIG)
    raw = _read_json(target, DEFAULT_CONFIG)
    return validate_config(raw)


def save_config(config: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path or default_config_path())
    clean = validate_config(config)
    _atomic_write(target, (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return clean


def read_status(path: str | Path | None = None) -> dict[str, Any]:
    data = _read_json(status_path(path), {})
    return data if isinstance(data, dict) else {}


def write_status(data: dict[str, Any], path: str | Path | None = None) -> None:
    _atomic_write(status_path(path), (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def read_leases(path: str | Path | None = None) -> list[dict[str, Any]]:
    data = _read_json(leases_path(path), {"leases": []})
    leases = data.get("leases", []) if isinstance(data, dict) else []
    return leases if isinstance(leases, list) else []


def tail_dns_log(path: str | Path | None = None, limit: int = 200) -> list[dict[str, Any]]:
    target = dns_log_path(path)
    limit = max(1, min(int(limit), 1000))
    try:
        with target.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 512 * 1024))
            chunk = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in chunk.splitlines()[-limit:]:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except json.JSONDecodeError:
            continue
    return list(reversed(rows))


def clear_dns_log(path: str | Path | None = None) -> None:
    target = dns_log_path(path)
    try:
        target.unlink()
    except FileNotFoundError:
        pass


def clear_leases(path: str | Path | None = None) -> None:
    _atomic_write(leases_path(path), b'{"leases": []}\n')


def _validate_record_value(record_type: str, value: str) -> None:
    if record_type == "A":
        _ip(value, 4)
    elif record_type == "AAAA":
        _ip(value, 6)
    elif record_type in {"CNAME", "PTR"}:
        normalize_domain(value)
    elif record_type == "MX":
        parts = value.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit() or not 0 <= int(parts[0]) <= 65535:
            raise ValueError("MX muss 'Priorität Hostname' enthalten")
        normalize_domain(parts[1])
    elif record_type == "SRV":
        parts = value.split()
        if len(parts) != 4 or any(not part.isdigit() for part in parts[:3]):
            raise ValueError("SRV muss 'Priorität Gewicht Port Ziel' enthalten")
        if any(not 0 <= int(part) <= 65535 for part in parts[:3]):
            raise ValueError("SRV-Zahlen liegen außerhalb 0..65535")
        normalize_domain(parts[3])
    elif record_type == "TXT" and len(value.encode("utf-8")) > 1024:
        raise ValueError("TXT-Eintrag ist zu lang")


def _encode_dns_name(name: str) -> bytes:
    name = normalize_domain(name)
    result = bytearray()
    for label in name.split("."):
        encoded = label.encode("idna")
        result.append(len(encoded))
        result.extend(encoded)
    result.append(0)
    return bytes(result)


def _encode_search_list(names: list[str]) -> bytes:
    return b"".join(_encode_dns_name(name) for name in names)


def _encode_classless_routes(routes: list[dict[str, str]]) -> bytes:
    result = bytearray()
    for route in routes:
        network = ipaddress.ip_network(route["network"], strict=False)
        prefix = network.prefixlen
        significant = (prefix + 7) // 8
        result.append(prefix)
        result.extend(network.network_address.packed[:significant])
        result.extend(ipaddress.ip_address(route["gateway"]).packed)
    return bytes(result)


def _custom_option_value(value: str) -> bytes:
    if value.startswith("hex:"):
        return bytes.fromhex(value[4:].replace(" ", ""))
    if value.startswith("base64:"):
        return base64.b64decode(value[7:], validate=True)
    if value.startswith("ip:"):
        return ipaddress.ip_address(value[3:].strip()).packed
    if value.startswith("u32:"):
        return struct.pack("!I", int(value[4:]))
    return value.removeprefix("text:").encode("utf-8")


def parse_dhcp_options(data: bytes) -> dict[int, bytes]:
    if not data.startswith(DHCP_MAGIC):
        raise ValueError("DHCP magic cookie fehlt")
    result: dict[int, bytearray] = {}
    offset = 4
    while offset < len(data):
        code = data[offset]
        offset += 1
        if code == 0:
            continue
        if code == 255:
            break
        if offset >= len(data):
            raise ValueError("Abgeschnittene DHCP-Option")
        length = data[offset]
        offset += 1
        if offset + length > len(data):
            raise ValueError("Abgeschnittene DHCP-Option")
        result.setdefault(code, bytearray()).extend(data[offset : offset + length])
        offset += length
    return {code: bytes(value) for code, value in result.items()}


def _dhcp_option(code: int, value: bytes) -> bytes:
    if not 0 < code < 255 or len(value) > 255:
        raise ValueError("Ungültige DHCP-Option")
    return bytes((code, len(value))) + value
