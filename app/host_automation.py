"""Safe host automation definitions for timers, USB, idle and background jobs."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .document_store import CONTROL_DIR

EVENT_TYPES = {"interval", "usb", "idle", "manual"}
ACTION_TYPES = {"script", "vm_start", "vm_stop", "container_start", "container_stop", "suspend", "poweroff"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,79}$")


class HostAutomationStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "host-automations.json"
        self.scripts = self.control / "host-scripts"

    def all(self) -> list[dict[str, object]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = payload.get("rules") if isinstance(payload, dict) else None
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def save(self, rule: dict[str, object]) -> dict[str, object]:
        normalized = validate_rule(rule)
        rows = [row for row in self.all() if row.get("id") != normalized["id"]]
        rows.append(normalized)
        self._write(rows)
        return normalized

    def delete(self, rule_id: str) -> None:
        self._write([row for row in self.all() if row.get("id") != rule_id])

    def _write(self, rows: list[dict[str, object]]) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "rules": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def validate_rule(rule: dict[str, object]) -> dict[str, object]:
    rule_id = str(rule.get("id") or "").strip()
    name = str(rule.get("name") or "").strip()
    trigger = str(rule.get("trigger") or "manual").strip()
    action = str(rule.get("action") or "").strip()
    if not re.fullmatch(r"[a-f0-9]{16,64}", rule_id):
        raise ValueError("Ungültige Regel-ID")
    if not NAME_RE.fullmatch(name):
        raise ValueError("Ungültiger Regelname")
    if trigger not in EVENT_TYPES or action not in ACTION_TYPES:
        raise ValueError("Unbekannter Trigger oder Aktion")
    interval = int(rule.get("interval_seconds") or 0)
    idle = int(rule.get("idle_seconds") or 0)
    if trigger == "interval" and not 60 <= interval <= 31_536_000:
        raise ValueError("Intervall muss zwischen 60 Sekunden und einem Jahr liegen")
    if trigger == "idle" and not 300 <= idle <= 86_400:
        raise ValueError("Idle-Zeit muss zwischen 5 Minuten und 24 Stunden liegen")
    target = str(rule.get("target") or "").strip()[:300]
    return {
        "id": rule_id,
        "name": name,
        "enabled": bool(rule.get("enabled", False)),
        "trigger": trigger,
        "action": action,
        "target": target,
        "interval_seconds": interval,
        "idle_seconds": idle,
        "usb_vendor": str(rule.get("usb_vendor") or "").strip()[:40],
        "usb_product": str(rule.get("usb_product") or "").strip()[:40],
        "require_no_active_users": bool(rule.get("require_no_active_users", action in {"suspend", "poweroff"})),
        "require_network_idle": bool(rule.get("require_network_idle", action in {"suspend", "poweroff"})),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def recommended_idle_rule() -> dict[str, object]:
    return {
        "trigger": "idle",
        "action": "suspend",
        "idle_seconds": 1800,
        "require_no_active_users": True,
        "require_network_idle": True,
        "description": "Nach 30 Minuten ohne aktive Benutzer und ohne relevante Netzwerknutzung in Standby wechseln.",
    }
