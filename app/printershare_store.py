"""Cross-platform printing with explicit retention contracts.

Print payloads are never added to the document archive. ``no_store`` jobs live
only in process memory until handed to the operating-system print subsystem.
The OS/driver spooler can still buffer a job temporarily; that is surfaced in
capabilities and UI instead of being presented as a stronger guarantee.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import sqlite3
from .sqlite_utils import connect as sqlite_connect
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .document_store import CONTROL_DIR, atomic_json_write
from .file_lock import exclusive_file_lock


RETENTION_ORDER = {"no_store": 0, "ttl": 1, "permanent": 2}
RETENTION_LABELS = {
    "no_store": "Nur drucken, keine dauerhafte App-Speicherung",
    "ttl": "Zeitlich begrenzter Druck-Backlog",
    "permanent": "Druckdatei dauerhaft aufbewahren",
}
RAW_CONTENT_TYPES = {
    "application/vnd.zebra-zpl",
    "application/x-zpl",
    "application/vnd.eltron-epl",
    "application/x-epl",
    "text/x-zpl",
    "text/x-epl",
}
PRINTER_ROLES = ("standard", "label")
PRINTER_POLICY_MODES = {"none", "recommended", "forced"}
_PRINTER_ID = re.compile(r"^[0-9a-f]{24}$")
DEFAULT_BROTHER_PROFILE: dict[str, Any] = {
    "uri": "", "model": "QL-820NWB", "label": "62", "font_name": "DejaVuSans",
    "font_size": 56, "color": "black", "align": "center", "bold": False,
    "cut": True, "uri_needs_review": False,
}
MAGIC = b"SOPRINT1\x00"
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "default_retention": "no_store",
    "ttl_seconds": 24 * 60 * 60,
    "max_job_bytes": 64 * 1024 * 1024,
    "federation_enabled": False,
    "federation_default_retention": "no_store",
    "printers": {},
    "user_preferences": {},
    "user_printer_policies": {},
    "brother_profile": DEFAULT_BROTHER_PROFILE,
    "brother_profile_migrated": False,
}


def normalize_retention(value: object, default: str = "no_store") -> str:
    candidate = str(value or "").strip().casefold()
    return candidate if candidate in RETENTION_ORDER else default


def effective_retention(configured: object, ceiling: object) -> str:
    configured_value = normalize_retention(configured)
    ceiling_value = normalize_retention(ceiling, "permanent")
    return configured_value if RETENTION_ORDER[configured_value] <= RETENTION_ORDER[ceiling_value] else ceiling_value


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def printer_id(name: str) -> str:
    material = f"{platform.system().casefold()}\0{name}".encode("utf-8", "replace")
    return hashlib.sha256(material).hexdigest()[:24]


def infer_printer_kind(name: str, driver: str = "") -> str:
    value = f"{name} {driver}".casefold()
    if any(token in value for token in ("zebra", "dymo", "brother ql", "label", "etikett", "thermal", "thermo")):
        return "label"
    return "normal"


def _discover_cups() -> list[dict[str, Any]]:
    executable = shutil.which("lpstat")
    if not executable:
        return []
    result = subprocess.run([executable, "-p"], capture_output=True, text=True, timeout=10, check=False)
    if result.returncode:
        return []
    default_name = ""
    default_result = subprocess.run([executable, "-d"], capture_output=True, text=True, timeout=10, check=False)
    if default_result.returncode == 0 and ":" in default_result.stdout:
        default_name = default_result.stdout.split(":", 1)[1].strip()
    printers: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        match = re.match(r"^printer\s+(\S+)", line.strip(), re.IGNORECASE)
        if not match or re.search(r"\bdisabled\b", line, re.IGNORECASE):
            continue
        name = match.group(1)
        printers.append({
            "printer_id": printer_id(name),
            "name": name,
            "driver": "CUPS",
            "port": "",
            "is_default": name == default_name,
            "kind": infer_printer_kind(name, "CUPS"),
            "backend": "cups",
        })
    return printers


def _discover_windows() -> list[dict[str, Any]]:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not executable:
        return []
    command = (
        "Get-CimInstance Win32_Printer | "
        "Select-Object Name,DriverName,PortName,Shared,Default,WorkOffline | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        return []
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    rows = value if isinstance(value, list) else [value]
    printers = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("Name") or "").strip() \
                or row.get("WorkOffline") is True:
            continue
        name = str(row["Name"]).strip()
        driver = str(row.get("DriverName") or "")
        printers.append({
            "printer_id": printer_id(name),
            "name": name,
            "driver": driver,
            "port": str(row.get("PortName") or ""),
            "is_default": bool(row.get("Default")),
            "kind": infer_printer_kind(name, driver),
            "backend": "windows",
        })
    return printers


def discover_printers() -> list[dict[str, Any]]:
    system = platform.system()
    if system == "Windows":
        return _discover_windows()
    if system in {"Linux", "Darwin"}:
        return _discover_cups()
    return []


def _spool_cups(printer: dict[str, Any], payload: bytes, content_type: str) -> str:
    executable = shutil.which("lp")
    if executable:
        command = [executable, "-d", printer["name"]]
        if printer.get("kind") == "raw" or content_type in RAW_CONTENT_TYPES:
            command += ["-o", "raw"]
    else:
        executable = shutil.which("lpr")
        if not executable:
            raise RuntimeError("CUPS-Druckwerkzeuge lp/lpr sind nicht installiert")
        command = [executable, "-P", printer["name"]]
    result = subprocess.run(command, input=payload, capture_output=True, timeout=180, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()[-1000:]
        raise RuntimeError(detail or f"Druckkommando beendet mit {result.returncode}")
    return (result.stdout or b"").decode("utf-8", "replace").strip()[:500]


def _spool_windows_raw(printer_name: str, payload: bytes) -> str:
    import ctypes
    from ctypes import wintypes

    winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(ctypes.c_wchar_p(printer_name), ctypes.byref(handle), None):
        raise OSError(ctypes.get_last_error(), "OpenPrinterW failed")
    try:
        class DOC_INFO_1W(ctypes.Structure):
            _fields_ = [
                ("pDocName", wintypes.LPWSTR),
                ("pOutputFile", wintypes.LPWSTR),
                ("pDatatype", wintypes.LPWSTR),
            ]
        info = DOC_INFO_1W("SimpleOffice4Me", None, "RAW")
        job_id = winspool.StartDocPrinterW(handle, 1, ctypes.byref(info))
        if not job_id:
            raise OSError(ctypes.get_last_error(), "StartDocPrinterW failed")
        try:
            if not winspool.StartPagePrinter(handle):
                raise OSError(ctypes.get_last_error(), "StartPagePrinter failed")
            written = wintypes.DWORD()
            buffer = ctypes.create_string_buffer(payload)
            if not winspool.WritePrinter(handle, buffer, len(payload), ctypes.byref(written)):
                raise OSError(ctypes.get_last_error(), "WritePrinter failed")
            if int(written.value) != len(payload):
                raise OSError("Drucker hat nicht alle RAW-Daten übernommen")
            winspool.EndPagePrinter(handle)
        finally:
            winspool.EndDocPrinter(handle)
        return f"Windows RAW Job {job_id}"
    finally:
        winspool.ClosePrinter(handle)


def _suffix_for(content_type: str, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return suffix
    return {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "text/plain": ".txt",
    }.get(content_type, ".bin")


def _spool_windows(printer: dict[str, Any], payload: bytes, content_type: str, filename: str) -> str:
    if printer.get("kind") == "raw" or content_type in RAW_CONTENT_TYPES:
        return _spool_windows_raw(printer["name"], payload)
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not executable:
        raise RuntimeError("PowerShell ist für den Windows-Druck nicht verfügbar")
    temporary = tempfile.NamedTemporaryFile(prefix="simpleoffice-print-", suffix=_suffix_for(content_type, filename), delete=False)
    path = Path(temporary.name)
    try:
        temporary.write(payload)
        temporary.flush()
        temporary.close()
        env = {**os.environ, "SOPRINT_FILE": str(path), "SOPRINT_PRINTER": printer["name"]}
        command = (
            "$ErrorActionPreference='Stop'; "
            "$p=Start-Process -FilePath $env:SOPRINT_FILE -Verb PrintTo "
            "-ArgumentList ('\"'+$env:SOPRINT_PRINTER+'\"') -PassThru -Wait; "
            "if ($null -ne $p.ExitCode) { exit $p.ExitCode }"
        )
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
            env=env,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip()[-1000:] or "Windows-Druck fehlgeschlagen")
        return (result.stdout or "Windows PrintTo").strip()[:500]
    finally:
        try:
            temporary.close()
        except Exception:
            pass
        path.unlink(missing_ok=True)


def spool_payload(printer: dict[str, Any], payload: bytes, content_type: str, filename: str = "") -> str:
    system = platform.system()
    if system == "Windows":
        return _spool_windows(printer, payload, content_type, filename)
    if system in {"Linux", "Darwin"}:
        return _spool_cups(printer, payload, content_type)
    raise RuntimeError(f"Drucken wird auf {system or 'dieser Plattform'} noch nicht unterstützt")


class PrinterShareStore:
    def __init__(self, root: str | Path, secret_key: object):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.settings_path = self.control / "printershare.json"
        self.lock_path = self.control / ".printershare-write.lock"
        self.jobs_path = self.control / "printershare.sqlite3"
        self.retained = self.control / "printershare-retained"
        self.secret_key = str(secret_key)
        self._initialize()

    def _initialize(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        self.retained.mkdir(parents=True, exist_ok=True)
        if not self.settings_path.exists():
            atomic_json_write(self.settings_path, DEFAULT_SETTINGS)
        with sqlite_connect(self.jobs_path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS print_job(
                    job_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_peer TEXT NOT NULL DEFAULT '',
                    printer_id TEXT NOT NULL,
                    printer_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    retention TEXT NOT NULL,
                    expires_at INTEGER NOT NULL DEFAULT 0,
                    payload_path TEXT NOT NULL DEFAULT '',
                    content_type TEXT NOT NULL,
                    payload_size INTEGER NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    policy_revision TEXT NOT NULL DEFAULT '',
                    spool_reference TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS print_job_created_idx ON print_job(created_at DESC);
                CREATE INDEX IF NOT EXISTS print_job_expiry_idx ON print_job(expires_at);
                """
            )

    def settings(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        result = {**DEFAULT_SETTINGS}
        if isinstance(loaded, dict):
            result.update({key: loaded[key] for key in DEFAULT_SETTINGS if key in loaded})
        result["enabled"] = bool(result.get("enabled", True))
        result["federation_enabled"] = bool(result.get("federation_enabled", False))
        result["default_retention"] = normalize_retention(result.get("default_retention"))
        result["federation_default_retention"] = normalize_retention(result.get("federation_default_retention"))
        result["ttl_seconds"] = _bounded_int(result.get("ttl_seconds"), 86400, 60, 365 * 86400)
        result["max_job_bytes"] = _bounded_int(result.get("max_job_bytes"), 64 * 1024 * 1024, 1024, 1024 * 1024 * 1024)
        result["printers"] = result.get("printers") if isinstance(result.get("printers"), dict) else {}
        result["user_preferences"] = self._sanitize_user_preferences(result.get("user_preferences"))
        result["user_printer_policies"] = self._sanitize_user_policies(result.get("user_printer_policies"))
        result["brother_profile"] = self._sanitize_brother_profile(result.get("brother_profile"))
        result["brother_profile_migrated"] = bool(result.get("brother_profile_migrated", False))
        return result

    @staticmethod
    def _sanitize_brother_profile(value: object) -> dict[str, Any]:
        profile = {**DEFAULT_BROTHER_PROFILE}
        if not isinstance(value, dict):
            return profile
        profile["uri"] = str(value.get("uri") or "").strip()[:300]
        profile["model"] = str(value.get("model") or profile["model"]).strip().upper()[:40]
        profile["label"] = str(value.get("label") or profile["label"]).strip().lower()[:30]
        profile["font_name"] = " ".join(str(value.get("font_name") or profile["font_name"]).split())[:80]
        profile["font_size"] = _bounded_int(value.get("font_size"), profile["font_size"], 8, 300)
        profile["color"] = str(value.get("color") or "black").strip().lower()
        if profile["color"] not in {"black", "red"}:
            profile["color"] = "black"
        profile["align"] = str(value.get("align") or "center").strip().lower()
        if profile["align"] not in {"left", "center", "right"}:
            profile["align"] = "center"
        profile["bold"] = bool(value.get("bold", False))
        profile["cut"] = bool(value.get("cut", True))
        profile["uri_needs_review"] = bool(value.get("uri_needs_review", False))
        return profile

    def _migrate_legacy_brother_profile(self) -> None:
        if self.settings()["brother_profile_migrated"]:
            return
        from .library.store import LibraryStore

        legacy = LibraryStore(self.root).printer_settings()
        profile = self._sanitize_brother_profile(legacy)
        try:
            from .library.printer import PrinterError, validate_printer_uri

            profile["uri"] = validate_printer_uri(profile["uri"])
        except (PrinterError, ValueError):
            profile["uri"] = ""
            profile["uri_needs_review"] = True
            logging.getLogger(__name__).warning(
                "A legacy Brother printer profile requires administrator review."
            )
        with exclusive_file_lock(self.lock_path):
            data = self.settings()
            if not data["brother_profile_migrated"]:
                data["brother_profile"] = profile
                data["brother_profile_migrated"] = True
                atomic_json_write(self.settings_path, data)

    def brother_profile(self) -> dict[str, Any]:
        self._migrate_legacy_brother_profile()
        return dict(self.settings()["brother_profile"])

    def set_brother_profile(self, values: dict[str, Any]) -> dict[str, Any]:
        from .library.printer import LABELS, MODELS, PrinterError, validate_printer_uri

        profile = self._sanitize_brother_profile(values)
        if profile["model"] not in MODELS:
            raise ValueError("Unbekanntes Brother-Druckermodell.")
        if profile["label"] not in LABELS:
            raise ValueError("Unbekannte Etikettengröße.")
        try:
            profile["uri"] = validate_printer_uri(profile["uri"])
            profile["uri_needs_review"] = False
        except PrinterError as exc:
            raise ValueError(str(exc)) from exc
        with exclusive_file_lock(self.lock_path):
            data = self.settings()
            data["brother_profile"] = profile
            data["brother_profile_migrated"] = True
            atomic_json_write(self.settings_path, data)
        return profile

    @staticmethod
    def _safe_username(value: object) -> str:
        username = str(value or "").strip()
        if not username or len(username) > 64 or any(ord(char) < 32 for char in username):
            raise ValueError("Ungültiger Benutzername für Druckerpräferenz")
        return username

    @classmethod
    def _sanitize_user_preferences(cls, value: object) -> dict[str, dict[str, str]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, dict[str, str]] = {}
        for raw_username, raw_preferences in value.items():
            try:
                username = cls._safe_username(raw_username)
            except ValueError:
                continue
            if not isinstance(raw_preferences, dict):
                continue
            result[username] = {
                role: (str(raw_preferences.get(role) or "") if _PRINTER_ID.fullmatch(str(raw_preferences.get(role) or "")) else "")
                for role in PRINTER_ROLES
            }
        return result

    @classmethod
    def _sanitize_user_policies(cls, value: object) -> dict[str, dict[str, dict[str, str]]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, dict[str, dict[str, str]]] = {}
        for raw_username, raw_policies in value.items():
            try:
                username = cls._safe_username(raw_username)
            except ValueError:
                continue
            if not isinstance(raw_policies, dict):
                continue
            policies: dict[str, dict[str, str]] = {}
            for role in PRINTER_ROLES:
                raw_policy = raw_policies.get(role)
                if not isinstance(raw_policy, dict):
                    continue
                mode = str(raw_policy.get("mode") or "none").casefold()
                printer_id_value = str(raw_policy.get("printer_id") or "")
                if mode not in PRINTER_POLICY_MODES:
                    mode = "none"
                if not _PRINTER_ID.fullmatch(printer_id_value):
                    printer_id_value = ""
                if mode == "none":
                    printer_id_value = ""
                policies[role] = {"mode": mode, "printer_id": printer_id_value}
            if policies:
                result[username] = policies
        return result

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        with exclusive_file_lock(self.lock_path):
            data = self.settings()
            for key in ("enabled", "federation_enabled"):
                if key in values:
                    data[key] = bool(values[key])
            for key in ("default_retention", "federation_default_retention"):
                if key in values:
                    data[key] = normalize_retention(values[key])
            if "ttl_seconds" in values:
                data["ttl_seconds"] = _bounded_int(values["ttl_seconds"], data["ttl_seconds"], 60, 365 * 86400)
            if "max_job_bytes" in values:
                data["max_job_bytes"] = _bounded_int(values["max_job_bytes"], data["max_job_bytes"], 1024, 1024 * 1024 * 1024)
            atomic_json_write(self.settings_path, data)
        return data

    def set_printer(self, target_id: str, *, kind: str, federation_shared: bool, label: str = "") -> dict[str, Any]:
        self._migrate_legacy_brother_profile()
        with exclusive_file_lock(self.lock_path):
            data = self.settings()
            available = {item["printer_id"]: item for item in self.printers()}
            if target_id not in available:
                raise ValueError("Drucker ist aktuell nicht eingerichtet")
            if available[target_id].get("backend") == "brother" and federation_shared:
                raise ValueError("Ein lokaler Brother-Drucker kann nicht für Federation freigegeben werden")
            normalized_kind = str(kind or "auto").casefold()
            if normalized_kind not in {"auto", "normal", "label", "raw"}:
                raise ValueError("Ungültiger Druckertyp")
            data["printers"][target_id] = {
                "kind": normalized_kind,
                "federation_shared": bool(federation_shared),
                "label": " ".join(str(label or "").split())[:160],
            }
            atomic_json_write(self.settings_path, data)
        return data["printers"][target_id]

    def printers(self) -> list[dict[str, Any]]:
        settings = self.settings()
        result = []
        for item in discover_printers():
            override = settings["printers"].get(item["printer_id"], {})
            configured_kind = str(override.get("kind") or "auto")
            kind = item["kind"] if configured_kind == "auto" else configured_kind
            result.append({
                **item,
                "kind": kind,
                "configured_kind": configured_kind,
                "label": str(override.get("label") or item["name"]),
                "federation_shared": bool(override.get("federation_shared", False)),
            })
        legacy_brother = self._legacy_brother_printer()
        if legacy_brother:
            result.append(legacy_brother)
        return sorted(result, key=lambda item: (not item["is_default"], item["label"].casefold()))

    def _legacy_brother_printer(self) -> dict[str, Any] | None:
        """Expose the migrated Brother profile as a private central device."""
        settings = self.brother_profile()
        uri = str(settings.get("uri") or "").strip()
        if not uri:
            return None
        try:
            from .library.printer import validate_printer_uri

            uri = validate_printer_uri(uri)
        except (RuntimeError, ValueError) as exc:
            import logging

            logging.getLogger(__name__).warning(
                "Ignoring invalid legacy Brother printer URI (%s)", type(exc).__name__,
            )
            return None
        model = str(settings.get("model") or "QL-820NWB").strip()[:40]
        name = "Brother " + model
        return {
            "printer_id": printer_id("simpleoffice-library-brother-label"),
            "name": name,
            "driver": "SimpleOffice Brother label transport",
            "port": "",
            "is_default": False,
            "kind": "label",
            "configured_kind": "label",
            "label": name,
            "federation_shared": False,
            "backend": "brother",
            "uri": uri,
            "model": model,
        }

    @staticmethod
    def _compatible(printer: dict[str, Any], role: str) -> bool:
        kind = str(printer.get("kind") or "normal").casefold()
        return kind in {"label", "raw"} if role == "label" else kind == "normal"

    def user_printer_context(self, username: str) -> dict[str, Any]:
        """Resolve personal preferences, admin directives and safe per-role fallback."""
        username = self._safe_username(username)
        settings = self.settings()
        preferences = settings["user_preferences"].get(username, {})
        policies = settings["user_printer_policies"].get(username, {})
        printers = self.printers()
        public_printers = [
            {key: printer.get(key) for key in ("printer_id", "name", "label", "kind", "is_default", "backend")}
            for printer in printers
        ]
        roles: dict[str, dict[str, Any]] = {}
        for role in PRINTER_ROLES:
            policy = policies.get(role, {"mode": "none", "printer_id": ""})
            candidates = [printer for printer in printers if self._compatible(printer, role)]
            by_id = {printer["printer_id"]: printer for printer in candidates}
            recommended_id = policy.get("printer_id", "")
            preferred_id = preferences.get(role, "")
            selected_id = ""
            source = "unavailable"
            if policy.get("mode") == "forced":
                if recommended_id in by_id:
                    selected_id, source = recommended_id, "forced"
            elif preferred_id in by_id:
                selected_id, source = preferred_id, "personal"
            elif policy.get("mode") == "recommended" and recommended_id in by_id:
                selected_id, source = recommended_id, "recommended"
            elif candidates:
                fallback = next((printer for printer in candidates if printer.get("is_default")), candidates[0])
                selected_id, source = fallback["printer_id"], "fallback"
            selected_printer = by_id.get(selected_id)
            roles[role] = {
                "policy": policy.get("mode", "none"),
                "policy_printer_id": recommended_id,
                "policy_available": policy.get("mode") == "none" or recommended_id in by_id,
                "preference_printer_id": preferred_id,
                "printer_id": selected_id,
                "source": source,
                "available": bool(selected_id),
                "selected_label": str(selected_printer.get("label") or selected_printer.get("name") or "")
                    if selected_printer else "",
                "selected_backend": str(selected_printer.get("backend") or "") if selected_printer else "",
                "options": [
                    {key: printer.get(key) for key in ("printer_id", "label", "kind", "backend")}
                    for printer in candidates
                ],
            }
        return {"username": username, "roles": roles, "printers": public_printers}

    def set_user_preferences(
        self, username: str, *, standard_printer_id: str = "", label_printer_id: str = "",
    ) -> dict[str, str]:
        username = self._safe_username(username)
        requested = {"standard": str(standard_printer_id or ""), "label": str(label_printer_id or "")}
        self._migrate_legacy_brother_profile()
        with exclusive_file_lock(self.lock_path):
            data = self.settings()
            printers = {printer["printer_id"]: printer for printer in self.printers()}
            current = data["user_printer_policies"].get(username, {})
            normalized: dict[str, str] = {}
            for role, printer_id_value in requested.items():
                policy = current.get(role, {"mode": "none", "printer_id": ""})
                if policy.get("mode") == "forced":
                    if printer_id_value != policy.get("printer_id"):
                        raise ValueError("Die Druckerwahl ist durch eine Administrationsrichtlinie gesperrt")
                    normalized[role] = str(policy["printer_id"])
                    continue
                if printer_id_value:
                    printer = printers.get(printer_id_value)
                    if printer is None or not self._compatible(printer, role):
                        raise ValueError("Der ausgewählte Drucker ist für diese Druckart nicht verfügbar")
                normalized[role] = printer_id_value
            data["user_preferences"][username] = normalized
            atomic_json_write(self.settings_path, data)
        return normalized

    def set_user_printer_policies(
        self, username: str, policies: dict[str, dict[str, str]],
    ) -> dict[str, dict[str, str]]:
        username = self._safe_username(username)
        if not isinstance(policies, dict) or set(policies) != set(PRINTER_ROLES):
            raise ValueError("Für Standard- und Etikettendruck muss eine Richtlinie angegeben werden")
        self._migrate_legacy_brother_profile()
        with exclusive_file_lock(self.lock_path):
            data = self.settings()
            printers = {printer["printer_id"]: printer for printer in self.printers()}
            normalized: dict[str, dict[str, str]] = {}
            for role in PRINTER_ROLES:
                value = policies[role] if isinstance(policies[role], dict) else {}
                mode = str(value.get("mode") or "none").casefold()
                printer_id_value = str(value.get("printer_id") or "")
                if mode not in PRINTER_POLICY_MODES:
                    raise ValueError("Unbekannte Druckerrichtlinie")
                if mode == "none":
                    printer_id_value = ""
                elif not printer_id_value:
                    raise ValueError("Für eine aktive Druckerrichtlinie muss ein Drucker gewählt werden")
                else:
                    printer = printers.get(printer_id_value)
                    if printer is None or not self._compatible(printer, role):
                        raise ValueError("Der gewählte Drucker ist für diese Druckart nicht verfügbar")
                normalized[role] = {"mode": mode, "printer_id": printer_id_value}
            data["user_printer_policies"][username] = normalized
            atomic_json_write(self.settings_path, data)
        return normalized

    def set_user_printer_policy(
        self, username: str, role: str, mode: str, printer_id_value: str = "",
    ) -> dict[str, str]:
        """Update one role without bypassing validation or dropping the other."""
        username = self._safe_username(username)
        if role not in PRINTER_ROLES:
            raise ValueError("Unbekannte Druckerrolle")
        current = self.settings()["user_printer_policies"].get(username, {})
        policies = {
            item: dict(current.get(item, {"mode": "none", "printer_id": ""}))
            for item in PRINTER_ROLES
        }
        policies[role] = {"mode": mode, "printer_id": printer_id_value}
        return self.set_user_printer_policies(username, policies)[role]

    def resolve_user_printer(self, username: str, role: str, requested_id: str = "") -> dict[str, Any]:
        if role not in PRINTER_ROLES:
            raise ValueError("Unbekannte Druckerrolle")
        context = self.user_printer_context(username)["roles"][role]
        if context["policy"] == "forced":
            if not context["available"]:
                raise ValueError("Der erzwungene Drucker ist nicht verfügbar; bitte Administration informieren")
            if requested_id and requested_id != context["printer_id"]:
                raise ValueError("Der Druckauftrag weicht von der erzwungenen Druckerrichtlinie ab")
            selected = context["printer_id"]
        else:
            selected = str(requested_id or context["printer_id"])
        printer = next((item for item in self.printers() if item["printer_id"] == selected), None)
        if printer is None or not self._compatible(printer, role):
            raise ValueError("Kein verfügbarer Drucker für diese Druckart ausgewählt")
        return printer

    def printer(self, target_id: str, *, federation: bool = False) -> dict[str, Any]:
        item = next((printer for printer in self.printers() if printer["printer_id"] == target_id), None)
        if item is None:
            raise ValueError("Drucker ist nicht eingerichtet oder momentan nicht verfügbar")
        settings = self.settings()
        if not settings["enabled"]:
            raise ValueError("PrinterShare ist deaktiviert")
        if federation and (not settings["federation_enabled"] or not item["federation_shared"]):
            raise ValueError("Drucker ist nicht für Federation freigegeben")
        return item

    def policy_revision(self) -> str:
        settings = self.settings()
        policy = {
            "enabled": settings["enabled"],
            "federation_enabled": settings["federation_enabled"],
            "federation_default_retention": settings["federation_default_retention"],
            "ttl_seconds": settings["ttl_seconds"],
            "max_job_bytes": settings["max_job_bytes"],
            "printers": [
                {"printer_id": item["printer_id"], "kind": item["kind"], "shared": item["federation_shared"]}
                for item in self.printers() if item["federation_shared"]
            ],
        }
        encoded = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def federation_capabilities(self) -> dict[str, Any]:
        settings = self.settings()
        return {
            "schema": 1,
            "enabled": bool(settings["enabled"] and settings["federation_enabled"]),
            "policy_revision": self.policy_revision(),
            "retention_contract": {
                "ceiling_enforced": True,
                "no_store_means_no_application_archive": True,
                "payload_metadata_only_after_spool": True,
                "os_spooler_may_cache": True,
                "windows_printto_may_use_transient_temp_file": platform.system() == "Windows",
            },
            "configured_retention": settings["federation_default_retention"],
            "ttl_seconds": settings["ttl_seconds"],
            "max_job_bytes": settings["max_job_bytes"],
            "printers": [
                {
                    "printer_id": item["printer_id"],
                    "label": item["label"],
                    "kind": item["kind"],
                    "is_default": item["is_default"],
                }
                for item in self.printers() if item["federation_shared"]
            ] if settings["federation_enabled"] else [],
        }

    def purge_expired(self) -> int:
        now = int(time.time())
        removed = 0
        with sqlite_connect(self.jobs_path) as db:
            rows = db.execute(
                "SELECT job_id,payload_path FROM print_job WHERE retention='ttl' AND expires_at>0 AND expires_at<=? AND payload_path<>''",
                (now,),
            ).fetchall()
            for job_id, relative in rows:
                path = (self.control / relative).resolve()
                if self.control in path.parents:
                    path.unlink(missing_ok=True)
                db.execute("UPDATE print_job SET payload_path='' WHERE job_id=?", (job_id,))
                removed += 1
        return removed

    def jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        self.purge_expired()
        with sqlite_connect(self.jobs_path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM print_job ORDER BY created_at DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
        return [dict(row) for row in rows]

    def _key(self) -> bytes:
        material = b"simpleoffice:printershare:v1\x00" + self.secret_key.encode("utf-8", "replace")
        return hashlib.sha256(material).digest()

    def _retain(self, job_id: str, payload: bytes) -> str:
        nonce = os.urandom(12)
        aad = f"simpleoffice:print-job:{job_id}:v1".encode("utf-8")
        encrypted = AESGCM(self._key()).encrypt(nonce, payload, aad)
        relative = Path("printershare-retained") / f"{job_id}.printenc"
        destination = self.control / relative
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(MAGIC + nonce + encrypted)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, destination)
        return str(relative)

    def _load_retained(self, job_id: str, relative: str) -> bytes:
        path = (self.control / relative).resolve()
        if self.control not in path.parents or not path.is_file():
            raise ValueError("Aufbewahrte Druckdatei ist nicht mehr verfügbar")
        raw = path.read_bytes()
        if not raw.startswith(MAGIC) or len(raw) <= len(MAGIC) + 12:
            raise ValueError("Aufbewahrte Druckdatei ist beschädigt")
        nonce = raw[len(MAGIC):len(MAGIC) + 12]
        aad = f"simpleoffice:print-job:{job_id}:v1".encode("utf-8")
        return AESGCM(self._key()).decrypt(nonce, raw[len(MAGIC) + 12:], aad)

    def submit(
        self,
        target_id: str,
        payload: bytes,
        *,
        content_type: str,
        filename: str = "",
        source: str = "web",
        source_peer: str = "",
        retention_ceiling: str = "permanent",
        ttl_ceiling_seconds: int = 0,
        policy_revision: str = "",
        federation: bool = False,
    ) -> dict[str, Any]:
        settings = self.settings()
        printer = self.printer(target_id, federation=federation)
        if not payload:
            raise ValueError("Leere Druckdatei")
        if len(payload) > settings["max_job_bytes"]:
            raise ValueError(f"Druckdatei ist größer als {settings['max_job_bytes']} Byte")
        configured = settings["federation_default_retention"] if federation else settings["default_retention"]
        retention = effective_retention(configured, retention_ceiling)
        ttl_seconds = settings["ttl_seconds"]
        if ttl_ceiling_seconds > 0:
            ttl_seconds = min(ttl_seconds, _bounded_int(ttl_ceiling_seconds, ttl_seconds, 60, 365 * 86400))
        now = int(time.time())
        expires_at = now + ttl_seconds if retention == "ttl" else 0
        digest = hashlib.sha256(payload).hexdigest()
        job_id = str(uuid.uuid4())
        payload_path = self._retain(job_id, payload) if retention != "no_store" else ""
        with sqlite_connect(self.jobs_path) as db:
            db.execute(
                """INSERT INTO print_job(job_id,source,source_peer,printer_id,printer_name,status,retention,
                       expires_at,payload_path,content_type,payload_size,payload_sha256,policy_revision,created_at)
                   VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?,?,?)""",
                (
                    job_id, source[:32], source_peer[:160], target_id, printer["name"], retention,
                    expires_at, payload_path, str(content_type or "application/octet-stream")[:200], len(payload),
                    digest, policy_revision[:128], now,
                ),
            )
        try:
            spool_reference = spool_payload(printer, payload, content_type, filename)
        except Exception as exc:
            with sqlite_connect(self.jobs_path) as db:
                db.execute("UPDATE print_job SET status='failed',error=? WHERE job_id=?", (str(exc)[:2000], job_id))
            raise
        completed_at = int(time.time())
        with sqlite_connect(self.jobs_path) as db:
            db.execute(
                "UPDATE print_job SET status='spooled',spool_reference=?,completed_at=?,error='' WHERE job_id=?",
                (spool_reference[:500], completed_at, job_id),
            )
        return {
            "job_id": job_id,
            "printer_id": target_id,
            "printer": printer["label"],
            "status": "spooled",
            "retention": retention,
            "expires_at": expires_at,
            "payload_sha256": digest,
            "payload_size": len(payload),
            "policy_revision": policy_revision,
            "completed_at": completed_at,
            "application_archive": retention != "no_store",
            "os_spooler_may_cache": True,
            "spool_reference": spool_reference,
        }

    def retry(self, job_id: str) -> dict[str, Any]:
        self.purge_expired()
        with sqlite_connect(self.jobs_path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM print_job WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Unbekannter Druckauftrag")
        if not row["payload_path"]:
            raise ValueError("Für diesen Auftrag wurde keine Druckdatei aufbewahrt")
        payload = self._load_retained(job_id, row["payload_path"])
        printer = self.printer(row["printer_id"])
        reference = spool_payload(printer, payload, row["content_type"], "")
        completed_at = int(time.time())
        with sqlite_connect(self.jobs_path) as db:
            db.execute(
                "UPDATE print_job SET status='spooled',spool_reference=?,completed_at=?,error='' WHERE job_id=?",
                (reference[:500], completed_at, job_id),
            )
        return {"job_id": job_id, "status": "spooled", "spool_reference": reference}

    def delete_retained(self, job_id: str) -> None:
        with sqlite_connect(self.jobs_path) as db:
            row = db.execute("SELECT payload_path FROM print_job WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("Unbekannter Druckauftrag")
            relative = str(row[0] or "")
            if relative:
                path = (self.control / relative).resolve()
                if self.control in path.parents:
                    path.unlink(missing_ok=True)
            db.execute("UPDATE print_job SET payload_path='' WHERE job_id=?", (job_id,))
