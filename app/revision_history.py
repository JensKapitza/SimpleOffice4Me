"""Git-backed, tamper-evident revision trail for metadata and configuration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .file_lock import exclusive_file_lock

GIT_TIMEOUT_SECONDS = 30
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]{1,180}$")
_SECRET_KEY = re.compile(
    r"(?:^|[_-])(?:password|passwd|passphrase|secret|token|credential|authorization|cookie|"
    r"private[_-]?key|master[_-]?key|api[_-]?key|access[_-]?key|refresh[_-]?token|access[_-]?token)(?:$|[_-])",
    re.IGNORECASE,
)
_ERROR_WORDS = ("failed", "failure", "error", "denied", "rejected", "blocked", "malware", "invalid")
_WARNING_WORDS = ("warning", "skipped", "expired", "cancelled", "canceled", "rollback", "recovered", "missing")
_MAX_CHANGED_FIELDS = 80
_MAX_DETAIL_ITEMS = 40
_MAX_DETAIL_TEXT = 1000
_MAX_REDACTION_DEPTH = 32

_ACTION_LABELS = {
    "settings_updated": "Einstellungen geändert",
    "document_created": "Dokument erstellt",
    "document_copied": "Dokument kopiert",
    "document_restored": "Dokument wiederhergestellt",
    "document_note_added": "Dokumentnotiz hinzugefügt",
    "document_state_updated": "Dokumentstatus geändert",
    "document_tags_updated": "Dokument-Tags geändert",
    "document_attribute_updated": "Dokumentattribut geändert",
    "document_version_imported": "Dokumentversion importiert",
    "ssh_public_key_added": "SSH-Schlüssel hinzugefügt",
    "ssh_public_key_removed": "SSH-Schlüssel entfernt",
    "calendar_event_created": "Kalendereintrag erstellt",
    "calendar_event_updated": "Kalendereintrag geändert",
    "calendar_event_deleted": "Kalendereintrag gelöscht",
    "todo_created": "Aufgabe erstellt",
    "todo_updated": "Aufgabe geändert",
    "todo_deleted": "Aufgabe gelöscht",
}

_DETAIL_KEYS = {
    "document_id", "source_document_id", "destination_document_id", "contact_id", "project_id", "object_id",
    "invoice_id", "task_id", "event_id", "message_id", "request_id", "transfer_id", "peer_id", "archive_id",
    "list_id", "path", "last_path", "source", "destination", "state", "status", "outcome", "result", "error",
    "reason", "count", "counts", "size", "sha256", "previous_sha256", "updated_at", "created_at", "deleted_at",
    "restored_at", "started_at", "completed_at", "duration_ms", "audit",
}


def _path_component(value: object) -> str:
    """Return a stable single path component for externally influenced IDs."""
    raw = str(value or "").strip()
    if raw and raw not in {".", ".."} and _SAFE_COMPONENT.fullmatch(raw):
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-_")[:120] or "item"
    return f"{readable}-{digest}"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        if os.name == "posix":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _redact_secrets(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Redact obvious credentials while preserving ordinary audit snapshot content."""
    if key and _SECRET_KEY.search(key):
        return "[REDACTED]"
    if depth >= _MAX_REDACTION_DEPTH:
        return "[DEPTH-LIMIT]"
    if isinstance(value, dict):
        return {str(item_key): _redact_secrets(item_value, key=str(item_key), depth=depth + 1) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_secrets(item, depth=depth + 1) for item in value]
    if isinstance(value, tuple):
        return [_redact_secrets(item, depth=depth + 1) for item in value]
    return value


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def _changed_paths(before: Any, after: Any, prefix: str = "", depth: int = 0) -> list[str]:
    """Return bounded dotted paths that changed, without copying values into the event."""
    if before == after:
        return []
    if depth >= 8:
        return [prefix or "$"]
    if isinstance(before, dict) and isinstance(after, dict):
        result: list[str] = []
        for key in sorted(set(before) | set(after), key=str.casefold):
            path = f"{prefix}.{key}" if prefix else str(key)
            result.extend(_changed_paths(before.get(key), after.get(key), path, depth + 1))
            if len(result) >= _MAX_CHANGED_FIELDS + 1:
                break
        return result
    return [prefix or "$"]


def _compact_detail(value: Any, depth: int = 0) -> Any:
    """Bound event details so one log record cannot become an accidental data dump."""
    if depth >= 4:
        return "…"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_DETAIL_ITEMS:
                result["…"] = f"{len(value) - _MAX_DETAIL_ITEMS} weitere Einträge"
                break
            result[str(key)[:120]] = _compact_detail(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        items = list(value)
        compact = [_compact_detail(item, depth + 1) for item in items[:_MAX_DETAIL_ITEMS]]
        if len(items) > _MAX_DETAIL_ITEMS:
            compact.append(f"… {len(items) - _MAX_DETAIL_ITEMS} weitere Einträge")
        return compact
    if isinstance(value, str):
        cleaned = value.replace("\x00", "")
        return cleaned if len(cleaned) <= _MAX_DETAIL_TEXT else cleaned[:_MAX_DETAIL_TEXT] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_DETAIL_TEXT]


def _event_details(snapshot: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for key in _DETAIL_KEYS:
        if key in snapshot:
            details[key] = _compact_detail(snapshot[key])
    return details


def _outcome(action: str, snapshot: dict[str, Any]) -> str:
    explicit = str(snapshot.get("outcome", "")).casefold().strip()
    if explicit in {"success", "ok", "completed"}:
        return "success"
    if explicit in {"warning", "partial", "skipped"}:
        return "warning"
    if explicit in {"error", "failed", "failure", "denied", "rejected"}:
        return "error"
    lowered = action.casefold()
    if any(word in lowered for word in _ERROR_WORDS) or snapshot.get("error"):
        return "error"
    if any(word in lowered for word in _WARNING_WORDS):
        return "warning"
    return "success"


def _action_label(action: str) -> str:
    if action in _ACTION_LABELS:
        return _ACTION_LABELS[action]
    return " ".join(part for part in action.replace("-", "_").split("_") if part).capitalize() or "Ereignis"


def _correlation_id(snapshot: dict[str, Any]) -> str:
    for key in ("correlation_id", "request_id", "transfer_id", "message_id", "document_id", "event_id"):
        value = str(snapshot.get(key, "")).strip()
        if value:
            return value[:200]
    return ""


class RevisionHistory:
    """A local Git repository with one attributable, tamper-evident commit per write action."""

    def __init__(self, document_root: Path):
        self.root = Path(document_root).expanduser().resolve() / ".simpleoffice-history"

    def record(self, action: str, actor: str, category: str, key: str, snapshot: dict[str, Any]) -> str:
        actor = str(actor or "").strip()
        action = str(action or "").strip()
        category = str(category or "").strip()
        key = str(key or "").strip()
        if not actor:
            raise ValueError("a named actor is required for every write action")
        if not action or len(action) > 300:
            raise ValueError("a bounded audit action is required")
        if not category or len(category) > 300:
            raise ValueError("a bounded audit category is required")
        if not key or len(key) > 1000:
            raise ValueError("a bounded audit key is required")
        if not isinstance(snapshot, dict):
            raise ValueError("audit snapshot must be an object")
        if shutil.which("git") is None:
            raise RuntimeError("git is required for the revision history")

        category_component = _path_component(category)
        key_component = _path_component(key)
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.root / ".simpleoffice-write.lock"):
            self._git("init", "--quiet")
            snapshot_path = self.root / "snapshots" / category_component / f"{key_component}.json"
            previous_snapshot = _read_json(snapshot_path)
            safe_snapshot = _redact_secrets(snapshot)
            if not isinstance(safe_snapshot, dict):
                raise ValueError("audit snapshot must remain an object after redaction")
            _write_json(snapshot_path, safe_snapshot)

            changes = _changed_paths(previous_snapshot, safe_snapshot)
            changes_truncated = len(changes) > _MAX_CHANGED_FIELDS
            changes = changes[:_MAX_CHANGED_FIELDS]
            at = datetime.now(timezone.utc).isoformat()
            event_id = str(uuid.uuid4())
            outcome = _outcome(action, safe_snapshot)
            severity = "error" if outcome == "error" else "warning" if outcome == "warning" else "info"
            after_digest = _canonical_digest(safe_snapshot)
            before_digest = _canonical_digest(previous_snapshot) if previous_snapshot else ""

            chain_path = self.root / "event-chain.json"
            chain = _read_json(chain_path)
            sequence = max(0, int(chain.get("sequence", 0) or 0)) + 1
            previous_event_hash = str(chain.get("last_event_hash", ""))
            event: dict[str, Any] = {
                "schema_version": 2,
                "event_id": event_id,
                "sequence": sequence,
                "at": at,
                "actor": actor[:300],
                "action": action,
                "action_label": _action_label(action),
                "category": category,
                "key": key,
                "outcome": outcome,
                "severity": severity,
                "correlation_id": _correlation_id(safe_snapshot),
                "change_count": len(changes),
                "changed_fields": changes,
                "changes_truncated": changes_truncated,
                "snapshot_sha256": after_digest,
                "previous_snapshot_sha256": before_digest,
                "previous_event_hash": previous_event_hash,
            }
            details = _event_details(safe_snapshot)
            if details:
                event["details"] = details
            event["event_hash"] = _canonical_digest(event)
            event_name = f"{at.replace(':', '-')}-{event_id}.json"
            _write_json(self.root / "events" / event_name, event)
            _write_json(chain_path, {"schema_version": 1, "sequence": sequence, "last_event_hash": event["event_hash"], "updated_at": at})

            self._git("add", "snapshots", "events", "event-chain.json")
            changed = subprocess.run(
                ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "diff", "--cached", "--quiet"],
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            ).returncode != 0
            if not changed:
                return ""
            identity = hashlib.sha256(actor.encode("utf-8")).hexdigest()[:12]
            environment = {
                **os.environ,
                "GIT_AUTHOR_NAME": actor[:300],
                "GIT_AUTHOR_EMAIL": f"{identity}@simpleoffice.local",
                "GIT_COMMITTER_NAME": actor[:300],
                "GIT_COMMITTER_EMAIL": f"{identity}@simpleoffice.local",
                "GIT_TERMINAL_PROMPT": "0",
            }
            self._git("commit", "--quiet", "-m", f"{action[:160]}: {category_component}/{key_component}", env=environment)
            return self._git("rev-parse", "HEAD").strip()

    def verify_event_chain(self) -> dict[str, Any]:
        """Verify v2 event hashes and links; legacy events remain readable but are not chained."""
        events_dir = self.root / "events"
        if not events_dir.exists():
            return {"valid": True, "checked": 0, "legacy": 0, "errors": []}
        expected_previous = ""
        expected_sequence = 1
        checked = legacy = 0
        errors: list[str] = []
        for path in sorted(events_dir.glob("*.json")):
            event = _read_json(path)
            if int(event.get("schema_version", 0) or 0) < 2:
                legacy += 1
                continue
            checked += 1
            stored_hash = str(event.get("event_hash", ""))
            payload = dict(event)
            payload.pop("event_hash", None)
            calculated = _canonical_digest(payload)
            if not stored_hash or stored_hash != calculated:
                errors.append(f"{path.name}: event hash mismatch")
            sequence = int(event.get("sequence", 0) or 0)
            if checked == 1:
                expected_sequence = sequence
                expected_previous = str(event.get("previous_event_hash", ""))
            if sequence != expected_sequence:
                errors.append(f"{path.name}: sequence mismatch")
            if checked > 1 and str(event.get("previous_event_hash", "")) != expected_previous:
                errors.append(f"{path.name}: chain link mismatch")
            expected_previous = stored_hash
            expected_sequence = sequence + 1
        chain = _read_json(self.root / "event-chain.json")
        if checked and str(chain.get("last_event_hash", "")) != expected_previous:
            errors.append("event-chain.json: final hash mismatch")
        return {"valid": not errors, "checked": checked, "legacy": legacy, "errors": errors}

    def _git(self, *arguments: str, env: dict[str, str] | None = None) -> str:
        # These repositories are small application audit trails. Detached auto
        # maintenance can outlive the request and race a shutdown, backup or
        # temporary test cleanup while writing objects. Explicit maintenance
        # can still be run by an administrator when needed.
        command = ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", *arguments]
        environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"} if env is None else env
        try:
            result = subprocess.run(
                command,
                cwd=self.root,
                env=environment,
                stdin=subprocess.DEVNULL,
                check=True,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"revision history git command timed out: {arguments[0] if arguments else 'git'}") from exc
        return result.stdout
