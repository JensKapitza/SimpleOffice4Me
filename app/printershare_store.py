"""Cross-platform printing with explicit retention contracts.

Print payloads are never added to the document archive. ``no_store`` jobs live
only in process memory until handed to the operating-system print subsystem.
The OS/driver spooler can still buffer a job temporarily; that is surfaced in
capabilities and UI instead of being presented as a stronger guarantee.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
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
MAGIC = b"SOPRINT1\x00"
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "default_retention": "no_store",
    "ttl_seconds": 24 * 60 * 60,
    "max_job_bytes": 64 * 1024 * 1024,
    "federation_enabled": False,
    "federation_default_retention": "no_store",
    "printers": {},
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
        if not match:
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
        "Select-Object Name,DriverName,PortName,Shared,Default | ConvertTo-Json -Compress"
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
        if not isinstance(row, dict) or not str(row.get("Name") or "").strip():
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
        with sqlite3.connect(self.jobs_path) as db:
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
        return result

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
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
        with exclusive_file_lock(self.lock_path):
            atomic_json_write(self.settings_path, data)
        return data

    def set_printer(self, target_id: str, *, kind: str, federation_shared: bool, label: str = "") -> dict[str, Any]:
        data = self.settings()
        if target_id not in {item["printer_id"] for item in discover_printers()}:
            raise ValueError("Drucker ist aktuell nicht eingerichtet")
        normalized_kind = str(kind or "auto").casefold()
        if normalized_kind not in {"auto", "normal", "label", "raw"}:
            raise ValueError("Ungültiger Druckertyp")
        data["printers"][target_id] = {
            "kind": normalized_kind,
            "federation_shared": bool(federation_shared),
            "label": " ".join(str(label or "").split())[:160],
        }
        with exclusive_file_lock(self.lock_path):
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
        return sorted(result, key=lambda item: (not item["is_default"], item["label"].casefold()))

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
        with sqlite3.connect(self.jobs_path) as db:
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
        with sqlite3.connect(self.jobs_path) as db:
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
        with sqlite3.connect(self.jobs_path) as db:
            db.execute(
                """INSERT INTO print_job(job_id,source,source_peer,printer_id,printer_name,status,retention,
                       expires_at,payload_path,content_type,payload_size,payload_sha256,policy_revision,created_at)
                   VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?)""",
                (
                    job_id, source[:32], source_peer[:160], target_id, printer["name"], retention,
                    expires_at, payload_path, str(content_type or "application/octet-stream")[:200], len(payload),
                    digest, policy_revision[:128], now,
                ),
            )
        try:
            spool_reference = spool_payload(printer, payload, content_type, filename)
        except Exception as exc:
            with sqlite3.connect(self.jobs_path) as db:
                db.execute("UPDATE print_job SET status='failed',error=? WHERE job_id=?", (str(exc)[:2000], job_id))
            raise
        completed_at = int(time.time())
        with sqlite3.connect(self.jobs_path) as db:
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
        with sqlite3.connect(self.jobs_path) as db:
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
        with sqlite3.connect(self.jobs_path) as db:
            db.execute(
                "UPDATE print_job SET status='spooled',spool_reference=?,completed_at=?,error='' WHERE job_id=?",
                (reference[:500], completed_at, job_id),
            )
        return {"job_id": job_id, "status": "spooled", "spool_reference": reference}

    def delete_retained(self, job_id: str) -> None:
        with sqlite3.connect(self.jobs_path) as db:
            row = db.execute("SELECT payload_path FROM print_job WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("Unbekannter Druckauftrag")
            relative = str(row[0] or "")
            if relative:
                path = (self.control / relative).resolve()
                if self.control in path.parents:
                    path.unlink(missing_ok=True)
            db.execute("UPDATE print_job SET payload_path='' WHERE job_id=?", (job_id,))
