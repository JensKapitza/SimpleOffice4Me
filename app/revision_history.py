"""Git-backed revision trail for document metadata and configuration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

    def _git(self, *arguments: str, env: dict[str, str] | None = None) -> str:
        return subprocess.run(["git", *arguments], cwd=self.root, env=env, check=True, capture_output=True, text=True).stdout
