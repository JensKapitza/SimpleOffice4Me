"""Master-side storage for received license usage reports and client state."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .document_store import CONTROL_DIR

_INSTALLATION_RE = re.compile(r"^[0-9a-fA-F-]{20,64}$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class MasterLicenseStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "license-master.sqlite3"
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS master_license_report(
                    installation_id TEXT NOT NULL,month TEXT NOT NULL,user_count INTEGER NOT NULL,
                    usage_json TEXT NOT NULL,client_prices_json TEXT NOT NULL,total_cents INTEGER NOT NULL,
                    report_hash TEXT NOT NULL,received_at INTEGER NOT NULL,
                    PRIMARY KEY(installation_id,month)
                );
                CREATE TABLE IF NOT EXISTS master_client_state(
                    installation_id TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT 'ok',
                    reason TEXT NOT NULL DEFAULT '',message TEXT NOT NULL DEFAULT '',updated_at INTEGER NOT NULL
                );
                """
            )

    def save_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        installation_id = str(payload.get("installation_id") or "").strip()
        month = str(payload.get("month") or "").strip()
        report_hash = str(payload.get("report_hash") or "").strip().lower()
        usage = payload.get("usage")
        prices = payload.get("prices_cents")
        if not _INSTALLATION_RE.fullmatch(installation_id) or not _MONTH_RE.fullmatch(month):
            raise ValueError("Ungültige Client- oder Monatskennung")
        if not _HASH_RE.fullmatch(report_hash) or not isinstance(usage, dict) or not isinstance(prices, dict):
            raise ValueError("Ungültiger Nutzungsbericht")
        try:
            user_count = max(0, min(int(payload.get("user_count", 0)), 1_000_000))
            total_cents = max(0, min(int(payload.get("total_cents", 0)), 100_000_000_000))
        except (TypeError, ValueError) as exc:
            raise ValueError("Ungültige Summen im Nutzungsbericht") from exc
        with self._db() as db:
            existing = db.execute(
                "SELECT report_hash FROM master_license_report WHERE installation_id=? AND month=?",
                (installation_id, month),
            ).fetchone()
            if existing and existing["report_hash"] != report_hash:
                raise ValueError("Für diesen Client-Monat existiert bereits ein anderer Bericht")
            db.execute(
                """INSERT OR REPLACE INTO master_license_report(
                       installation_id,month,user_count,usage_json,client_prices_json,total_cents,report_hash,received_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (installation_id, month, user_count, _json(usage), _json(prices), total_cents, report_hash, _now()),
            )
            db.execute(
                "INSERT OR IGNORE INTO master_client_state(installation_id,state,reason,message,updated_at) VALUES(?,'ok','','',?)",
                (installation_id, _now()),
            )
        return {"installation_id": installation_id, "month": month, "report_hash": report_hash}

    def client_state(self, installation_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM master_client_state WHERE installation_id=?", (installation_id,)).fetchone()
        if not row:
            return {"state": "ok", "reason": "", "message": ""}
        return {"state": row["state"], "reason": row["reason"], "message": row["message"], "updated_at": row["updated_at"]}

    def set_client_state(self, installation_id: str, state: str, reason: str = "", message: str = "") -> dict[str, Any]:
        state = str(state).strip().casefold()
        if not _INSTALLATION_RE.fullmatch(str(installation_id)) or state not in {"ok", "warning", "blocked"}:
            raise ValueError("Ungültiger Clientstatus")
        with self._db() as db:
            db.execute(
                """INSERT OR REPLACE INTO master_client_state(installation_id,state,reason,message,updated_at)
                   VALUES(?,?,?,?,?)""",
                (installation_id, state, str(reason)[:240], str(message)[:1000], _now()),
            )
        return self.client_state(installation_id)

    def reports(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM master_license_report ORDER BY month DESC,received_at DESC LIMIT ?",
                (max(1, min(int(limit), 5000)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["usage"] = json.loads(item.pop("usage_json") or "{}")
            item["client_prices_cents"] = json.loads(item.pop("client_prices_json") or "{}")
            item["state"] = self.client_state(item["installation_id"])
            result.append(item)
        return result
