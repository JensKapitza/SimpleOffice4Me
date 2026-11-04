"""Read-only desktop proxy heartbeat; shared by admin and Mini-Services UI."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

def _desktop_proxy_status(document_root):
    """Read the desktop proxy heartbeat without trusting stale process state."""
    result = {"state": "unknown", "message": "No desktop proxy status available", "origin": ""}
    path = Path(document_root) / ".simpleoffice-meta" / "desktop-proxy-status.json"
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict) or content.get("schema") != 1:
            return result
        updated = datetime.fromisoformat(str(content["updated_at"]).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
        if age < 0 or age > 20:
            return {"state": "stale", "message": "Desktop proxy status is outdated", "origin": ""}
        state = str(content.get("state", "unknown"))
        if state not in {"running", "disabled", "stopped", "error"}:
            return result
        origin = str(content.get("origin", ""))
        if len(origin) > 255 or not origin.startswith("http://"):
            origin = ""
        return {
            "state": state,
            "message": str(content.get("message", ""))[:240],
            "origin": origin if state == "running" else "",
        }
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return result


