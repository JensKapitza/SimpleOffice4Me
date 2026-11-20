"""Physical object lending: checkout, return, due dates and history."""
from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _due_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Rückgabedatum fehlt")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Rückgabedatum muss ein ISO-Datum sein") from exc
    if parsed < date.today():
        raise ValueError("Rückgabedatum liegt in der Vergangenheit")
    return parsed.isoformat()


class LendingStore:
    """Small locked JSON store for object loans."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "inventory"
        self.state_path = self.directory / "lending.json"
        self.lock_path = self.directory / ".lending-write.lock"

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        loans = value.get("loans", [])
        value["loans"] = [row for row in loans if isinstance(row, dict)]
        return value

    def _write(self, state: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.state_path, state)

    def loans(self, object_id: str = "") -> list[dict[str, Any]]:
        wanted = str(object_id or "").strip()
        rows = [dict(row) for row in self._read()["loans"]]
        if wanted:
            rows = [row for row in rows if str(row.get("object_id", "")) == wanted]
        return sorted(rows, key=lambda row: str(row.get("checked_out_at", "")), reverse=True)

    def open_loan(self, object_id: str) -> dict[str, Any] | None:
        for row in self.loans(object_id):
            if not str(row.get("returned_at", "") or "").strip():
                return row
        return None

    def overdue(self, today: str = "") -> list[dict[str, Any]]:
        current = str(today or "").strip() or date.today().isoformat()
        return [row for row in self.loans()
                if not str(row.get("returned_at", "") or "").strip()
                and str(row.get("due_date", "")) < current]

    def checkout(self, object_id: str, borrower: str, due_date: str, actor: str, *, note: str = "") -> dict[str, Any]:
        clean_object = str(object_id or "").strip()
        clean_borrower = _clean(borrower, 200)
        if not clean_object or not clean_borrower:
            raise ValueError("Objekt und entleihende Person sind erforderlich")
        if not str(actor or "").strip():
            raise ValueError("user is required")
        due = _due_date(due_date)
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            for row in state["loans"]:
                if str(row.get("object_id", "")) == clean_object and not str(row.get("returned_at", "") or "").strip():
                    raise ValueError("Objekt ist bereits entliehen")
            row = {"loan_id": str(uuid.uuid4()), "object_id": clean_object,
                   "borrower": clean_borrower, "due_date": due,
                   "note": _clean(note, 1000), "checked_out_at": utc_now(),
                   "checked_out_by": actor, "returned_at": "", "returned_by": ""}
            state["loans"].append(row)
            self._write(state)
        return dict(row)

    def checkin(self, loan_id: str, actor: str) -> dict[str, Any]:
        wanted = str(loan_id or "").strip()
        if not str(actor or "").strip():
            raise ValueError("user is required")
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            row = next((value for value in state["loans"]
                        if str(value.get("loan_id", "")) == wanted), None)
            if row is None:
                raise ValueError("Unbekannte Leihe")
            if str(row.get("returned_at", "") or "").strip():
                raise ValueError("Leihe ist bereits zurückgegeben")
            row["returned_at"] = utc_now()
            row["returned_by"] = actor
            self._write(state)
        return dict(row)
