"""Object reservations with date ranges, separate from physical checkout."""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _reservation_dates(start: Any, end: Any) -> tuple[str, str]:
    first_text, last_text = str(start or "").strip(), str(end or "").strip()
    if not first_text or not last_text:
        raise ValueError("Reservierungszeitraum fehlt")
    try:
        first, last = date.fromisoformat(first_text), date.fromisoformat(last_text)
    except ValueError as exc:
        raise ValueError("Reservierungszeitraum muss aus ISO-Daten bestehen") from exc
    if first < date.today():
        raise ValueError("Reservierungsbeginn liegt in der Vergangenheit")
    if last < first:
        raise ValueError("Reservierungsende liegt vor dem Beginn")
    if last > date.today() + timedelta(days=730):
        raise ValueError("Reservierungszeitraum ist zu lang")
    return first.isoformat(), last.isoformat()


def _overlaps(first_start: str, first_end: str, second_start: str, second_end: str) -> bool:
    return first_start <= second_end and second_start <= first_end


class ReservationStore:
    """Small locked JSON store for object reservations."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "inventory"
        self.state_path = self.directory / "reservations.json"
        self.lock_path = self.directory / ".reservations-write.lock"

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        reservations = value.get("reservations", [])
        value["reservations"] = [row for row in reservations if isinstance(row, dict)]
        return value

    def _write(self, state: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.state_path, state)

    def reservations(self, object_id: str = "") -> list[dict[str, Any]]:
        wanted = str(object_id or "").strip()
        rows = [dict(row) for row in self._read()["reservations"]
                if str(row.get("status", "")) == "active"]
        if wanted:
            rows = [row for row in rows if str(row.get("object_id", "")) == wanted]
        return sorted(rows, key=lambda row: (str(row.get("start_date", "")), str(row.get("end_date", ""))))

    def conflicts(self, object_id: str, start: str, end: str) -> list[dict[str, Any]]:
        """Active reservations overlapping [start, end]; pure check, changes nothing."""
        first, last = str(start or "").strip(), str(end or "").strip()
        if not first or not last:
            return []
        return [row for row in self.reservations(object_id)
                if _overlaps(first, last, str(row.get("start_date", "")),
                              str(row.get("end_date", "")))]

    def reserve(self, object_id: str, reserver: str, start_date: str, end_date: str,
              actor: str, *, note: str = "") -> dict[str, Any]:
        clean_object = str(object_id or "").strip()
        clean_reserver = _clean(reserver, 200)
        if not clean_object or not clean_reserver:
            raise ValueError("Objekt und reservierende Person sind erforderlich")
        if not str(actor or "").strip():
            raise ValueError("user is required")
        first, last = _reservation_dates(start_date, end_date)
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            for row in state["reservations"]:
                if (str(row.get("object_id", "")) == clean_object
                        and str(row.get("status", "")) == "active"
                        and _overlaps(first, last, str(row.get("start_date", "")),
                                      str(row.get("end_date", "")))):
                    raise ValueError("Zeitraum überschneidet eine bestehende Reservierung")
            row = {"reservation_id": str(uuid.uuid4()), "object_id": clean_object,
                   "reserver": clean_reserver, "start_date": first, "end_date": last,
                   "note": _clean(note, 1000), "status": "active",
                   "created_at": utc_now(), "created_by": actor}
            state["reservations"].append(row)
            self._write(state)
        return dict(row)

    def cancel(self, reservation_id: str, actor: str) -> dict[str, Any]:
        wanted = str(reservation_id or "").strip()
        if not str(actor or "").strip():
            raise ValueError("user is required")
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            row = next((value for value in state["reservations"]
                        if str(value.get("reservation_id", "")) == wanted), None)
            if row is None or str(row.get("status", "")) != "active":
                raise ValueError("Unbekannte oder bereits beendete Reservierung")
            row["status"] = "cancelled"
            row["cancelled_at"] = utc_now()
            row["cancelled_by"] = actor
            self._write(state)
        return dict(row)
