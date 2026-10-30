"""Persistent per-actor hint visibility state (Batch 3).

Snoozed and dismissed hints stay hidden for the owning actor only. Browser
state alone never decides this; the state lives next to the domain data.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Any

from ..document_store import CONTROL_DIR, atomic_json_write, utc_now
from ..file_lock import exclusive_file_lock
from .adapters import _require_actor

STORE_FILE = "contextflow-hint-states.json"
STATES = {"visible", "snoozed", "dismissed"}

DEFAULT_QUIET_START = "22:00"
DEFAULT_QUIET_END = "07:00"
DEFAULT_DAILY_LIMIT = 3
MAX_DAILY_LIMIT = 10
BUDGET_KEEP_DAYS = 7

PREFS_FILE = "contextflow-hint-prefs.json"
BUDGET_FILE = "contextflow-hint-budget.json"

# File locks do not exclude threads of the same process, so budget
# writes take this process lock first (threaded web servers).
_BUDGET_WRITE_LOCK = threading.Lock()


def parse_hhmm(value: str) -> tuple[int, int]:
    """Parse a strict HH:MM clock time; garbage raises ValueError."""
    parts = str(value or "").strip().split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError("time must be HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if hour > 23 or minute > 59:
        raise ValueError("time must be HH:MM")
    return hour, minute


def is_quiet_time(
    now_hhmm: str,
    start: str = DEFAULT_QUIET_START,
    end: str = DEFAULT_QUIET_END,
) -> bool:
    """Decide whether a clock time falls into the quiet window.

    Overnight windows (start > end) wrap past midnight. Equal start
    and end disable quiet hours entirely (never quiet).
    """
    now = parse_hhmm(now_hhmm)
    begin = parse_hhmm(start)
    stop = parse_hhmm(end)
    if begin == stop:
        return False
    if begin < stop:
        return begin <= now < stop
    return now >= begin or now < stop


class HintStates:
    """JSON-backed actor-scoped hint visibility."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.path = self.root / CONTROL_DIR / STORE_FILE
        self.lock = self.root / CONTROL_DIR / ".contextflow-hint-states-write.lock"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "states": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "states": []}
        if not isinstance(data, dict):
            return {"version": 1, "states": []}
        if not isinstance(data.get("states"), list):
            data["states"] = []
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.path, data)

    def get(self, actor: str, link_id: str) -> dict[str, Any]:
        """Return the actor's state for a link; visible by default."""
        actor = _require_actor(actor)
        for row in self._read().get("states", []):
            if (
                isinstance(row, dict)
                and row.get("owner") == actor
                and row.get("link_id") == link_id
            ):
                return dict(row)
        return {"owner": actor, "link_id": link_id, "state": "visible", "until": ""}

    def set(
        self, actor: str, link_id: str, state: str, until: str = ""
    ) -> dict[str, Any]:
        """Persist a visibility state for the actor's own link."""
        actor = _require_actor(actor)
        if state not in STATES:
            raise ValueError("unknown hint state")
        until = str(until or "").strip()
        if until:
            try:
                date.fromisoformat(until)
            except ValueError:
                raise ValueError("until must be an ISO date (YYYY-MM-DD)") from None
        if not isinstance(link_id, str) or not link_id.strip():
            raise ValueError("link reference is required")
        row = {
            "owner": actor,
            "link_id": link_id,
            "state": state,
            "until": until,
            "updated_at": utc_now(),
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            states = data["states"]
            for index, existing in enumerate(states):
                if (
                    isinstance(existing, dict)
                    and existing.get("owner") == actor
                    and existing.get("link_id") == link_id
                ):
                    states[index] = row
                    break
            else:
                states.append(row)
            self._write(data)
        return dict(row)

    def is_visible(self, actor: str, link_id: str, today: str = "") -> bool:
        """Decide visibility for one link without leaking other actors."""
        current = self.get(actor, link_id)
        state = str(current.get("state", "visible"))
        if state == "dismissed":
            return False
        if state == "snoozed":
            until = str(current.get("until", "") or "")
            if until and (not today or until >= today):
                return False
        return True


class HintPrefs:
    """Per-actor quiet hours and hints-per-day (dashboard control)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.path = self.root / CONTROL_DIR / PREFS_FILE
        self.lock = self.root / CONTROL_DIR / ".contextflow-hint-prefs-write.lock"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "prefs": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "prefs": []}
        if not isinstance(data, dict):
            return {"version": 1, "prefs": []}
        if not isinstance(data.get("prefs"), list):
            data["prefs"] = []
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.path, data)

    def get(self, actor: str) -> dict[str, Any]:
        """Return the actor's prefs; missing actors get the defaults."""
        actor = _require_actor(actor)
        for row in self._read().get("prefs", []):
            if isinstance(row, dict) and row.get("owner") == actor:
                try:
                    return {
                        "quiet_start": "%02d:%02d" % parse_hhmm(row.get("quiet_start", "")),
                        "quiet_end": "%02d:%02d" % parse_hhmm(row.get("quiet_end", "")),
                        "daily_limit": self._checked_limit(row.get("daily_limit")),
                    }
                except ValueError:
                    break
        return {
            "quiet_start": DEFAULT_QUIET_START,
            "quiet_end": DEFAULT_QUIET_END,
            "daily_limit": DEFAULT_DAILY_LIMIT,
        }

    @staticmethod
    def _checked_limit(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            try:
                value = int(str(value).strip())
            except (TypeError, ValueError):
                raise ValueError("daily limit must be 1-10") from None
        if isinstance(value, bool) or not 1 <= value <= MAX_DAILY_LIMIT:
            raise ValueError("daily limit must be 1-10")
        return value

    def set(
        self,
        actor: str,
        quiet_start: str,
        quiet_end: str,
        daily_limit: Any,
    ) -> dict[str, Any]:
        """Persist validated prefs for the actor's own account."""
        actor = _require_actor(actor)
        start = "%02d:%02d" % parse_hhmm(quiet_start)
        end = "%02d:%02d" % parse_hhmm(quiet_end)
        limit = self._checked_limit(daily_limit)
        row = {
            "owner": actor,
            "quiet_start": start,
            "quiet_end": end,
            "daily_limit": limit,
            "updated_at": utc_now(),
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            prefs = data["prefs"]
            for index, existing in enumerate(prefs):
                if isinstance(existing, dict) and existing.get("owner") == actor:
                    prefs[index] = row
                    break
            else:
                prefs.append(row)
            self._write(data)
        return {"quiet_start": start, "quiet_end": end, "daily_limit": limit}


class HintBudget:
    """Per-actor daily log of shown hints (digest list source)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.path = self.root / CONTROL_DIR / BUDGET_FILE
        self.lock = self.root / CONTROL_DIR / ".contextflow-hint-budget-write.lock"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "days": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "days": []}
        if not isinstance(data, dict):
            return {"version": 1, "days": []}
        if not isinstance(data.get("days"), list):
            data["days"] = []
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.path, data)

    @staticmethod
    def _checked_day(today: str) -> str:
        try:
            return date.fromisoformat(str(today).strip()).isoformat()
        except ValueError:
            raise ValueError("day must be an ISO date (YYYY-MM-DD)") from None

    def shown_today(self, actor: str, today: str) -> list[str]:
        """Return the actor's already-shown link ids for one day."""
        actor = _require_actor(actor)
        day = self._checked_day(today)
        for row in self._read().get("days", []):
            if (
                isinstance(row, dict)
                and row.get("owner") == actor
                and row.get("day") == day
            ):
                shown = row.get("shown", [])
                return [str(item) for item in shown if str(item).strip()]
        return []

    def allow(self, actor: str, link_id: str, today: str, limit: int) -> bool:
        """Decide whether one more hint may render today.

        Already-shown links stay allowed (stable re-renders); new
        links render only while fewer than ``limit`` distinct links
        were shown. Garbage input fails closed (no render).
        """
        try:
            actor = _require_actor(actor)
            day = self._checked_day(today)
            link = str(link_id or "").strip()
            if not link or isinstance(limit, bool) or int(limit) < 1:
                return False
            shown = self.shown_today(actor, day)
        except (ValueError, TypeError):
            return False
        if link in shown:
            return True
        return len(shown) < int(limit)

    @staticmethod
    def _shown_of(mine: dict[str, Any]) -> list[str]:
        """Return the row's shown ids; a damaged value reads as empty."""
        shown = mine.get("shown", [])
        if not isinstance(shown, list):
            return []
        return [str(item) for item in shown if str(item).strip()]

    @staticmethod
    def _prune(data: dict[str, Any], actor: str) -> None:
        """Keep only the actor's newest days; drop unreadable rows."""
        rows = [row for row in data["days"] if isinstance(row, dict)]
        mine_days = sorted(
            {str(row.get("day", "")) for row in rows if row.get("owner") == actor}
        )[-BUDGET_KEEP_DAYS:]
        data["days"] = [
            row
            for row in rows
            if row.get("owner") != actor or str(row.get("day", "")) in mine_days
        ]

    def _mine(self, data: dict[str, Any], actor: str, day: str) -> dict[str, Any]:
        """Return the actor's day row, creating it when missing."""
        mine: dict[str, Any] | None = None
        for row in data["days"]:
            if (
                isinstance(row, dict)
                and row.get("owner") == actor
                and row.get("day") == day
            ):
                mine = row
                break
        if mine is None:
            mine = {"owner": actor, "day": day, "shown": []}
            data["days"].append(mine)
        return mine

    def record(self, actor: str, link_ids: list[str], today: str) -> list[str]:
        """Append rendered link ids to the actor's day; prune old days."""
        actor = _require_actor(actor)
        day = self._checked_day(today)
        fresh = [str(item).strip() for item in link_ids if str(item).strip()]
        with _BUDGET_WRITE_LOCK:
            with exclusive_file_lock(self.lock):
                data = self._read()
                mine = self._mine(data, actor, day)
                seen = self._shown_of(mine)
                for link_id in fresh:
                    if link_id not in seen:
                        seen.append(link_id)
                mine["shown"] = seen
                self._prune(data, actor)
                self._write(data)
        return list(seen)

    def reserve(self, actor: str, link_id: str, today: str, limit: int) -> bool:
        """Atomically reserve one display slot for today.

        Check and append happen under the file lock (plus the process
        lock above, since file locks do not exclude threads of the
        same process): already-reserved ids stay reserved so reloads
        render stably; new ids are admitted only while fewer than
        ``limit`` distinct ids are shown. Garbage input fails closed
        (no slot). Call only after the hint evaluated successfully —
        failed evaluations must never consume budget.
        """
        try:
            actor = _require_actor(actor)
            day = self._checked_day(today)
            link = str(link_id or "").strip()
            if not link or isinstance(limit, bool) or int(limit) < 1:
                return False
            cap = int(limit)
        except (ValueError, TypeError):
            return False
        with _BUDGET_WRITE_LOCK:
            with exclusive_file_lock(self.lock):
                data = self._read()
                mine = self._mine(data, actor, day)
                seen = self._shown_of(mine)
                if link in seen:
                    return True
                if len(seen) >= cap:
                    return False
                seen.append(link)
                mine["shown"] = seen
                self._prune(data, actor)
                self._write(data)
        return True
