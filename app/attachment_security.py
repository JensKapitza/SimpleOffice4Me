"""Confirmed MIME attachment extraction and ClamAV quarantine workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, sha256_file, utc_now
from .file_lock import exclusive_file_lock

MAX_PARTS = 100
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MANIFEST_TTL_MINUTES = 30


@dataclass(frozen=True)
class ScanResult:
    verdict: str
    detail: str
    engine: str


class QuarantineCapacityError(RuntimeError):
    """The private upload quarantine cannot safely accept another payload."""


class ClamAV:
    """Execute fixed ClamAV programs without a shell or network listener."""

    def __init__(self, timeout: int | None = None):
        configured = timeout if timeout is not None else int(os.environ.get("SIMPLEOFFICE_CLAMAV_TIMEOUT", "120"))
        self.timeout = max(5, min(configured, 900))

    def executable(self) -> str:
        requested = os.environ.get("SIMPLEOFFICE_CLAMAV_SCANNER", "").strip()
        if requested:
            candidate = Path(requested)
            scanner_name = candidate.name.casefold()
            if not candidate.is_absolute() or scanner_name not in {"clamdscan", "clamscan", "clamdscan.exe", "clamscan.exe"}:
                raise RuntimeError("SIMPLEOFFICE_CLAMAV_SCANNER must be an absolute clamdscan or clamscan path")
            if not candidate.is_file():
                raise RuntimeError("configured ClamAV scanner is unavailable")
            return str(candidate)
        found = shutil.which("clamdscan") or shutil.which("clamscan")
        if not found:
            raise RuntimeError("ClamAV is not installed or not in PATH")
        return found

    def status(self) -> dict[str, str]:
        try:
            executable = self.executable()
            result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, check=False)
            return {"state": "available" if result.returncode == 0 else "error", "engine": Path(executable).name, "version": (result.stdout or result.stderr).strip()[:300]}
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return {"state": "unavailable", "engine": "", "version": str(exc)}

    def _run_scan(self, executable: str, path: Path) -> ScanResult:
        command = [executable, "--no-summary"]
        if Path(executable).name.casefold() == "clamdscan" and os.name != "nt":
            command.append("--fdpass")
        command.append(str(path))
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ClamAV scan timed out") from exc
        detail = (result.stdout or result.stderr or "no scanner output").replace(str(path), path.name).strip()[:1000]
        if result.returncode == 0:
            return ScanResult("clean", detail, Path(executable).name)
        if result.returncode == 1:
            return ScanResult("infected", detail, Path(executable).name)
        raise RuntimeError(f"ClamAV scan failed (exit {result.returncode}): {detail}")

    def scan(self, path: Path) -> ScanResult:
        executable = self.executable()
        try:
            return self._run_scan(executable, path)
        except RuntimeError:
            if os.environ.get("SIMPLEOFFICE_CLAMAV_SCANNER", "").strip():
                raise
            if Path(executable).name.casefold() not in {"clamdscan", "clamdscan.exe"}:
                raise
            fallback = shutil.which("clamscan")
            if not fallback or Path(fallback).resolve() == Path(executable).resolve():
                raise
            return self._run_scan(fallback, path)

    def update(self) -> str:
        executable = shutil.which("freshclam")
        if not executable:
            raise RuntimeError("freshclam is not installed or not in PATH")
        result = subprocess.run([executable, "--stdout"], capture_output=True, text=True, timeout=300, check=False)
        output = (result.stdout or result.stderr).strip()[-3000:]
        if result.returncode:
            raise RuntimeError(f"freshclam failed (exit {result.returncode}): {output}")
        return output


class AttachmentSecurity:
    def __init__(self, root: str | Path, scanner: ClamAV | None = None):
        self.root = Path(root).resolve()
        self.control = self.root / CONTROL_DIR
        self.manifests = self.control / "attachment-manifests"
        self.quarantine = self.control / "quarantine"
        self.webdav_quarantine = self.control / "webdav-upload-quarantine"
        self.registry = self.control / "malware-scan.json"
        self.scanner = scanner or ClamAV()

    def scan_webdav_upload(self, payload: bytes, actor: str, target_path: str, max_quarantine_bytes: int, *, source_type: str = "webdav-put") -> dict[str, Any]:
        """Scan one untrusted PUT body before it can enter the visible tree."""
        if not actor.strip() or not target_path.strip():
            raise ValueError("a named actor and target path are required")
        self.control.mkdir(parents=True, exist_ok=True)
        if self.webdav_quarantine.exists() and (self.webdav_quarantine.is_symlink() or not self.webdav_quarantine.is_dir()):
            raise RuntimeError("WebDAV quarantine is not a safe directory")
        self.webdav_quarantine.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.webdav_quarantine, 0o700)
        except OSError:
            pass
        used = 0
        for candidate in self.webdav_quarantine.iterdir():
            if candidate.is_symlink() or not candidate.is_file():
                raise RuntimeError("WebDAV quarantine contains an unsafe entry")
            used += candidate.stat().st_size
        if len(payload) > max_quarantine_bytes or used + len(payload) > max_quarantine_bytes:
            raise QuarantineCapacityError("WebDAV quarantine capacity is exhausted")
        scan_id = uuid.uuid4().hex
        pending = self.webdav_quarantine / f"{scan_id}.pending"
        with pending.open("xb") as handle:
            os.chmod(pending, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        digest = hashlib.sha256(payload).hexdigest()
        base = {"scan_id": scan_id, "scanned_at": utc_now(), "actor": actor, "source_type": source_type, "target_path": target_path, "filename": Path(target_path).name, "size": len(payload), "sha256": digest}
        try:
            verdict = self.scanner.scan(pending)
            if verdict.verdict not in {"clean", "infected"}:
                raise RuntimeError("ClamAV returned an unsupported verdict")
            record = {**base, **asdict(verdict)}
            if verdict.verdict == "infected":
                retained = self.webdav_quarantine / f"{scan_id}.infected"
                pending.replace(retained)
                record["quarantine_id"] = retained.name
                record["action"] = "blocked_quarantined"
                self._record_scan(record)
                self._record_webdav_scan_audit("webdav_upload_malware_blocked", record)
                return record
            record["action"] = "allowed"
            self._record_scan(record)
            self._record_webdav_scan_audit("webdav_upload_malware_scanned", record)
            pending.unlink()
            return record
        except (OSError, RuntimeError) as exc:
            error_path = self.webdav_quarantine / f"{scan_id}.error"
            if pending.exists():
                pending.replace(error_path)
            record = {**base, "verdict": "error", "detail": self.safe_error_code(exc), "engine": "", "action": "scan_failed_quarantined", "quarantine_id": error_path.name if error_path.exists() else ""}
            try:
                self._record_scan(record)
                self._record_webdav_scan_audit("webdav_upload_malware_scan_failed", record)
            except OSError:
                pass
            raise RuntimeError("WebDAV upload malware scan failed") from exc

    def _record_webdav_scan_audit(self, action: str, record: dict[str, Any]) -> None:
        snapshot = {key: record.get(key) for key in ("scan_id", "scanned_at", "actor", "source_type", "target_path", "filename", "size", "sha256", "verdict", "engine", "action", "quarantine_id") if record.get(key) not in {None, ""}}
        DocumentStore(self.root).history.record(action, str(record["actor"]), "webdav-malware-scan", hashlib.sha256(f"{record['actor']}:{record['target_path']}:{record['scan_id']}".encode()).hexdigest(), snapshot)

    @staticmethod
    def _safe_name(value: str, index: int) -> str:
        value = Path(value.replace("\\", "/")).name
        value = re.sub(r"[\x00-\x1f\x7f]+", "_", value).strip(" .")
        return value[:180] or f"attachment-{index}.bin"

    def _message(self, source: Path):
        if source.suffix.casefold() != ".eml" or not source.is_file() or source.is_symlink():
            raise ValueError("source must be a regular .eml document")
        return BytesParser(policy=policy.default).parsebytes(source.read_bytes())

    def preview_eml(self, document_id: str, actor: str) -> dict[str, Any]:
        store = DocumentStore(self.root)
        document = store.get_document(document_id)
        source = self.root / document.get("last_path", "")
        message = self._message(source)
        rows, total = [], 0
        for index, part in enumerate(message.walk()):
            filename = part.get_filename()
            if part.is_multipart() or (not filename and part.get_content_disposition() != "attachment"):
                continue
            if len(rows) >= MAX_PARTS:
                raise ValueError(f"message contains more than {MAX_PARTS} attachments")
            payload = part.get_payload(decode=True) or b""
            if len(payload) > MAX_ATTACHMENT_BYTES:
                raise ValueError("an attachment exceeds the 50 MiB extraction limit")
            total += len(payload)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("decoded attachments exceed the 200 MiB total limit")
            rows.append({"part": index, "filename": self._safe_name(filename or "", index), "declared_filename": filename or "", "content_type": part.get_content_type(), "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "content_id": (part.get("Content-ID") or "").strip("<>")[:300]})
        manifest_id = uuid.uuid4().hex
        manifest = {"version": 1, "manifest_id": manifest_id, "actor": actor, "source_type": "eml", "document_id": document_id, "source_path": document.get("last_path", ""), "source_sha256": sha256_file(source), "created_at": utc_now(), "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=MANIFEST_TTL_MINUTES)).isoformat(), "message": {"message_id": (message.get("Message-ID") or "").strip()[:500], "subject": str(message.get("Subject") or "")[:500], "from": str(message.get("From") or "")[:500]}, "attachments": rows}
        self.manifests.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.manifests / f"{manifest_id}.json", manifest)
        store.history.record("attachment_extraction_previewed", actor, "documents", document_id, {"manifest_id": manifest_id, "attachments": [{"part": row["part"], "filename": row["filename"], "size": row["size"], "sha256": row["sha256"]} for row in rows]})
        return manifest

    def extract(self, manifest_id: str, selected: list[int], actor: str) -> list[dict[str, Any]]:
        path = self.manifests / f"{manifest_id}.json"
        with exclusive_file_lock(path.with_suffix(".lock")):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("extraction preview does not exist") from exc
            if manifest.get("actor") != actor:
                raise PermissionError("extraction preview belongs to another user")
            if datetime.fromisoformat(manifest["expires_at"]) < datetime.now(timezone.utc):
                raise ValueError("extraction preview has expired")
            source = self.root / manifest["source_path"]
            if sha256_file(source) != manifest["source_sha256"]:
                raise ValueError("source changed after preview; create a new preview")
            allowed = {row["part"]: row for row in manifest["attachments"]}
            chosen = sorted(set(selected))
            if not chosen or any(index not in allowed for index in chosen):
                raise ValueError("select at least one listed attachment")
            message = self._message(source)
            parts = list(message.walk())
            results = []
            self.quarantine.mkdir(parents=True, exist_ok=True)
            for index in chosen:
                row = allowed[index]
                payload = parts[index].get_payload(decode=True) or b""
                if hashlib.sha256(payload).hexdigest() != row["sha256"]:
                    raise ValueError("attachment changed after preview")
                quarantine_path = self.quarantine / f"{uuid.uuid4().hex}.pending"
                with quarantine_path.open("xb") as handle:
                    os.chmod(quarantine_path, 0o600)
                    handle.write(payload)
                try:
                    verdict = self.scanner.scan(quarantine_path)
                    record = {"scan_id": uuid.uuid4().hex, "scanned_at": utc_now(), "actor": actor, "source_type": "eml-attachment", "source_document_id": manifest["document_id"], "filename": row["filename"], "size": len(payload), "sha256": row["sha256"], **asdict(verdict)}
                    if verdict.verdict != "clean":
                        destination = self.quarantine / f"{record['scan_id']}.infected"
                        quarantine_path.replace(destination)
                        record["quarantine_id"] = destination.name
                        record["action"] = "quarantined"
                        self._record_scan(record)
                        results.append(record)
                        continue
                    record["action"] = "allowed_import"
                    self._record_scan(record)
                    with quarantine_path.open("rb") as handle:
                        imported = DocumentStore(self.root).import_upload(handle, row["filename"], actor, max_bytes=MAX_ATTACHMENT_BYTES)
                    tags = ["attachment", "source:eml", f"source-document:{manifest['document_id']}", f"source-message:{hashlib.sha256(manifest['message']['message_id'].encode()).hexdigest()[:16] if manifest['message']['message_id'] else 'unknown'}"]
                    store = DocumentStore(self.root)
                    store.set_tags(imported["document_id"], [*imported.get("tags", []), *tags], actor)
                    for key, value in {"attachment_origin": {"type": "eml", "source_document_id": manifest["document_id"], "source_path": manifest["source_path"], "message_id": manifest["message"]["message_id"], "subject": manifest["message"]["subject"], "from": manifest["message"]["from"], "mime_part": index, "content_type": row["content_type"], "sha256": row["sha256"]}, "malware_scan": record}.items():
                        store.set_attribute(imported["document_id"], key, value, actor)
                    quarantine_path.unlink(missing_ok=True)
                    results.append({**record, "document_id": imported["document_id"]})
                except Exception:
                    quarantine_path.rename(self.quarantine / f"{quarantine_path.stem}.error")
                    raise
        return results

    def _record_scan(self, record: dict[str, Any]) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.control / ".malware-scan-write.lock"):
            data = {"scans": []}
            try:
                data = json.loads(self.registry.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            data.setdefault("scans", []).append(record)
            data["scans"] = data["scans"][-5000:]
            atomic_json_write(self.registry, data)

    @staticmethod
    def safe_error_code(exc: Exception) -> str:
        message = str(exc).casefold()
        if "not installed" in message or "not in path" in message or "unavailable" in message:
            return "scanner_unavailable"
        if "timed out" in message:
            return "scanner_timeout"
        return "scanner_error"

    def record_event(self, action: str, actor: str, outcome: str, *, detail: str = "", counts: dict[str, int] | None = None, duration_ms: int | None = None) -> None:
        record: dict[str, Any] = {"event_id": uuid.uuid4().hex, "event_type": "server_action", "action": action, "occurred_at": utc_now(), "actor": actor, "outcome": outcome, "detail": str(detail)[:160]}
        if counts is not None:
            record["counts"] = {key: int(value) for key, value in counts.items()}
        if duration_ms is not None:
            record["duration_ms"] = max(0, int(duration_ms))
        self._record_scan(record)

    def scan_documents(self, actor: str, max_files: int = 10000) -> dict[str, int]:
        started = time.monotonic()
        self.record_event("server_scan", actor, "started", detail="Bestandsprüfung gestartet")
        result = {"clean": 0, "infected": 0, "errors": 0, "skipped": 0}
        for count, document in enumerate(DocumentStore(self.root)._all_documents()):
            if count >= max_files:
                result["skipped"] += 1
                continue
            path = self.root / document.get("last_path", "")
            if not path.is_file() or path.is_symlink():
                result["skipped"] += 1
                continue
            base = {"scan_id": uuid.uuid4().hex, "event_type": "file_scan", "scanned_at": utc_now(), "actor": actor, "source_type": "managed-document", "document_id": document.get("document_id", ""), "target_path": document.get("last_path", ""), "filename": path.name, "size": path.stat().st_size}
            try:
                verdict = self.scanner.scan(path)
                result[verdict.verdict] += 1
                self._record_scan({**base, "sha256": sha256_file(path), "action": "reported" if verdict.verdict == "infected" else "none", **asdict(verdict)})
            except (OSError, RuntimeError) as exc:
                result["errors"] += 1
                self._record_scan({**base, "verdict": "error", "engine": "", "action": "scan_failed", "detail": str(exc)[:1000]})
        self.record_event("server_scan", actor, "completed", detail="Bestandsprüfung abgeschlossen", counts=result, duration_ms=round((time.monotonic() - started) * 1000))
        DocumentStore(self.root).history.record("managed_documents_malware_scanned", actor, "security", "clamav", result)
        return result

    def scan_document(self, document_id: str, actor: str) -> dict[str, Any]:
        """Scan one managed document and retain an auditable verdict."""
        document = DocumentStore(self.root).get_document(document_id)
        candidate = self.root / str(document.get("last_path", ""))
        if candidate.is_symlink():
            raise ValueError("managed document file is unavailable")
        path = candidate.resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("document path is outside the managed root") from exc
        if not path.is_file() or path.is_symlink():
            raise ValueError("managed document file is unavailable")
        base = {
            "scan_id": uuid.uuid4().hex, "event_type": "file_scan",
            "scanned_at": utc_now(), "actor": actor,
            "source_type": "managed-document", "document_id": document["document_id"],
            "target_path": document.get("last_path", ""), "filename": path.name,
            "size": path.stat().st_size,
        }
        try:
            verdict = self.scanner.scan(path)
            if verdict.verdict not in {"clean", "infected"}:
                raise RuntimeError("ClamAV returned an unsupported verdict")
            record = {
                **base, "sha256": sha256_file(path),
                "action": "reported" if verdict.verdict == "infected" else "none",
                **asdict(verdict),
            }
        except (OSError, RuntimeError) as exc:
            record = {**base, "verdict": "error", "engine": "", "action": "scan_failed", "detail": str(exc)[:1000]}
        self._record_scan(record)
        DocumentStore(self.root).history.record(
            "managed_document_malware_scanned", actor, "document", document["document_id"],
            {key: record[key] for key in ("scan_id", "verdict", "engine", "action", "detail")},
        )
        return record

    def _all_recent_records(self) -> list[dict[str, Any]]:
        try:
            rows = json.loads(self.registry.read_text(encoding="utf-8")).get("scans", [])
            return list(reversed(rows))[:100]
        except (OSError, json.JSONDecodeError):
            return []

    def recent_scans(self) -> list[dict[str, Any]]:
        """Return only file-scan records; every returned row has a verdict."""
        return [row for row in self._all_recent_records() if row.get("event_type") != "server_action" and "verdict" in row]

    def recent_events(self) -> list[dict[str, Any]]:
        """Return file verdicts and server actions as one useful timeline."""
        return self._all_recent_records()
