"""PXE/iPXE network boot storage and RFC 1350 TFTP service without Flask imports."""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable

from simpleoffice_mini_services import _atomic_write, default_config_path, state_dir

TFTP_RRQ = 1
TFTP_WRQ = 2
TFTP_DATA = 3
TFTP_ACK = 4
TFTP_ERROR = 5
TFTP_OACK = 6
TFTP_ERROR_NOT_FOUND = 1
TFTP_ERROR_ACCESS = 2
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,500}$")
PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
DEFAULT_BOOT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "enabled": False,
    "tftp_enabled": False,
    "tftp_bind": "127.0.0.1",
    "tftp_port": 69,
    "tftp_timeout": 3,
    "tftp_retries": 5,
    "tftp_max_file_bytes": 256 * 1024 * 1024,
    "http_base_url": "",
    "default_profile": "",
    "bios_loader": "undionly.kpxe",
    "uefi_x64_loader": "ipxe.efi",
    "uefi_arm64_loader": "ipxe-arm64.efi",
    "profiles": [],
}


def boot_root(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path or default_config_path()) / "network-boot"


def assets_root(config_path: str | Path | None = None) -> Path:
    return boot_root(config_path) / "assets"


def boot_settings_path(config_path: str | Path | None = None) -> Path:
    return boot_root(config_path) / "settings.json"


def _safe_relative(value: str, *, required: bool = True) -> str:
    value = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not value:
        if required:
            raise ValueError("Boot-Dateiname fehlt")
        return ""
    if not SAFE_COMPONENT.fullmatch(value) or ".." in Path(value).parts:
        raise ValueError("Ungültiger Boot-Dateiname")
    return value


def safe_asset_path(relative: str, config_path: str | Path | None = None, *, must_exist: bool = True) -> Path:
    relative = _safe_relative(relative)
    root = assets_root(config_path).resolve()
    path = (root / relative).resolve()
    if root not in (path, *path.parents):
        raise ValueError("Boot-Datei außerhalb des Asset-Verzeichnisses")
    if must_exist and (not path.is_file() or path.is_symlink()):
        raise ValueError("Boot-Datei nicht gefunden")
    return path


def _profile(item: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(item.get("id") or "").strip()
    if not PROFILE_ID.fullmatch(profile_id):
        raise ValueError("Bootprofil-ID ist ungültig")
    mode = str(item.get("mode") or "kernel").strip().casefold()
    if mode not in {"kernel", "iso", "chain"}:
        raise ValueError("Bootprofil-Modus muss kernel, iso oder chain sein")
    architectures = []
    for value in item.get("architectures", [0, 7, 9, 11]):
        code = int(value)
        if not 0 <= code <= 65535:
            raise ValueError("Ungültige PXE-Architektur")
        if code not in architectures:
            architectures.append(code)
    result = {
        "id": profile_id,
        "label": str(item.get("label") or profile_id).strip()[:160],
        "enabled": bool(item.get("enabled", True)),
        "mode": mode,
        "architectures": architectures,
        "kernel": _safe_relative(item.get("kernel", ""), required=False),
        "initrd": _safe_relative(item.get("initrd", ""), required=False),
        "iso": _safe_relative(item.get("iso", ""), required=False),
        "chain_url": str(item.get("chain_url") or "").strip()[:2000],
        "kernel_args": " ".join(str(item.get("kernel_args") or "").replace("\x00", "").split())[:4000],
    }
    if mode == "kernel" and not result["kernel"]:
        raise ValueError("Kernel-Bootprofil benötigt eine Kernel-Datei")
    if mode == "iso" and not result["iso"]:
        raise ValueError("ISO-Bootprofil benötigt eine ISO-Datei")
    if mode == "chain" and not result["chain_url"].startswith(("http://", "https://")):
        raise ValueError("Chain-Bootprofil benötigt eine HTTP(S)-URL")
    return result


def validate_boot_settings(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("Network-Boot-Konfiguration muss ein Objekt sein")
    data = dict(DEFAULT_BOOT_SETTINGS); data.update(candidate); data["version"] = 1
    for key in ("enabled", "tftp_enabled"):
        data[key] = bool(data.get(key))
    try:
        socket.inet_aton(str(data.get("tftp_bind") or "127.0.0.1"))
    except OSError as exc:
        raise ValueError("TFTP-Bind-Adresse muss IPv4 sein") from exc
    data["tftp_bind"] = str(data.get("tftp_bind") or "127.0.0.1")
    data["tftp_port"] = int(data.get("tftp_port", 69))
    data["tftp_timeout"] = int(data.get("tftp_timeout", 3))
    data["tftp_retries"] = int(data.get("tftp_retries", 5))
    data["tftp_max_file_bytes"] = int(data.get("tftp_max_file_bytes", 256 * 1024 * 1024))
    if not 1 <= data["tftp_port"] <= 65535:
        raise ValueError("TFTP-Port ist ungültig")
    if not 1 <= data["tftp_timeout"] <= 60 or not 1 <= data["tftp_retries"] <= 20:
        raise ValueError("TFTP Timeout/Retry liegt außerhalb des erlaubten Bereichs")
    if not 1024 <= data["tftp_max_file_bytes"] <= 4 * 1024 * 1024 * 1024:
        raise ValueError("TFTP-Dateilimit ist ungültig")
    data["http_base_url"] = str(data.get("http_base_url") or "").strip().rstrip("/")[:2000]
    if data["http_base_url"] and not data["http_base_url"].startswith(("http://", "https://")):
        raise ValueError("HTTP-Boot-Basis muss mit http:// oder https:// beginnen")
    for key in ("bios_loader", "uefi_x64_loader", "uefi_arm64_loader"):
        data[key] = _safe_relative(data.get(key, ""), required=False)
    profiles = [_profile(item) for item in data.get("profiles", []) if isinstance(item, dict)]
    if len(profiles) > 200:
        raise ValueError("Zu viele Bootprofile")
    if len({item["id"] for item in profiles}) != len(profiles):
        raise ValueError("Bootprofil-ID ist mehrfach vorhanden")
    data["profiles"] = profiles
    default_profile = str(data.get("default_profile") or "").strip()
    if default_profile and default_profile not in {item["id"] for item in profiles}:
        raise ValueError("Standard-Bootprofil existiert nicht")
    data["default_profile"] = default_profile
    return data


def load_boot_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    path = boot_settings_path(config_path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = DEFAULT_BOOT_SETTINGS
    return validate_boot_settings(value)


def save_boot_settings(candidate: dict[str, Any], config_path: str | Path | None = None) -> dict[str, Any]:
    clean = validate_boot_settings(candidate)
    _atomic_write(boot_settings_path(config_path), (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return clean


def list_assets(config_path: str | Path | None = None) -> list[dict[str, Any]]:
    root = assets_root(config_path); root.mkdir(parents=True, exist_ok=True); rows = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix(); digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        rows.append({"path": relative, "size": path.stat().st_size, "sha256": digest.hexdigest(), "mtime_ns": path.stat().st_mtime_ns})
    return sorted(rows, key=lambda row: row["path"].casefold())


def store_asset(source, filename: str, config_path: str | Path | None = None, *, max_bytes: int = 16 * 1024 * 1024 * 1024) -> dict[str, Any]:
    relative = _safe_relative(filename); target = safe_asset_path(relative, config_path, must_exist=False)
    target.parent.mkdir(parents=True, exist_ok=True); temporary = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256(); total = 0
    try:
        with temporary.open("wb") as handle:
            while True:
                block = source.read(1024 * 1024)
                if not block: break
                total += len(block)
                if total > max_bytes: raise ValueError("Boot-Datei überschreitet das Größenlimit")
                digest.update(block); handle.write(block)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True); raise
    return {"path": relative, "size": total, "sha256": digest.hexdigest()}


def remove_asset(relative: str, config_path: str | Path | None = None) -> None:
    safe_asset_path(relative, config_path).unlink()


def architecture_from_options(options: dict[int, bytes]) -> int:
    raw = options.get(93, b"")
    return struct.unpack("!H", raw[:2])[0] if len(raw) >= 2 else 0


def is_ipxe(options: dict[int, bytes]) -> bool:
    vendor = options.get(60, b"").decode("ascii", errors="ignore").casefold()
    user = options.get(77, b"").decode("ascii", errors="ignore").casefold()
    return "ipxe" in vendor or "ipxe" in user


def pxe_dhcp_values(options: dict[int, bytes], config_path: str | Path | None = None) -> tuple[str, str]:
    settings = load_boot_settings(config_path)
    if not settings["enabled"]:
        return "", ""
    if is_ipxe(options):
        base = settings["http_base_url"]
        if not base: return "", ""
        profile = settings["default_profile"]
        return "", f"{base}/network-boot/ipxe" + (f"?profile={profile}" if profile else "")
    arch = architecture_from_options(options)
    loader = settings["uefi_arm64_loader"] if arch == 11 else settings["uefi_x64_loader"] if arch in {6, 7, 9} else settings["bios_loader"]
    return "", loader


def render_ipxe(profile_id: str, config_path: str | Path | None = None, *, request_base: str = "") -> str:
    settings = load_boot_settings(config_path); profiles = {item["id"]: item for item in settings["profiles"] if item["enabled"]}
    selected = profile_id or settings["default_profile"]
    if not selected or selected not in profiles: raise ValueError("Kein aktives Bootprofil ausgewählt")
    profile = profiles[selected]; base = settings["http_base_url"] or request_base.rstrip("/")
    if not base: raise ValueError("HTTP-Basis für Networkboot fehlt")
    file_base = base + "/network-boot/files/"; lines = ["#!ipxe", f"# SimpleOffice4Me profile: {profile['label']}", "dhcp ||"]
    if profile["mode"] == "kernel":
        kernel = file_base + profile["kernel"]; args = profile["kernel_args"]
        lines.append(f"kernel {kernel}{(' ' + args) if args else ''}")
        if profile["initrd"]: lines.append(f"initrd {file_base}{profile['initrd']}")
        lines.append("boot")
    elif profile["mode"] == "iso":
        lines.extend([f"sanboot {file_base}{profile['iso']}", "exit"])
    else:
        lines.append(f"chain {profile['chain_url']}")
    return "\n".join(lines) + "\n"


def federation_manifest(config_path: str | Path | None = None) -> dict[str, Any]:
    return {"schema": "simpleoffice-network-boot/v1", "settings": load_boot_settings(config_path), "assets": list_assets(config_path), "generated_at": int(time.time())}


def _parse_rrq(packet: bytes) -> tuple[str, str, dict[str, str]]:
    if len(packet) < 4 or struct.unpack_from("!H", packet)[0] != TFTP_RRQ: raise ValueError("Keine TFTP-RRQ")
    parts = packet[2:].split(b"\0")
    if len(parts) < 3 or not parts[0] or not parts[1]: raise ValueError("Ungültige TFTP-RRQ")
    filename = parts[0].decode("utf-8", errors="strict"); mode = parts[1].decode("ascii", errors="strict").casefold(); options = {}
    values = parts[2:]; values = values[:-1] if values and values[-1] == b"" else values
    for index in range(0, len(values) - 1, 2):
        options[values[index].decode("ascii", errors="ignore").casefold()] = values[index + 1].decode("ascii", errors="ignore")
    return filename, mode, options


def _error(code: int, message: str) -> bytes:
    return struct.pack("!HH", TFTP_ERROR, code) + message.encode("ascii", errors="replace")[:200] + b"\0"


class TftpService:
    """Read-only RFC 1350 TFTP server with RFC 2347/2348/2349 options."""
    def __init__(self, settings: dict[str, Any], config_path: Path, event: Callable[[dict[str, Any]], None] | None = None):
        self.settings = validate_boot_settings(settings); self.config_path = config_path; self.event = event or (lambda _row: None)
        self.stop_event = threading.Event(); self.socket: socket.socket | None = None; self.thread: threading.Thread | None = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.settings["tftp_bind"], self.settings["tftp_port"])); sock.settimeout(1); self.socket = sock
        self.thread = threading.Thread(target=self._loop, name="simpleoffice-tftp", daemon=True); self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.socket:
            try: self.socket.close()
            except OSError: pass
        if self.thread: self.thread.join(timeout=3)

    def _loop(self) -> None:
        assert self.socket is not None
        while not self.stop_event.is_set():
            try: packet, client = self.socket.recvfrom(65535)
            except socket.timeout: continue
            except OSError: break
            if len(packet) >= 2 and struct.unpack_from("!H", packet)[0] == TFTP_WRQ:
                try: self.socket.sendto(_error(TFTP_ERROR_ACCESS, "read only"), client)
                except OSError: pass
                continue
            threading.Thread(target=self._serve_rrq, args=(packet, client), daemon=True).start()

    def _serve_rrq(self, packet: bytes, client: tuple[str, int]) -> None:
        try:
            filename, mode, requested = _parse_rrq(packet)
            if mode not in {"octet", "netascii"}: raise ValueError("unsupported mode")
            path = safe_asset_path(filename, self.config_path); size = path.stat().st_size
            if size > int(self.settings["tftp_max_file_bytes"]): raise PermissionError("file too large for TFTP; use HTTP")
            blksize = min(65464, max(8, int(requested.get("blksize", "512")))) if "blksize" in requested else 512
            timeout = min(255, max(1, int(requested.get("timeout", self.settings["tftp_timeout"]))))
            accepted = {}
            if "blksize" in requested: accepted["blksize"] = str(blksize)
            if "timeout" in requested: accepted["timeout"] = str(timeout)
            if requested.get("tsize") == "0": accepted["tsize"] = str(size)
        except PermissionError as exc:
            self._send_once(client, _error(TFTP_ERROR_ACCESS, str(exc))); return
        except (OSError, ValueError):
            self._send_once(client, _error(TFTP_ERROR_NOT_FOUND, "not found")); return
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as transfer:
            transfer.bind((self.settings["tftp_bind"], 0)); transfer.settimeout(timeout)
            if accepted:
                payload = struct.pack("!H", TFTP_OACK) + b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in accepted.items())
                if not self._exchange(transfer, client, payload, expected_ack=0): return
            with path.open("rb") as source:
                block_number = 1
                while True:
                    data = source.read(blksize); payload = struct.pack("!HH", TFTP_DATA, block_number & 0xFFFF) + data
                    if not self._exchange(transfer, client, payload, expected_ack=block_number & 0xFFFF): return
                    if len(data) < blksize: break
                    block_number += 1
            self.event({"service": "tftp", "action": "served", "client": client[0], "file": filename, "bytes": size})

    def _send_once(self, client: tuple[str, int], payload: bytes) -> None:
        if self.socket:
            try: self.socket.sendto(payload, client)
            except OSError: pass

    def _exchange(self, transfer: socket.socket, client: tuple[str, int], payload: bytes, *, expected_ack: int) -> bool:
        for _ in range(int(self.settings["tftp_retries"])):
            try:
                transfer.sendto(payload, client); reply, source = transfer.recvfrom(2048)
            except socket.timeout: continue
            except OSError: return False
            if source != client or len(reply) < 4: continue
            opcode, number = struct.unpack_from("!HH", reply)
            if opcode == TFTP_ACK and number == expected_ack: return True
            if opcode == TFTP_ERROR: return False
        return False
