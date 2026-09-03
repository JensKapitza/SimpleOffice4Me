"""Git-backed revision trail for document metadata and configuration."""

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
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
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


class RevisionHistory:
    """A local Git repository with one attributable commit per write action."""

    def __init__(self, document_root: Path):
        self.root = Path(document_root).expanduser().resolve() / ".simpleoffice-history"

    def record(self, action: str, actor: str, category: str, key: str, snapshot: dict[str, Any]) -> str:
        actor = str(actor or "").strip()
        if not actor:
            raise ValueError("a named actor is required for every write action")
        if shutil.which("git") is None:
            raise RuntimeError("git is required for the revision history")
        category_component = _path_component(category)
        key_component = _path_component(key)
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.root / ".simpleoffice-write.lock"):
            self._git("init", "--quiet")
            _write_json(self.root / "snapshots" / category_component / f"{key_component}.json", snapshot)
            event = {
                "event_id": str(uuid.uuid4()),
                "at": datetime.now(timezone.utc).isoformat(),
                "actor": actor[:300],
                "action": str(action)[:300],
                "category": str(category)[:300],
                "key": str(key)[:1000],
            }
            event_name = f"{event['at'].replace(':', '-')}-{event['event_id']}.json"
            _write_json(self.root / "events" / event_name, event)
            self._git("add", "snapshots", "events")
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
            self._git("commit", "--quiet", "-m", f"{str(action)[:160]}: {category_component}/{key_component}", env=environment)
            return self._git("rev-parse", "HEAD").strip()

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
