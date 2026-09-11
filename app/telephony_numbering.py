"""Private numbering and master-approved routing for federated telephony."""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .document_store import CONTROL_DIR

_NUMBER_RE = re.compile(r"^[0-9]{1,12}$")
_DIAL_RE = re.compile(r"^[0-9]{1,36}$")
_INSTALLATION_RE = re.compile(r"^[0-9a-fA-F-]{20,64}$")
_MASTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def _now() -> int:
    return int(time.time())


def clean_number(value: Any, label: str = "Nummer") -> str:
    number = str(value or "").strip()
    if not _NUMBER_RE.fullmatch(number):
        raise ValueError(f"{label} muss 1 bis 12 Ziffern enthalten")
    return number


def clean_dial_number(value: Any) -> str:
    dialed = str(value or "").strip()
    if dialed.startswith("+"):
        dialed = dialed[1:]
    if not _DIAL_RE.fullmatch(dialed):
        raise ValueError("Federation-Ziel muss 1 bis 36 Ziffern enthalten")
    return dialed


def clean_master_id(value: Any) -> str:
    master = str(value or "").strip()
    if not _MASTER_RE.fullmatch(master):
        raise ValueError("Ungültige Master-ID")
    return master


def clean_installation_id(value: Any) -> str:
    installation = str(value or "").strip()
    if not _INSTALLATION_RE.fullmatch(installation):
        raise ValueError("Ungültige Installations-ID")
    return installation


def prefixes_conflict(left: str, right: str) -> bool:
    left, right = clean_number(left), clean_number(right)
    return left.startswith(right) or right.startswith(left)


def private_dial_number(master_prefix: str, base_number: str, extension: str) -> str:
    """Return a private dial alias; it is deliberately not claimed as PSTN/E.164."""
    return "+" + clean_number(master_prefix, "Master-Vorwahl") + clean_number(base_number, "Basisnummer") + clean_number(extension, "Nebenstelle")


def route_identity(master_id: str, installation_id: str, base_number: str, extension: str) -> dict[str, str]:
    """Canonical routing identity transported between federation masters."""
    return {
        "master_id": clean_master_id(master_id),
        "installation_id": clean_installation_id(installation_id),
        "base_number": clean_number(base_number, "Basisnummer"),
        "extension": clean_number(extension, "Nebenstelle"),
    }


def _legacy_claim_schema(db: sqlite3.Connection) -> bool:
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='telephony_number_claim'"
    ).fetchone()
    if not row:
        return False
    sql = " ".join(str(row["sql"] or "").lower().split())
    return "base_number text not null unique" in sql


def _migrate_claim_schema(db: sqlite3.Connection) -> None:
    if not _legacy_claim_schema(db):
        return
    db.execute("ALTER TABLE telephony_number_claim RENAME TO telephony_number_claim_legacy")
    db.execute(
        """CREATE TABLE telephony_number_claim(
               installation_id TEXT PRIMARY KEY,
               base_number TEXT NOT NULL,
               state TEXT NOT NULL DEFAULT 'approved' CHECK(state IN ('approved','released')),
               updated_at INTEGER NOT NULL
           )"""
    )
    db.execute(
        """INSERT INTO telephony_number_claim(installation_id,base_number,state,updated_at)
           SELECT installation_id,base_number,state,updated_at FROM telephony_number_claim_legacy"""
    )
    db.execute("DROP TABLE telephony_number_claim_legacy")


def _begin_write(db: sqlite3.Connection) -> None:
    db.execute("BEGIN IMMEDIATE")


class MasterNumberRegistry:
    """Atomic license-master registry for server basis numbers and master routes."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "telephony-numbering.sqlite3"
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS telephony_number_claim(
                       installation_id TEXT PRIMARY KEY,
                       base_number TEXT NOT NULL,
                       state TEXT NOT NULL DEFAULT 'approved' CHECK(state IN ('approved','released')),
                       updated_at INTEGER NOT NULL
                   )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS telephony_master_route(
                       master_id TEXT PRIMARY KEY,
                       dial_prefix TEXT NOT NULL UNIQUE,
                       next_peer_id TEXT NOT NULL,
                       enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                       updated_at INTEGER NOT NULL
                   )"""
            )
            _migrate_claim_schema(db)
            db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS uq_telephony_approved_base
                   ON telephony_number_claim(base_number) WHERE state='approved'"""
            )

    def claim(self, installation_id: str, requested_number: str) -> dict[str, Any]:
        installation_id = clean_installation_id(installation_id)
        requested_number = clean_number(requested_number, "Basisnummer")
        with self._db() as db:
            _begin_write(db)
            rows = db.execute(
                "SELECT installation_id,base_number FROM telephony_number_claim WHERE state='approved'"
            ).fetchall()
            for row in rows:
                if row["installation_id"] == installation_id:
                    continue
                if prefixes_conflict(requested_number, row["base_number"]):
                    raise ValueError("Basisnummer ist bereits vergeben oder überschneidet sich mit einem vorhandenen Nummernblock")
            current = db.execute(
                "SELECT base_number,state FROM telephony_number_claim WHERE installation_id=?",
                (installation_id,),
            ).fetchone()
            if current and current["state"] == "approved" and current["base_number"] != requested_number:
                raise ValueError("Installation besitzt bereits eine bestätigte Basisnummer; zuerst freigeben")
            db.execute(
                """INSERT INTO telephony_number_claim(installation_id,base_number,state,updated_at)
                   VALUES(?,?,'approved',?)
                   ON CONFLICT(installation_id) DO UPDATE SET
                     base_number=excluded.base_number,state='approved',updated_at=excluded.updated_at""",
                (installation_id, requested_number, _now()),
            )
        return self.claim_for(installation_id)

    def release(self, installation_id: str) -> dict[str, Any]:
        installation_id = clean_installation_id(installation_id)
        with self._db() as db:
            _begin_write(db)
            db.execute(
                "UPDATE telephony_number_claim SET state='released',updated_at=? WHERE installation_id=?",
                (_now(), installation_id),
            )
        return self.claim_for(installation_id)

    def claim_for(self, installation_id: str) -> dict[str, Any]:
        installation_id = clean_installation_id(installation_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM telephony_number_claim WHERE installation_id=?", (installation_id,)).fetchone()
        return dict(row) if row else {"installation_id": installation_id, "base_number": "", "state": "unassigned", "updated_at": 0}

    def approved_claims(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM telephony_number_claim WHERE state='approved' ORDER BY length(base_number),base_number"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_master_route(self, master_id: str, dial_prefix: str, next_peer_id: str, enabled: bool = True) -> dict[str, Any]:
        master_id = clean_master_id(master_id)
        dial_prefix = clean_number(dial_prefix, "Master-Vorwahl")
        next_peer_id = clean_master_id(next_peer_id)
        with self._db() as db:
            _begin_write(db)
            rows = db.execute("SELECT master_id,dial_prefix FROM telephony_master_route WHERE enabled=1").fetchall()
            for row in rows:
                if row["master_id"] == master_id:
                    continue
                if prefixes_conflict(dial_prefix, row["dial_prefix"]):
                    raise ValueError("Master-Vorwahl kollidiert mit einer vorhandenen Route")
            db.execute(
                """INSERT INTO telephony_master_route(master_id,dial_prefix,next_peer_id,enabled,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(master_id) DO UPDATE SET
                     dial_prefix=excluded.dial_prefix,next_peer_id=excluded.next_peer_id,
                     enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (master_id, dial_prefix, next_peer_id, 1 if enabled else 0, _now()),
            )
        return self.master_route(master_id)

    def master_route(self, master_id: str) -> dict[str, Any]:
        master_id = clean_master_id(master_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM telephony_master_route WHERE master_id=?", (master_id,)).fetchone()
        if not row:
            raise ValueError("Keine Telefonroute für diesen Master")
        return dict(row)

    def resolve_master_prefix(self, dialed: str) -> dict[str, Any]:
        digits = clean_dial_number(dialed)
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM telephony_master_route WHERE enabled=1 ORDER BY length(dial_prefix) DESC"
            ).fetchall()
        for row in rows:
            if digits.startswith(row["dial_prefix"]):
                result = dict(row)
                result["remainder"] = digits[len(row["dial_prefix"]):]
                return result
        raise ValueError("Keine Master-Route für die gewählte Federation-Nummer")
