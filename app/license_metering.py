"""Local license metering, monthly snapshots and billing state.

The subsystem is deliberately non-blocking for local application features. A
master can mark an installation as billing-blocked; this is advertised to
federation peers and influences federation trust/priority, while the local app
continues to work.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from .access_control import FEATURES
from .build_master import LICENSE_MASTER_MODE, LICENSE_MASTER_PEER_ID, LICENSE_MASTER_URL
from .document_store import CONTROL_DIR
from .system_identity import installation_id

SCHEMA_VERSION = 1
DEFAULT_PRICE_CENTS = {"user": 0, **{feature: 0 for feature in FEATURES}}


def _now() -> int:
    return int(time.time())


def _month_key(timestamp: int | None = None) -> str:
    dt = datetime.fromtimestamp(timestamp or _now(), timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def _previous_month(key: str) -> str:
    year, month = (int(part) for part in key.split("-", 1))
    month -= 1
    if month == 0:
        year -= 1
        month = 12
    return f"{year:04d}-{month:02d}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def feature_for_endpoint(endpoint: str) -> str:
    value = str(endpoint or "")
    prefix = value.split(".", 1)[0]
    mapping = {
        "documents": "documents", "photo_upload": "documents", "inventory": "documents",
        "caldav": "calendar", "calendar": "calendar", "calendar_store": "calendar",
        "carddav": "contacts", "contact_audit": "contacts", "contact_tools": "contacts",
        "mail_routes": "mail", "mail_reader": "mail", "mail_index": "mail",
        "webdav": "webdav", "federation_http": "sync", "federation_admin": "sync",
        "federation_catalog": "sync", "software_admin": "sync", "replication": "sync",
        "task_management": "projects", "personnel": "projects", "personnel_time": "projects",
        "datalogger": "datalogger",
    }
    if prefix in mapping:
        return mapping[prefix]
    for feature in FEATURES:
        if value.startswith(feature + "."):
            return feature
    return ""


class LicenseStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "licensing.sqlite3"
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
                CREATE TABLE IF NOT EXISTS license_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS license_usage(
                    month TEXT NOT NULL,user_id INTEGER NOT NULL,feature TEXT NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,last_used_at INTEGER NOT NULL,
                    PRIMARY KEY(month,user_id,feature)
                );
                CREATE TABLE IF NOT EXISTS license_month(
                    month TEXT PRIMARY KEY,finalized_at INTEGER NOT NULL,user_count INTEGER NOT NULL,
                    usage_json TEXT NOT NULL,price_json TEXT NOT NULL,total_cents INTEGER NOT NULL,
                    report_hash TEXT NOT NULL,report_state TEXT NOT NULL DEFAULT 'pending',
                    reported_at INTEGER,last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS license_invoice(
                    invoice_id TEXT PRIMARY KEY,service TEXT NOT NULL,month TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,currency TEXT NOT NULL DEFAULT 'EUR',
                    due_date TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'open',
                    document_url TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS license_state(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),state TEXT NOT NULL DEFAULT 'ok',
                    reason TEXT NOT NULL DEFAULT '',message TEXT NOT NULL DEFAULT '',updated_at INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'local'
                );
                """
            )
            db.execute("INSERT OR REPLACE INTO license_meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
            db.execute(
                "INSERT OR IGNORE INTO license_state(singleton,state,reason,message,updated_at,source) VALUES(1,'ok','','',?,'local')",
                (_now(),),
            )
            for key, cents in DEFAULT_PRICE_CENTS.items():
                db.execute("INSERT OR IGNORE INTO license_meta(key,value) VALUES(?,?)", (f"price:{key}", str(cents)))

    def record_usage(self, user_id: int, feature: str) -> None:
        if feature not in FEATURES or int(user_id) <= 0:
            return
        month = _month_key()
        with self._db() as db:
            db.execute(
                """INSERT INTO license_usage(month,user_id,feature,request_count,last_used_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(month,user_id,feature) DO UPDATE SET
                   request_count=request_count+1,last_used_at=excluded.last_used_at""",
                (month, int(user_id), feature, 1, _now()),
            )
        self.finalize_due_months()

    def prices(self) -> dict[str, int]:
        with self._db() as db:
            rows = db.execute("SELECT key,value FROM license_meta WHERE key LIKE 'price:%'").fetchall()
        result = dict(DEFAULT_PRICE_CENTS)
        for row in rows:
            try:
                result[str(row["key"])[6:]] = max(0, int(row["value"]))
            except (TypeError, ValueError):
                pass
        return result

    def set_prices(self, prices: dict[str, Any]) -> dict[str, int]:
        allowed = {"user", *FEATURES}
        clean: dict[str, int] = {}
        for key, value in prices.items():
            if key not in allowed:
                continue
            try:
                cents = int(value)
            except (TypeError, ValueError):
                continue
            clean[key] = max(0, min(cents, 100_000_000))
        with self._db() as db:
            for key, cents in clean.items():
                db.execute("INSERT OR REPLACE INTO license_meta(key,value) VALUES(?,?)", (f"price:{key}", str(cents)))
        return self.prices()

    def finalize_due_months(self) -> list[dict[str, Any]]:
        current = _month_key()
        with self._db() as db:
            rows = db.execute("SELECT DISTINCT month FROM license_usage WHERE month < ? ORDER BY month", (current,)).fetchall()
        finalized = []
        for row in rows:
            item = self.finalize_month(str(row["month"]))
            if item:
                finalized.append(item)
        return finalized

    def finalize_month(self, month: str) -> dict[str, Any] | None:
        if month >= _month_key():
            return None
        with self._db() as db:
            existing = db.execute("SELECT * FROM license_month WHERE month=?", (month,)).fetchone()
            if existing:
                return dict(existing)
            rows = db.execute(
                "SELECT user_id,feature,request_count FROM license_usage WHERE month=? ORDER BY user_id,feature", (month,)
            ).fetchall()
            if not rows:
                return None
        users = sorted({int(row["user_id"]) for row in rows})
        usage = {feature: {"users": 0, "requests": 0} for feature in FEATURES}
        by_feature_users: dict[str, set[int]] = {feature: set() for feature in FEATURES}
        for row in rows:
            feature = str(row["feature"])
            if feature not in usage:
                continue
            usage[feature]["requests"] += int(row["request_count"])
            by_feature_users[feature].add(int(row["user_id"]))
        for feature in usage:
            usage[feature]["users"] = len(by_feature_users[feature])
        prices = self.prices()
        total = len(users) * prices.get("user", 0)
        for feature, stats in usage.items():
            total += int(stats["users"]) * prices.get(feature, 0)
        payload = {
            "schema": 1, "installation_id": installation_id(), "month": month,
            "user_count": len(users), "usage": usage, "prices_cents": prices, "total_cents": total,
        }
        digest = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        with self._db() as db:
            db.execute(
                """INSERT INTO license_month(month,finalized_at,user_count,usage_json,price_json,total_cents,report_hash)
                   VALUES(?,?,?,?,?,?,?)""",
                (month, _now(), len(users), _json(usage), _json(prices), total, digest),
            )
        return self.month(month)

    def month(self, month: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM license_month WHERE month=?", (month,)).fetchone()
        return self._month_row(row) if row else None

    def months(self, limit: int = 24) -> list[dict[str, Any]]:
        self.finalize_due_months()
        with self._db() as db:
            rows = db.execute("SELECT * FROM license_month ORDER BY month DESC LIMIT ?", (max(1, min(limit, 120)),)).fetchall()
        return [self._month_row(row) for row in rows]

    def mark_report(self, month: str, *, success: bool, error: str = "") -> None:
        with self._db() as db:
            db.execute(
                "UPDATE license_month SET report_state=?,reported_at=?,last_error=? WHERE month=?",
                ("reported" if success else "failed", _now() if success else None, str(error)[:1000], month),
            )

    def pending_reports(self) -> list[dict[str, Any]]:
        self.finalize_due_months()
        with self._db() as db:
            rows = db.execute("SELECT * FROM license_month WHERE report_state!='reported' ORDER BY month").fetchall()
        return [self._month_row(row) for row in rows]

    def state(self) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM license_state WHERE singleton=1").fetchone()
        result = dict(row) if row else {"state": "ok", "reason": "", "message": "", "updated_at": 0, "source": "local"}
        result["bad_client"] = result["state"] == "blocked"
        return result

    def set_state(self, state: str, *, reason: str = "", message: str = "", source: str = "master") -> dict[str, Any]:
        state = str(state).strip().casefold()
        if state not in {"ok", "warning", "blocked"}:
            raise ValueError("Ungültiger Lizenzstatus")
        with self._db() as db:
            db.execute(
                """INSERT OR REPLACE INTO license_state(singleton,state,reason,message,updated_at,source)
                   VALUES(1,?,?,?,?,?)""",
                (state, str(reason)[:240], str(message)[:1000], _now(), str(source)[:80]),
            )
        return self.state()

    def save_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        invoice_id = str(payload.get("invoice_id") or "").strip()[:160]
        service = str(payload.get("service") or "license").strip()[:160] or "license"
        month = str(payload.get("month") or "").strip()[:7]
        currency = str(payload.get("currency") or "EUR").strip().upper()[:3]
        due_date = str(payload.get("due_date") or "").strip()[:32]
        document_url = str(payload.get("document_url") or "").strip()[:1000]
        status = str(payload.get("status") or "open").strip().casefold()
        if not invoice_id or status not in {"open", "paid", "cancelled"} or currency != "EUR":
            raise ValueError("Ungültige Abrechnung")
        try:
            amount_cents = max(0, int(payload.get("amount_cents", 0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("Ungültiger Rechnungsbetrag") from exc
        now = _now()
        with self._db() as db:
            current = db.execute("SELECT created_at FROM license_invoice WHERE invoice_id=?", (invoice_id,)).fetchone()
            db.execute(
                """INSERT INTO license_invoice(invoice_id,service,month,amount_cents,currency,due_date,status,document_url,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(invoice_id) DO UPDATE SET
                   service=excluded.service,month=excluded.month,amount_cents=excluded.amount_cents,
                   currency=excluded.currency,due_date=excluded.due_date,status=excluded.status,
                   document_url=excluded.document_url,updated_at=excluded.updated_at""",
                (invoice_id, service, month, amount_cents, currency, due_date, status, document_url,
                 int(current["created_at"]) if current else now, now),
            )
        return self.invoice(invoice_id) or {}

    def invoice(self, invoice_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM license_invoice WHERE invoice_id=?", (invoice_id,)).fetchone()
        return dict(row) if row else None

    def invoices(self, *, open_only: bool = False) -> list[dict[str, Any]]:
        with self._db() as db:
            sql = "SELECT * FROM license_invoice" + (" WHERE status='open'" if open_only else "") + " ORDER BY created_at DESC"
            rows = db.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def overview(self) -> dict[str, Any]:
        current = _month_key()
        with self._db() as db:
            active_users = db.execute("SELECT COUNT(DISTINCT user_id) FROM license_usage WHERE month=?", (current,)).fetchone()[0]
            usage_rows = db.execute(
                "SELECT feature,COUNT(DISTINCT user_id) users,SUM(request_count) requests FROM license_usage WHERE month=? GROUP BY feature",
                (current,),
            ).fetchall()
        usage = {feature: {"users": 0, "requests": 0} for feature in FEATURES}
        for row in usage_rows:
            if row["feature"] in usage:
                usage[row["feature"]] = {"users": int(row["users"] or 0), "requests": int(row["requests"] or 0)}
        return {
            "current_month": current, "active_users": int(active_users or 0), "usage": usage,
            "prices_cents": self.prices(), "months": self.months(), "state": self.state(),
            "invoices": self.invoices(), "open_invoices": self.invoices(open_only=True),
            "master": {"url": LICENSE_MASTER_URL, "peer_id": LICENSE_MASTER_PEER_ID, "is_master": bool(LICENSE_MASTER_MODE)},
        }

    @staticmethod
    def _month_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        try:
            result["usage"] = json.loads(result.pop("usage_json"))
        except (TypeError, json.JSONDecodeError):
            result["usage"] = {}
        try:
            result["prices_cents"] = json.loads(result.pop("price_json"))
        except (TypeError, json.JSONDecodeError):
            result["prices_cents"] = {}
        return result
