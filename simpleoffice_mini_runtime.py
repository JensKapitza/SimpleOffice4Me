"""DHCP and DNS runtime for SimpleOffice mini network services."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import struct
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from simpleoffice_mini_core import (
    DHCP_ACK,
    DHCP_DECLINE,
    DHCP_DISCOVER,
    DHCP_HEADER,
    DHCP_INFORM,
    DHCP_MAGIC,
    DHCP_NAK,
    DHCP_OFFER,
    DHCP_RELEASE,
    DHCP_REQUEST,
    DNS_TYPE_NAMES,
    DNS_TYPES,
    UNSPECIFIED_IPV4,
    _atomic_write,
    _custom_option_value,
    _dhcp_option,
    _encode_classless_routes,
    _encode_dns_name,
    _encode_search_list,
    _ip,
    _read_json,
    blocklist_path,
    dns_log_path,
    leases_path,
    parse_dhcp_options,
    parse_upstream,
    read_leases,
    utc_now,
)


class LeaseStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.leases: list[dict[str, Any]] = read_leases(path.parent.parent / "mini-services.json") if path == leases_path(path.parent.parent / "mini-services.json") else []
        if not self.leases:
            raw = _read_json(path, {"leases": []})
            self.leases = raw.get("leases", []) if isinstance(raw, dict) and isinstance(raw.get("leases", []), list) else []

    def _save(self) -> None:
        _atomic_write(self.path, (json.dumps({"version": 1, "leases": self.leases}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            before = len(self.leases)
            self.leases = [lease for lease in self.leases if float(lease.get("expires", 0)) > now]
            if len(self.leases) != before:
                self._save()

    def find_client(self, client_key: str) -> dict[str, Any] | None:
        self.cleanup()
        with self._lock:
            return next((lease for lease in self.leases if lease.get("client_key") == client_key), None)

    def ip_busy(self, address: str, client_key: str = "") -> bool:
        self.cleanup()
        with self._lock:
            return any(lease.get("ip") == address and lease.get("client_key") != client_key for lease in self.leases)

    def upsert(self, *, client_key: str, mac: str, ip: str, hostname: str, state: str, lifetime: int) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self.leases = [lease for lease in self.leases if lease.get("client_key") != client_key and lease.get("ip") != ip]
            lease = {
                "client_key": client_key,
                "mac": mac,
                "ip": ip,
                "hostname": hostname[:253],
                "state": state,
                "starts": now,
                "expires": now + lifetime,
                "updated_at": utc_now(),
            }
            self.leases.append(lease)
            self._save()
            return lease

    def release(self, client_key: str, ip: str = "") -> None:
        with self._lock:
            self.leases = [lease for lease in self.leases if not (lease.get("client_key") == client_key and (not ip or lease.get("ip") == ip))]
            self._save()

    def decline(self, client_key: str, ip: str, lifetime: int) -> None:
        self.upsert(client_key=f"declined:{ip}", mac="", ip=ip, hostname="", state="declined", lifetime=lifetime)
        self.release(client_key)


class DhcpService:
    def __init__(self, config: dict[str, Any], config_path: Path, event: Callable[[dict[str, Any]], None] | None = None):
        self.config = config
        self.config_path = config_path
        self.event = event or (lambda _row: None)
        self.stop_event = threading.Event()
        self.socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.leases = LeaseStore(leases_path(config_path))
        self.network = ipaddress.ip_network(config["network"], strict=False)
        self.server_ip = str(ipaddress.ip_address(config["server_ip"]))
        self.reservations_by_mac = {item["mac"]: item for item in config["reservations"] if item.get("mac")}
        self.reservations_by_client = {item["client_id"]: item for item in config["reservations"] if item.get("client_id")}
        self.reserved_ips = {item["ip"] for item in config["reservations"]}
        self.exclusions = set(config["exclusions"]) | {self.server_ip}

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if self.config.get("interface") and hasattr(socket, "SO_BINDTODEVICE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, self.config["interface"].encode() + b"\0")
        sock.bind((self.config["bind"], int(self.config["port"])))
        sock.settimeout(1.0)
        self.socket = sock
        self.thread = threading.Thread(target=self._loop, name="simpleoffice-dhcp", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
        if self.thread is not None:
            self.thread.join(timeout=3)

    def _loop(self) -> None:
        assert self.socket is not None
        while not self.stop_event.is_set():
            try:
                packet, source = self.socket.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                response = self.handle_packet(packet, source)
                if response is not None:
                    payload, destination = response
                    self.socket.sendto(payload, destination)
            except Exception as exc:
                self.event({"service": "dhcp", "level": "error", "error": type(exc).__name__, "message": str(exc)[:300]})

    def _reservation(self, mac: str, client_id: str) -> dict[str, str] | None:
        return self.reservations_by_client.get(client_id) or self.reservations_by_mac.get(mac)

    def _client_key(self, htype: int, mac: str, options: dict[int, bytes]) -> tuple[str, str]:
        raw = options.get(61, b"")
        client_id = raw.hex() if raw else ""
        return (f"id:{client_id}" if client_id else f"hw:{htype}:{mac}", client_id)

    def _candidate_available(self, address: str, client_key: str, reservation: dict[str, str] | None) -> bool:
        ip = ipaddress.ip_address(address)
        if ip not in self.network or ip in {self.network.network_address, self.network.broadcast_address}:
            return False
        if address in self.exclusions or self.leases.ip_busy(address, client_key):
            return False
        if address in self.reserved_ips and (reservation is None or reservation.get("ip") != address):
            return False
        if self.config.get("ping_check") and _host_responds(address):
            return False
        return True

    def _choose_ip(self, client_key: str, reservation: dict[str, str] | None, requested: str = "") -> str | None:
        if reservation:
            return reservation["ip"] if self._candidate_available(reservation["ip"], client_key, reservation) or not self.leases.ip_busy(reservation["ip"], client_key) else None
        existing = self.leases.find_client(client_key)
        if existing and existing.get("state") in {"active", "offered"} and self._candidate_available(existing["ip"], client_key, None):
            return str(existing["ip"])
        if requested and self._candidate_available(requested, client_key, None):
            start = int(ipaddress.ip_address(self.config["pool_start"]))
            end = int(ipaddress.ip_address(self.config["pool_end"]))
            if start <= int(ipaddress.ip_address(requested)) <= end:
                return requested
        start = int(ipaddress.ip_address(self.config["pool_start"]))
        end = int(ipaddress.ip_address(self.config["pool_end"]))
        for number in range(start, end + 1):
            address = str(ipaddress.ip_address(number))
            if self._candidate_available(address, client_key, None):
                return address
        return None

    def handle_packet(self, packet: bytes, source: tuple[str, int]) -> tuple[bytes, tuple[str, int]] | None:
        if len(packet) < DHCP_HEADER.size + 4:
            return None
        values = DHCP_HEADER.unpack_from(packet)
        op, htype, hlen, hops, xid, _secs, flags = values[:7]
        ciaddr, yiaddr, siaddr, giaddr, chaddr, _sname, _file = values[7:]
        if op != 1 or hlen <= 0 or hlen > 16:
            return None
        options = parse_dhcp_options(packet[DHCP_HEADER.size :])
        if 53 not in options or len(options[53]) != 1:
            return None
        message_type = options[53][0]
        mac = ":".join(f"{byte:02x}" for byte in chaddr[:hlen])
        client_key, client_id = self._client_key(htype, mac, options)
        reservation = self._reservation(mac, client_id)
        hostname = options.get(12, b"").decode("utf-8", errors="replace").strip()[:253]
        if reservation and reservation.get("hostname"):
            hostname = reservation["hostname"]
        requested = str(ipaddress.ip_address(options[50])) if len(options.get(50, b"")) == 4 else ""
        ciaddr_text = str(ipaddress.ip_address(ciaddr))
        giaddr_text = str(ipaddress.ip_address(giaddr))

        if message_type == DHCP_RELEASE:
            self.leases.release(client_key, ciaddr_text if ciaddr_text != UNSPECIFIED_IPV4 else requested)
            self.event({"service": "dhcp", "action": "release", "mac": mac, "ip": ciaddr_text})
            return None
        if message_type == DHCP_DECLINE:
            if requested:
                self.leases.decline(client_key, requested, int(self.config["decline_hold_seconds"]))
                self.event({"service": "dhcp", "action": "decline", "mac": mac, "ip": requested})
            return None
        if message_type == DHCP_REQUEST and len(options.get(54, b"")) == 4:
            selected_server = str(ipaddress.ip_address(options[54]))
            if selected_server != self.server_ip:
                return None

        assigned = ""
        response_type = 0
        if message_type == DHCP_DISCOVER:
            assigned = self._choose_ip(client_key, reservation, requested) or ""
            if not assigned:
                return None
            self.leases.upsert(client_key=client_key, mac=mac, ip=assigned, hostname=hostname, state="offered", lifetime=60)
            response_type = DHCP_OFFER
        elif message_type == DHCP_REQUEST:
            target = requested or (ciaddr_text if ciaddr_text != UNSPECIFIED_IPV4 else "")
            assigned = self._choose_ip(client_key, reservation, target) or ""
            if not assigned or (target and assigned != target):
                if not self.config.get("authoritative", True):
                    return None
                response_type = DHCP_NAK
                assigned = ""
            else:
                self.leases.upsert(client_key=client_key, mac=mac, ip=assigned, hostname=hostname, state="active", lifetime=int(self.config["lease_time"]))
                response_type = DHCP_ACK
        elif message_type == DHCP_INFORM:
            response_type = DHCP_ACK
        else:
            return None

        payload = self._reply(values, options, response_type, assigned)
        if giaddr_text != UNSPECIFIED_IPV4:
            destination = (giaddr_text, 67)
        elif response_type == DHCP_NAK or not assigned or flags & 0x8000 or ciaddr_text == UNSPECIFIED_IPV4:
            destination = ("255.255.255.255", 68)
        else:
            destination = (ciaddr_text, 68)
        self.event({"service": "dhcp", "action": {DHCP_OFFER: "offer", DHCP_ACK: "ack", DHCP_NAK: "nak"}.get(response_type, "reply"), "mac": mac, "ip": assigned, "source": source[0]})
        return payload, destination

    def _reply(self, request_values: tuple[Any, ...], request_options: dict[int, bytes], message_type: int, assigned: str) -> bytes:
        op, htype, hlen, hops, xid, _secs, flags = request_values[:7]
        ciaddr, _yiaddr, _siaddr, giaddr, chaddr, _sname, _file = request_values[7:]
        yiaddr = ipaddress.ip_address(assigned).packed if assigned else b"\0" * 4
        next_server = self.config.get("next_server") or UNSPECIFIED_IPV4
        siaddr = ipaddress.ip_address(next_server).packed
        header = DHCP_HEADER.pack(2, htype, hlen, hops, xid, 0, flags, ciaddr, yiaddr, siaddr, giaddr, chaddr, b"", b"")
        options = bytearray(DHCP_MAGIC)
        options.extend(_dhcp_option(53, bytes((message_type,))))
        options.extend(_dhcp_option(54, ipaddress.ip_address(self.server_ip).packed))
        if message_type != DHCP_NAK:
            mask = self.network.netmask.packed
            options.extend(_dhcp_option(1, mask))
            if self.config["routers"]:
                options.extend(_dhcp_option(3, b"".join(ipaddress.ip_address(value).packed for value in self.config["routers"])))
            if self.config["dns_servers"]:
                options.extend(_dhcp_option(6, b"".join(ipaddress.ip_address(value).packed for value in self.config["dns_servers"])))
            if self.config["domain"]:
                options.extend(_dhcp_option(15, self.config["domain"].encode("ascii")))
            options.extend(_dhcp_option(28, self.network.broadcast_address.packed))
            if self.config["ntp_servers"]:
                options.extend(_dhcp_option(42, b"".join(ipaddress.ip_address(value).packed for value in self.config["ntp_servers"])))
            options.extend(_dhcp_option(51, struct.pack("!I", int(self.config["lease_time"]))))
            options.extend(_dhcp_option(58, struct.pack("!I", int(self.config["renewal_time"]))))
            options.extend(_dhcp_option(59, struct.pack("!I", int(self.config["rebinding_time"]))))
            if self.config.get("mtu"):
                options.extend(_dhcp_option(26, struct.pack("!H", int(self.config["mtu"]))))
            if self.config["tftp_server"]:
                options.extend(_dhcp_option(66, self.config["tftp_server"].encode("utf-8")))
            if self.config["boot_file"]:
                options.extend(_dhcp_option(67, self.config["boot_file"].encode("utf-8")))
            if self.config["domain_search"]:
                encoded = _encode_search_list(self.config["domain_search"])
                if len(encoded) <= 255:
                    options.extend(_dhcp_option(119, encoded))
            if self.config["static_routes"]:
                encoded = _encode_classless_routes(self.config["static_routes"])
                if len(encoded) <= 255:
                    options.extend(_dhcp_option(121, encoded))
            if 82 in request_options and len(request_options[82]) <= 255:
                options.extend(_dhcp_option(82, request_options[82]))
            for code_text, value in self.config["custom_options"].items():
                code = int(code_text)
                if code in {1, 3, 6, 15, 26, 28, 42, 51, 53, 54, 58, 59, 66, 67, 82, 119, 121}:
                    continue
                raw = _custom_option_value(value)
                if len(raw) <= 255:
                    options.extend(_dhcp_option(code, raw))
        options.append(255)
        minimum = 300
        payload = header + bytes(options)
        return payload + b"\0" * max(0, minimum - len(payload))


def _host_responds(address: str) -> bool:
    try:
        result = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", "1", address],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def decode_dns_name(message: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    original_next: int | None = None
    seen: set[int] = set()
    for _ in range(128):
        if offset >= len(message):
            raise ValueError("DNS-Name abgeschnitten")
        length = message[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(message):
                raise ValueError("DNS-Kompressionszeiger abgeschnitten")
            pointer = ((length & 0x3F) << 8) | message[offset + 1]
            if pointer >= len(message) or pointer in seen:
                raise ValueError("Ungültige DNS-Namenskompression")
            seen.add(pointer)
            if original_next is None:
                original_next = offset + 2
            offset = pointer
            continue
        if length & 0xC0:
            raise ValueError("Ungültiges DNS-Label")
        offset += 1
        if length == 0:
            return ".".join(labels).casefold(), original_next if original_next is not None else offset
        if length > 63 or offset + length > len(message):
            raise ValueError("DNS-Label abgeschnitten")
        labels.append(message[offset : offset + length].decode("idna"))
        offset += length
    raise ValueError("DNS-Name zu tief verschachtelt")


def _skip_dns_rr(message: bytes, offset: int) -> tuple[int, int, int, int]:
    _name, offset = decode_dns_name(message, offset)
    if offset + 10 > len(message):
        raise ValueError("DNS-RR abgeschnitten")
    rr_type, _rr_class, ttl, rdlength = struct.unpack_from("!HHIH", message, offset)
    ttl_offset = offset + 4
    offset += 10
    if offset + rdlength > len(message):
        raise ValueError("DNS-RDATA abgeschnitten")
    return offset + rdlength, rr_type, ttl, ttl_offset


def parse_dns_query(message: bytes) -> dict[str, Any]:
    if len(message) < 12:
        raise ValueError("DNS-Header abgeschnitten")
    ident, flags, qdcount, ancount, nscount, arcount = struct.unpack_from("!HHHHHH", message)
    if flags & 0x8000:
        raise ValueError("DNS-Anfrage ist bereits eine Antwort")
    if qdcount != 1:
        raise ValueError("Es wird genau eine DNS-Frage unterstützt")
    name, offset = decode_dns_name(message, 12)
    if offset + 4 > len(message):
        raise ValueError("DNS-Frage abgeschnitten")
    qtype, qclass = struct.unpack_from("!HH", message, offset)
    question_end = offset + 4
    scan = question_end
    has_edns = False
    for _ in range(ancount + nscount + arcount):
        scan, rr_type, _ttl, _ttl_offset = _skip_dns_rr(message, scan)
        if rr_type == 41:
            has_edns = True
    return {
        "id": ident,
        "flags": flags,
        "name": name,
        "qtype": qtype,
        "qclass": qclass,
        "question_end": question_end,
        "has_edns": has_edns,
    }


def _dns_rdata(record_type: str, value: str) -> bytes:
    if record_type == "A":
        return ipaddress.ip_address(value).packed
    if record_type == "AAAA":
        return ipaddress.ip_address(value).packed
    if record_type in {"CNAME", "PTR", "NS"}:
        return _encode_dns_name(value)
    if record_type == "TXT":
        raw = value.encode("utf-8")
        chunks = [raw[index : index + 255] for index in range(0, len(raw), 255)] or [b""]
        return b"".join(bytes((len(chunk),)) + chunk for chunk in chunks)
    if record_type == "MX":
        priority, target = value.split(None, 1)
        return struct.pack("!H", int(priority)) + _encode_dns_name(target)
    if record_type == "SRV":
        priority, weight, port, target = value.split()
        return struct.pack("!HHH", int(priority), int(weight), int(port)) + _encode_dns_name(target)
    raise ValueError(f"Nicht unterstützter DNS-Typ: {record_type}")


def _dns_response(query: bytes, parsed: dict[str, Any], *, records: list[dict[str, Any]] | None = None, rcode: int = 0) -> bytes:
    answers = records or []
    request_flags = int(parsed["flags"])
    flags = 0x8000 | 0x0080 | (request_flags & 0x0100) | (rcode & 0xF)
    header = struct.pack("!HHHHHH", int(parsed["id"]), flags, 1, len(answers), 0, 0)
    result = bytearray(header + query[12 : int(parsed["question_end"])])
    for record in answers:
        record_type = str(record["type"]).upper()
        rdata = _dns_rdata(record_type, str(record["value"]))
        result.extend(b"\xc0\x0c")
        result.extend(struct.pack("!HHIH", DNS_TYPES[record_type], 1, int(record.get("ttl", 300)), len(rdata)))
        result.extend(rdata)
    return bytes(result)


def _domain_rule_matches(name: str, rule: str) -> bool:
    rule = rule.casefold().rstrip(".")
    if rule.startswith("*."):
        suffix = rule[2:]
        return name == suffix or name.endswith("." + suffix)
    return name == rule


def _response_ttl_info(message: bytes) -> tuple[int, list[tuple[int, int]], bool]:
    if len(message) < 12:
        return 0, [], False
    _ident, flags, qdcount, ancount, nscount, arcount = struct.unpack_from("!HHHHHH", message)
    offset = 12
    try:
        for _ in range(qdcount):
            _name, offset = decode_dns_name(message, offset)
            offset += 4
            if offset > len(message):
                return 0, [], False
        values: list[tuple[int, int]] = []
        soa_minimum: int | None = None
        has_opt = False
        for section, count in enumerate((ancount, nscount, arcount)):
            for _ in range(count):
                _name, rr_header = decode_dns_name(message, offset)
                if rr_header + 10 > len(message):
                    return 0, [], has_opt
                rr_type, _rr_class, ttl, rdlength = struct.unpack_from("!HHIH", message, rr_header)
                ttl_offset = rr_header + 4
                rdata_offset = rr_header + 10
                offset = rdata_offset + rdlength
                if offset > len(message):
                    return 0, [], has_opt
                if rr_type == 41:
                    has_opt = True
                    continue
                values.append((ttl_offset, ttl))
                if section == 1 and rr_type == 6 and rdlength >= 20:
                    try:
                        _mname, pos = decode_dns_name(message, rdata_offset)
                        _rname, pos = decode_dns_name(message, pos)
                        if pos + 20 <= rdata_offset + rdlength:
                            soa_minimum = struct.unpack_from("!I", message, pos + 16)[0]
                    except ValueError:
                        pass
        if flags & 0xF == 3 or (ancount == 0 and soa_minimum is not None):
            ttl = min([value for _offset, value in values] + ([soa_minimum] if soa_minimum is not None else []), default=0)
        else:
            ttl = min((value for _offset, value in values), default=0)
        return min(ttl, 86400), values, has_opt
    except (ValueError, struct.error):
        return 0, [], False


class DnsCache:
    def __init__(self, max_entries: int):
        self.max_entries = max_entries
        self._entries: OrderedDict[bytes, tuple[float, bytes, list[tuple[int, int]]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: bytes, query_id: bytes) -> bytes | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if not entry:
                return None
            stored, response, ttl_values = entry
            elapsed = int(now - stored)
            if not ttl_values or elapsed >= min(ttl for _offset, ttl in ttl_values):
                self._entries.pop(key, None)
                return None
            mutable = bytearray(response)
            mutable[0:2] = query_id
            for offset, original in ttl_values:
                struct.pack_into("!I", mutable, offset, max(0, original - elapsed))
            self._entries.move_to_end(key)
            return bytes(mutable)

    def put(self, key: bytes, response: bytes) -> None:
        if self.max_entries <= 0:
            return
        ttl, ttl_values, has_opt = _response_ttl_info(response)
        if ttl <= 0 or has_opt:
            return
        with self._lock:
            self._entries[key] = (time.monotonic(), response, ttl_values)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)


class QueryLogger:
    def __init__(self, path: Path, max_bytes: int):
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def write(self, row: dict[str, Any]) -> None:
        if self.max_bytes <= 0:
            return
        payload = (json.dumps({"at": utc_now(), **row}, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.path.exists() and self.path.stat().st_size + len(payload) > self.max_bytes:
                    with self.path.open("rb") as handle:
                        size = handle.seek(0, os.SEEK_END)
                        keep = min(size, self.max_bytes // 2)
                        handle.seek(max(0, size - keep))
                        tail = handle.read()
                    first_newline = tail.find(b"\n")
                    if first_newline >= 0:
                        tail = tail[first_newline + 1 :]
                    _atomic_write(self.path, tail)
                with self.path.open("ab") as handle:
                    handle.write(payload)
            except OSError:
                return


class DnsService:
    def __init__(self, config: dict[str, Any], config_path: Path, event: Callable[[dict[str, Any]], None] | None = None):
        self.config = config
        self.config_path = config_path
        self.event = event or (lambda _row: None)
        self.stop_event = threading.Event()
        self.sockets: list[socket.socket] = []
        self.threads: list[threading.Thread] = []
        self.cache = DnsCache(int(config.get("cache_max_entries", 10000)))
        self.logger = QueryLogger(dns_log_path(config_path), int(config.get("query_log_max_bytes", 0)))
        self.blocked = self._load_blocked()
        self._upstream_index = 0
        self._upstream_lock = threading.Lock()

    def _load_blocked(self) -> set[str]:
        result = {rule.removeprefix("*.") for rule in self.config.get("manual_blocks", [])}
        try:
            for line in blocklist_path(self.config_path).read_text(encoding="utf-8").splitlines():
                line = line.strip().casefold()
                if line:
                    result.add(line)
        except OSError:
            pass
        return result

    def start(self) -> None:
        for bind in self.config["bind"]:
            bind_address = _ip(bind)
            if bind_address.is_unspecified:
                raise ValueError("DNS-Bind-Adresse darf nicht alle Netzwerkinterfaces umfassen")
            bind = str(bind_address)
            family = socket.AF_INET6 if bind_address.version == 6 else socket.AF_INET
            udp = socket.socket(family, socket.SOCK_DGRAM)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind((bind, int(self.config["port"])))
            udp.settimeout(1.0)
            tcp = socket.socket(family, socket.SOCK_STREAM)
            tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tcp.bind((bind, int(self.config["port"])))
            tcp.listen(64)
            tcp.settimeout(1.0)
            self.sockets.extend((udp, tcp))
            udp_thread = threading.Thread(target=self._udp_loop, args=(udp,), name=f"simpleoffice-dns-udp-{bind}", daemon=True)
            tcp_thread = threading.Thread(target=self._tcp_loop, args=(tcp,), name=f"simpleoffice-dns-tcp-{bind}", daemon=True)
            self.threads.extend((udp_thread, tcp_thread))
            udp_thread.start()
            tcp_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        for sock in self.sockets:
            try:
                sock.close()
            except OSError:
                pass
        for thread in self.threads:
            thread.join(timeout=3)

    def _udp_loop(self, sock: socket.socket) -> None:
        while not self.stop_event.is_set():
            try:
                query, client = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_udp, args=(sock, query, client), daemon=True).start()

    def _handle_udp(self, sock: socket.socket, query: bytes, client: tuple[Any, ...]) -> None:
        response = self.resolve(query, str(client[0]), tcp_client=False)
        if response:
            try:
                sock.sendto(response[:65535], client)
            except OSError:
                pass

    def _tcp_loop(self, sock: socket.socket) -> None:
        while not self.stop_event.is_set():
            try:
                connection, client = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_tcp, args=(connection, client), daemon=True).start()

    def _handle_tcp(self, connection: socket.socket, client: tuple[Any, ...]) -> None:
        with connection:
            connection.settimeout(float(self.config["timeout"]) + 2)
            while not self.stop_event.is_set():
                try:
                    length_raw = _recv_exact(connection, 2)
                    if not length_raw:
                        return
                    length = struct.unpack("!H", length_raw)[0]
                    if length <= 0:
                        return
                    query = _recv_exact(connection, length)
                    if len(query) != length:
                        return
                    response = self.resolve(query, str(client[0]), tcp_client=True)
                    if not response:
                        return
                    connection.sendall(struct.pack("!H", len(response)) + response)
                except (OSError, struct.error):
                    return

    def _matching_records(self, name: str, qtype: int) -> list[dict[str, Any]]:
        exact: list[dict[str, Any]] = []
        wildcard: list[dict[str, Any]] = []
        for record in self.config["records"]:
            record_name = record["name"]
            type_code = DNS_TYPES[record["type"]]
            if qtype not in {255, type_code} and record["type"] != "CNAME":
                continue
            if record_name == name:
                exact.append(record)
            elif record_name.startswith("*.") and _domain_rule_matches(name, record_name):
                wildcard.append(record)
        return exact or wildcard

    def _is_allowed(self, name: str) -> bool:
        return any(_domain_rule_matches(name, rule) for rule in self.config.get("allowlist", []))

    def _is_blocked(self, name: str) -> bool:
        if self._is_allowed(name):
            return False
        if any(_domain_rule_matches(name, rule) for rule in self.config.get("manual_blocks", [])):
            return True
        labels = name.split(".")
        return any(".".join(labels[index:]) in self.blocked for index in range(len(labels)))

    def resolve(self, query: bytes, client: str, *, tcp_client: bool) -> bytes | None:
        started = time.monotonic()
        action = "error"
        upstream_name = ""
        rcode = 2
        name = ""
        qtype = 0
        try:
            parsed = parse_dns_query(query)
            name = parsed["name"]
            qtype = int(parsed["qtype"])
            if int(parsed["qclass"]) != 1:
                response = _dns_response(query, parsed, rcode=4)
                rcode = 4
                action = "notimp"
                return response
            records = self._matching_records(name, qtype)
            if records:
                response = _dns_response(query, parsed, records=records)
                rcode = 0
                action = "local"
                return response
            if self._is_blocked(name):
                mode = self.config.get("block_mode", "zero")
                if mode == "refused":
                    response = _dns_response(query, parsed, rcode=5)
                    rcode = 5
                elif mode == "nxdomain":
                    response = _dns_response(query, parsed, rcode=3)
                    rcode = 3
                elif qtype == DNS_TYPES["A"]:
                    response = _dns_response(query, parsed, records=[{"name": name, "type": "A", "value": UNSPECIFIED_IPV4, "ttl": 60}])
                    rcode = 0
                elif qtype == DNS_TYPES["AAAA"]:
                    response = _dns_response(query, parsed, records=[{"name": name, "type": "AAAA", "value": "::", "ttl": 60}])
                    rcode = 0
                else:
                    response = _dns_response(query, parsed, records=[])
                    rcode = 0
                action = "blocked"
                return response
            key = b"\0\0" + query[2:]
            if self.config.get("cache_enabled") and not parsed["has_edns"]:
                cached = self.cache.get(key, query[:2])
                if cached is not None:
                    rcode = cached[3] & 0x0F if len(cached) > 3 else 0
                    action = "cache"
                    return cached
            response, upstream_name = self._forward(query, force_tcp=tcp_client)
            if response is None:
                action = "servfail"
                return _dns_response(query, parsed, rcode=2)
            rcode = response[3] & 0x0F if len(response) > 3 else 2
            action = "upstream"
            if self.config.get("cache_enabled") and not parsed["has_edns"]:
                self.cache.put(key, response)
            return response
        except ValueError:
            if len(query) >= 2:
                ident = query[:2]
                flags = struct.pack("!H", 0x8001)
                return ident + flags + b"\0\0\0\0\0\0\0\0"
            return None
        finally:
            if self.config.get("query_log"):
                self.logger.write({
                    "client": client[:80],
                    "name": name[:253],
                    "type": DNS_TYPE_NAMES.get(qtype, str(qtype)),
                    "action": action,
                    "upstream": upstream_name,
                    "rcode": rcode,
                    "ms": round((time.monotonic() - started) * 1000, 2),
                })

    def _forward(self, query: bytes, *, force_tcp: bool = False) -> tuple[bytes | None, str]:
        upstreams = list(self.config["upstreams"])
        with self._upstream_lock:
            start = self._upstream_index % len(upstreams)
            self._upstream_index += 1
        ordered = upstreams[start:] + upstreams[:start]
        for upstream in ordered:
            host, port = parse_upstream(upstream)
            try:
                response = _dns_tcp_exchange(host, port, query, float(self.config["timeout"])) if force_tcp else _dns_udp_exchange(host, port, query, float(self.config["timeout"]))
                if response and response[:2] == query[:2]:
                    if not force_tcp and len(response) >= 4 and struct.unpack_from("!H", response, 2)[0] & 0x0200:
                        response = _dns_tcp_exchange(host, port, query, float(self.config["timeout"]))
                    if response and response[:2] == query[:2]:
                        return response, upstream
            except OSError:
                continue
        return None, ""


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = sock.recv(length - len(result))
        if not chunk:
            break
        result.extend(chunk)
    return bytes(result)


def _dns_udp_exchange(host: str, port: int, query: bytes, timeout: float) -> bytes:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(query, (host, port))
        response, source = sock.recvfrom(65535)
        if str(source[0]) != str(host):
            return b""
        return response


def _dns_tcp_exchange(host: str, port: int, query: bytes, timeout: float) -> bytes:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall(struct.pack("!H", len(query)) + query)
        length_raw = _recv_exact(sock, 2)
        if len(length_raw) != 2:
            return b""
        length = struct.unpack("!H", length_raw)[0]
        return _recv_exact(sock, length)
