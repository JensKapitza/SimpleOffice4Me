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


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class RevisionHistory:
    """A local Git repository with one attributable commit per write action."""

    def __init__(self, document_root: Path):
        self.root = document_root / ".simpleoffice-history"

    def record(self, action: str, actor: str, category: str, key: str, snapshot: dict[str, Any]) -> str:
        actor = actor.strip()
        if not actor:
            raise ValueError("a named actor is required for every write action")
        if shutil.which("git") is None:
            raise RuntimeError("git is required for the revision history")
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.root / ".simpleoffice-write.lock"):
            self._git("init", "--quiet")
            _write_json(self.root / "snapshots" / category / f"{key}.json", snapshot)
            event = {
                "event_id": str(uuid.uuid4()),
                "at": datetime.now(timezone.utc).isoformat(),
                "actor": actor,
                "action": action,
                "category": category,
                "key": key,
            }
            _write_json(self.root / "events" / f"{event['at'].replace(':', '-')}-{event['event_id']}.json", event)
            self._git("add", "snapshots", "events")
            changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.root, check=False).returncode != 0
            if not changed:
                return ""
            identity = hashlib.sha256(actor.encode("utf-8")).hexdigest()[:12]
            environment = {**os.environ, "GIT_AUTHOR_NAME": actor, "GIT_AUTHOR_EMAIL": f"{identity}@simpleoffice.local", "GIT_COMMITTER_NAME": actor, "GIT_COMMITTER_EMAIL": f"{identity}@simpleoffice.local"}
            self._git("commit", "--quiet", "-m", f"{action}: {category}/{key}", env=environment)
            return self._git("rev-parse", "HEAD").strip()

    def events_for_key(self, key: str) -> list[dict[str, Any]]:
        """Read one object's revisions from Git without scanning every event file."""
        key = key.strip()
        if not key or not (self.root / ".git").is_dir():
            return []
        output = self._git(
            "log",
            "--all",
            "--fixed-strings",
            f"--grep=/{key}",
            "--format=%H%x1f%aI%x1f%an%x1f%s%x1e",
        )
        events: list[dict[str, Any]] = []
        for record in output.split("\x1e"):
            fields = record.strip().split("\x1f", 3)
            if len(fields) != 4:
                continue
            commit, occurred_at, actor, subject = fields
            action, separator, target = subject.partition(": ")
            category, slash, event_key = target.rpartition("/")
            if not separator or not slash or event_key != key:
                continue
            events.append({
                "event_id": commit,
                "commit": commit,
                "at": occurred_at,
                "actor": actor,
                "action": action,
                "category": category,
                "key": event_key,
                "source": "revision",
            })
        return events

    def snapshot_at(self, commit: str, category: str, key: str) -> dict[str, Any]:
        """Return the recorded snapshot for one already selected revision."""
        if not all(value and "\x00" not in value for value in (commit, category, key)):
            return {}
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            return {}
        if any(value in {".", ".."} or "/" in value or "\\" in value for value in (category, key)):
            return {}
        try:
            raw = self._git("show", f"{commit}:snapshots/{category}/{key}.json")
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, subprocess.CalledProcessError):
            return {}

    def _git(self, *arguments: str, env: dict[str, str] | None = None) -> str:
        # These repositories are small application audit trails. Detached auto
        # maintenance can outlive the request and race a shutdown, backup or
        # temporary test cleanup while writing ``objects``. Explicit maintenance
        # can still be run by an administrator when ever needed.
        command = ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", *arguments]
        return subprocess.run(command, cwd=self.root, env=env, check=True, capture_output=True, text=True).stdout
