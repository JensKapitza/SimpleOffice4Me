"""Account-level mail send delegation with optional validity windows."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .document_store import CONTROL_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MailSendDelegationStore:
    """Persist non-secret send delegation state next to mail-case metadata."""

    def __init__(self, root: str | Path, history=None):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "mail-cases.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.history = history
        self._initialize()

    @contextmanager
    def _db(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        if write:
            db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db(write=True) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS mail_send_delegation(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_owner TEXT NOT NULL,
                account_id TEXT NOT NULL,
                delegate_user TEXT NOT NULL,
                valid_from TEXT NOT NULL DEFAULT '',
                valid_until TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(account_owner, account_id, delegate_user)
            );
            CREATE INDEX IF NOT EXISTS idx_mail_send_delegation_lookup
                ON mail_send_delegation(account_owner, account_id, delegate_user, enabled);
            """)

    @staticmethod
    def _date(value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValueError("delegation date must use YYYY-MM-DD") from exc

    def set(
        self, owner: str, account_id: str, delegate_user: str, *,
        valid_from: str = "", valid_until: str = "", enabled: bool = True,
    ) -> None:
        owner = owner.strip()
        account_id = account_id.strip()
        delegate_user = delegate_user.strip()
        if not owner or not account_id or not delegate_user or delegate_user == owner:
            raise ValueError("valid owner, account and different delegate are required")
        start = self._date(valid_from)
        end = self._date(valid_until)
        if start and end and start > end:
            raise ValueError("delegation start must not be after end")
        now = _now()
        with self._db(write=True) as db:
            db.execute(
                """INSERT INTO mail_send_delegation
                   (account_owner,account_id,delegate_user,valid_from,valid_until,enabled,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(account_owner,account_id,delegate_user)
                   DO UPDATE SET valid_from=excluded.valid_from,valid_until=excluded.valid_until,
                                 enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (owner, account_id, delegate_user, start, end, int(bool(enabled)), now, now),
            )
        self._audit(
            "mail_send_delegation_changed", owner, account_id,
            {"delegate_user": delegate_user, "valid_from": start, "valid_until": end, "enabled": bool(enabled)},
        )

    def remove(self, owner: str, account_id: str, delegate_user: str) -> None:
        with self._db(write=True) as db:
            row = db.execute(
                """SELECT 1 FROM mail_send_delegation
                   WHERE account_owner=? AND account_id=? AND delegate_user=?""",
                (owner, account_id, delegate_user),
            ).fetchone()
            if row is None:
                raise KeyError(delegate_user)
            db.execute(
                """DELETE FROM mail_send_delegation
                   WHERE account_owner=? AND account_id=? AND delegate_user=?""",
                (owner, account_id, delegate_user),
            )
        self._audit(
            "mail_send_delegation_removed", owner, account_id,
            {"delegate_user": delegate_user},
        )

    def list(self, owner: str, account_id: str) -> list[dict]:
        with self._db() as db:
            return [dict(row) for row in db.execute(
                """SELECT delegate_user,valid_from,valid_until,enabled,created_at,updated_at
                   FROM mail_send_delegation
                   WHERE account_owner=? AND account_id=?
                   ORDER BY delegate_user COLLATE NOCASE""",
                (owner, account_id),
            )]

    def has_active(
        self, owner: str, account_id: str, delegate_user: str, *, on_date: str = "",
    ) -> bool:
        day = self._date(on_date) if on_date else datetime.now(timezone.utc).date().isoformat()
        with self._db() as db:
            row = db.execute(
                """SELECT valid_from,valid_until,enabled FROM mail_send_delegation
                   WHERE account_owner=? AND account_id=? AND delegate_user=?""",
                (owner, account_id, delegate_user),
            ).fetchone()
        if row is None or not bool(row["enabled"]):
            return False
        start = str(row["valid_from"] or "")
        end = str(row["valid_until"] or "")
        return (not start or day >= start) and (not end or day <= end)

    def _audit(self, action: str, actor: str, account_id: str, details: dict) -> None:
        if self.history is not None:
            self.history.record(action, actor, "mail-delegation", account_id, details)
