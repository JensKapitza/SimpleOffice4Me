"""Durable opt-in and retry policy for Google Calendar pulls."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

POLL_SECONDS = 30
INTERVAL_SECONDS = 15 * 60
MAX_RETRY_SECONDS = 6 * 60 * 60
CRASH_RETRY_SECONDS = 5 * 60
MAX_FAILURES = 12


def timestamp(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat() if seconds > 0 else ""


class AutomaticGoogleCalendar:
    """Called while holding the same per-account file lock as manual sync."""

    def __init__(self, sync):
        self.sync = sync

    def _state(self, actor: str) -> dict:
        return self.sync._state().get(actor, {}).get("automatic", {})

    def _write(self, actor: str, automatic: dict) -> None:
        self.sync._update_state(actor, lambda state: {**state, "automatic": automatic})

    def configure(self, actor: str, user_id: int, enable: bool) -> None:
        from .google_calendar_sync import GoogleCalendarError

        with self.sync.account_lock(actor, blocking=False) as acquired:
            if not acquired:
                raise GoogleCalendarError("Google Calendar sync is already running")
            if not enable:
                if self._state(actor):
                    self.sync._update_state(
                        actor, lambda state: {key: value for key, value in state.items() if key != "automatic"},
                    )
                    self._audit(actor, False)
                return
            cfg, target = self.sync._connection(actor)
            existing = self._state(actor)
            identity = self.sync.config_key(actor, cfg, target)
            if existing.get("config_key") == identity and existing.get("user_id") == user_id:
                return
            self._write(actor, {"config_key": identity, "user_id": user_id, "due": 0, "outcome": "waiting"})
            self._audit(actor, True)

    def _audit(self, actor: str, enabled: bool) -> None:
        self.sync.history.record(
            "google_calendar_automatic_configured", actor, "google-calendar-sync", actor, {"enabled": enabled},
        )

    def status(self, config_key: str, state: dict, user_id: int | None) -> dict:
        recorded = state.get("automatic", {})
        if not recorded:
            return {"enabled": False}
        same = bool(config_key and recorded.get("config_key") == config_key
                    and (user_id is None or recorded.get("user_id") == user_id))
        outcome = recorded.get("outcome", "waiting") if same else "configuration_changed"
        due = recorded.get("due", 0) if same and outcome != "conflict" else 0
        return {
            "enabled": same, "configured_before": True, "outcome": outcome,
            "next_run": timestamp(due), "last_attempt": timestamp(recorded.get("last_attempt", 0)),
        }

    def _result(self, actor: str, state: dict, now: int, outcome: str) -> None:
        failures = min(MAX_FAILURES, state.get("failures", 0) + 1) if outcome == "error" else 0
        delay = min(MAX_RETRY_SECONDS, INTERVAL_SECONDS * 2 ** (failures - 1)) if failures else INTERVAL_SECONDS
        self._write(actor, {
            **state, "outcome": outcome, "failures": failures,
            "due": 0 if outcome == "conflict" else now + delay,
        })

    def manual_result(self, actor: str, config_key: str, conflicts: bool) -> None:
        state = self._state(actor)
        if state.get("config_key") == config_key:
            self._result(actor, state, int(self.sync.clock()), "conflict" if conflicts else "ok")

    def run_due(self, actor: str, access: Callable[[str, int], bool]) -> bool:
        now = int(self.sync.clock())
        with self.sync.account_lock(actor, blocking=False) as acquired:
            if not acquired:
                return False
            state = self._state(actor)
            if (not state or state.get("due", 0) > now or state.get("outcome") == "conflict"
                    or not access(actor, state["user_id"])):
                return False
            try:
                cfg, target = self.sync._connection(actor)
            except (ValueError, OSError):
                self._result(actor, {**state, "last_attempt": now}, now, "error")
                return True
            if state.get("config_key") != self.sync.config_key(actor, cfg, target):
                return False
            state = {**state, "last_attempt": now}
            # Persist a restart delay before network work. A dead process cannot
            # leave a permanent running flag or cause every restart to re-fetch.
            self._write(actor, {**state, "due": now + CRASH_RETRY_SECONDS})
            try:
                result = self.sync._synchronize(
                    actor, True, "", connection=(cfg, target), recheck=lambda: access(actor, state["user_id"]),
                )
            except (ValueError, OSError, TypeError, AttributeError):
                self._result(actor, state, int(self.sync.clock()), "error")
            else:
                outcome = "conflict" if result["conflicts"] else "ok"
                self._result(actor, state, int(self.sync.clock()), outcome)
            return True


def wall_clock() -> float:
    return time.time()
