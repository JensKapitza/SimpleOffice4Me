"""Durable finance core shared by banking, invoices, rentals and EÜR.

Existing feature stores remain canonical until explicit adapters migrate them.
Raw bank transactions are append-only; categorisation and matching reference the
raw records instead of rewriting imported bank data.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .document_store import CONTROL_DIR
from .finance_schema import (
    ACCOUNT_KINDS, ALLOCATION_TARGETS, ENTRY_DIRECTIONS, ENTRY_STATUSES, SCHEMA,
    SOURCE_TYPES, TAG_RE, OBLIGATION_KINDS, RECURRENCE_UNITS, TAX_YEAR_STATUSES, BANK_CONNECTION_STATUSES, currency, iso_date, new_id, normalize_iban,
    positive_cents, signed_cents, text, transaction_fingerprint,
)


def _now() -> int:
    return int(time.time())


class FinanceStore:
    """SQLite finance store with source idempotency and explicit allocation data."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "finance.sqlite3"
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript(SCHEMA)
            # Forward-only additive migration for databases created by an
            # earlier Finance Core draft. Never drop or rewrite finance data.
            columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(finance_bank_connection)").fetchall()}
            if columns and "bank_code" not in columns:
                db.execute("ALTER TABLE finance_bank_connection ADD COLUMN bank_code TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _actor(value: Any) -> str:
        actor = text(value, 120)
        if not actor:
            raise ValueError("a named user is required")
        return actor

    def _audit(self, db: sqlite3.Connection, actor: str, event: str, entity_type: str, entity_id: str, data: dict[str, Any] | None = None) -> None:
        db.execute("INSERT INTO finance_audit(actor,event,entity_type,entity_id,data_json,created_at) VALUES(?,?,?,?,?,?)", (actor, event, entity_type, entity_id, json.dumps(data or {}, ensure_ascii=False, sort_keys=True), _now()))

    def create_account(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        actor = self._actor(actor)
        kind = text(values.get("kind", "bank"), 30).casefold()
        if kind not in ACCOUNT_KINDS:
            raise ValueError("account kind is invalid")
        name = text(values.get("name"), 200)
        if not name:
            raise ValueError("account name is required")
        account_id, ts = new_id("acc"), _now()
        row = {"account_id": account_id, "kind": kind, "name": name, "institution": text(values.get("institution"), 200), "iban": normalize_iban(values.get("iban")), "bic": text(values.get("bic"), 20).upper(), "currency": currency(values.get("currency", "EUR")), "owner": actor, "active": 1, "created_at": ts, "updated_at": ts}
        try:
            with self._db() as db:
                db.execute("INSERT INTO finance_account(account_id,kind,name,institution,iban,bic,currency,owner,active,created_at,updated_at) VALUES(:account_id,:kind,:name,:institution,:iban,:bic,:currency,:owner,:active,:created_at,:updated_at)", row)
                self._audit(db, actor, "account_created", "account", account_id, {"kind": kind, "name": name})
        except sqlite3.IntegrityError as exc:
            raise ValueError("account already exists for this IBAN") from exc
        return row

    def accounts(self, actor: str, *, is_admin: bool = False, active_only: bool = True) -> list[dict[str, Any]]:
        actor = self._actor(actor); where, params = [], []
        if not is_admin: where.append("owner=?"); params.append(actor)
        if active_only: where.append("active=1")
        sql = "SELECT * FROM finance_account" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY name COLLATE NOCASE,account_id"
        with self._db() as db: return [dict(row) for row in db.execute(sql, params).fetchall()]

    def account_by_iban(self, iban: str, actor: str, *, is_admin: bool = False) -> dict[str, Any] | None:
        normalized, actor = normalize_iban(iban), self._actor(actor)
        if not normalized: return None
        with self._db() as db:
            if is_admin: row = db.execute("SELECT * FROM finance_account WHERE iban=? ORDER BY created_at LIMIT 1", (normalized,)).fetchone()
            else: row = db.execute("SELECT * FROM finance_account WHERE owner=? AND iban=?", (actor, normalized)).fetchone()
        return dict(row) if row else None

    def create_import_batch(self, account_id: str, values: dict[str, Any], actor: str) -> tuple[dict[str, Any], bool]:
        actor = self._actor(actor); digest = text(values.get("file_sha256"), 64).casefold()
        if digest and not re.fullmatch(r"[0-9a-f]{64}", digest): raise ValueError("file_sha256 must be a SHA-256 digest")
        row = {"batch_id": new_id("batch"), "account_id": text(account_id, 100), "source_type": text(values.get("source_type", "file"), 40) or "file", "source_name": text(values.get("source_name"), 300), "file_sha256": digest, "period_start": iso_date(values.get("period_start"), "period_start", allow_empty=True), "period_end": iso_date(values.get("period_end"), "period_end", allow_empty=True), "imported_by": actor, "imported_at": _now(), "row_count": max(0, int(values.get("row_count", 0) or 0)), "status": "imported"}
        with self._db() as db:
            account = db.execute("SELECT owner FROM finance_account WHERE account_id=?", (row["account_id"],)).fetchone()
            if not account or str(account["owner"]) != actor: raise ValueError("account not found")
            if digest:
                existing = db.execute("SELECT * FROM finance_import_batch WHERE account_id=? AND file_sha256=?", (row["account_id"], digest)).fetchone()
                if existing: return dict(existing), False
            db.execute("INSERT INTO finance_import_batch(batch_id,account_id,source_type,source_name,file_sha256,period_start,period_end,imported_by,imported_at,row_count,status) VALUES(:batch_id,:account_id,:source_type,:source_name,:file_sha256,:period_start,:period_end,:imported_by,:imported_at,:row_count,:status)", row)
            self._audit(db, actor, "import_batch_created", "import_batch", row["batch_id"], {"account_id": row["account_id"], "file_sha256": digest})
        return row, True

    def import_bank_transaction(self, values: dict[str, Any], actor: str) -> tuple[dict[str, Any], str]:
        """Append bank raw data; fingerprints warn but never silently deduplicate."""
        actor = self._actor(actor); account_id = text(values.get("account_id"), 100)
        row = {"transaction_id": new_id("tx"), "account_id": account_id, "import_batch_id": text(values.get("import_batch_id"), 100), "booking_date": iso_date(values.get("booking_date"), "booking_date"), "value_date": iso_date(values.get("value_date"), "value_date", allow_empty=True), "amount_cents": signed_cents(values.get("amount_cents")), "currency": currency(values.get("currency", "EUR")), "counterparty_name": text(values.get("counterparty_name"), 300), "counterparty_iban": normalize_iban(values.get("counterparty_iban")), "purpose": text(values.get("purpose"), 2000), "bank_transaction_id": text(values.get("bank_transaction_id"), 300), "end_to_end_id": text(values.get("end_to_end_id"), 300), "mandate_id": text(values.get("mandate_id"), 300), "duplicate_state": "distinct", "raw_json": json.dumps(values.get("raw", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:100000], "created_at": _now()}
        row["fingerprint"] = transaction_fingerprint(row)
        with self._db() as db:
            account = db.execute("SELECT owner FROM finance_account WHERE account_id=?", (account_id,)).fetchone()
            if not account or str(account["owner"]) != actor: raise ValueError("account not found")
            if row["import_batch_id"]:
                batch = db.execute("SELECT account_id FROM finance_import_batch WHERE batch_id=?", (row["import_batch_id"],)).fetchone()
                if not batch or str(batch["account_id"]) != account_id: raise ValueError("import batch does not belong to account")
            if row["bank_transaction_id"]:
                existing = db.execute("SELECT * FROM finance_bank_transaction WHERE account_id=? AND bank_transaction_id=? ORDER BY created_at LIMIT 1", (account_id, row["bank_transaction_id"])).fetchone()
                if existing: return dict(existing), "existing"
            likely = None
            if row["end_to_end_id"]: likely = db.execute("SELECT transaction_id FROM finance_bank_transaction WHERE account_id=? AND end_to_end_id=? AND amount_cents=? AND booking_date=? LIMIT 1", (account_id, row["end_to_end_id"], row["amount_cents"], row["booking_date"])).fetchone()
            if not likely and row["mandate_id"]: likely = db.execute("SELECT transaction_id FROM finance_bank_transaction WHERE account_id=? AND mandate_id=? AND amount_cents=? AND booking_date=? LIMIT 1", (account_id, row["mandate_id"], row["amount_cents"], row["booking_date"])).fetchone()
            possible = db.execute("SELECT transaction_id FROM finance_bank_transaction WHERE account_id=? AND fingerprint=? LIMIT 1", (account_id, row["fingerprint"])).fetchone()
            if likely: row["duplicate_state"], result = "likely_duplicate", "possible_duplicate"
            elif possible: row["duplicate_state"], result = "possible_duplicate", "possible_duplicate"
            else: result = "created"
            db.execute("INSERT INTO finance_bank_transaction(transaction_id,account_id,import_batch_id,booking_date,value_date,amount_cents,currency,counterparty_name,counterparty_iban,purpose,bank_transaction_id,end_to_end_id,mandate_id,fingerprint,duplicate_state,raw_json,created_at) VALUES(:transaction_id,:account_id,:import_batch_id,:booking_date,:value_date,:amount_cents,:currency,:counterparty_name,:counterparty_iban,:purpose,:bank_transaction_id,:end_to_end_id,:mandate_id,:fingerprint,:duplicate_state,:raw_json,:created_at)", row)
            self._audit(db, actor, "bank_transaction_imported", "bank_transaction", row["transaction_id"], {"account_id": account_id, "duplicate_state": row["duplicate_state"]})
        return row, result

    def bank_transactions(self, account_id: str, actor: str, *, limit: int = 500) -> list[dict[str, Any]]:
        actor, limit = self._actor(actor), max(1, min(int(limit), 5000))
        with self._db() as db:
            account = db.execute("SELECT owner FROM finance_account WHERE account_id=?", (text(account_id, 100),)).fetchone()
            if not account or str(account["owner"]) != actor: raise ValueError("account not found")
            rows = db.execute("SELECT * FROM finance_bank_transaction WHERE account_id=? ORDER BY booking_date DESC,created_at DESC,transaction_id DESC LIMIT ?", (account_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def create_entry(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        actor = self._actor(actor); direction = text(values.get("direction"), 20).casefold(); status = text(values.get("status", "paid"), 30).casefold(); source_type = text(values.get("source_type", "manual"), 40).casefold() or "manual"
        if direction not in ENTRY_DIRECTIONS: raise ValueError("entry direction is invalid")
        if status not in ENTRY_STATUSES: raise ValueError("entry status is invalid")
        if source_type not in SOURCE_TYPES: raise ValueError("entry source type is invalid")
        description = text(values.get("description"), 500)
        if not description: raise ValueError("entry description is required")
        tax_year_raw = values.get("tax_year"); tax_year = None if tax_year_raw in {None, ""} else int(tax_year_raw)
        if tax_year is not None and not 1900 <= tax_year <= 2200: raise ValueError("tax_year is outside the supported range")
        ts = _now(); row = {"entry_id": new_id("entry"), "owner": actor, "account_id": text(values.get("account_id"), 100), "direction": direction, "amount_cents": positive_cents(values.get("amount_cents")), "currency": currency(values.get("currency", "EUR")), "booking_date": iso_date(values.get("booking_date"), "booking_date"), "value_date": iso_date(values.get("value_date"), "value_date", allow_empty=True), "description": description, "category": text(values.get("category"), 120), "source_type": source_type, "source_id": text(values.get("source_id"), 200), "status": status, "tax_year": tax_year, "created_at": ts, "updated_at": ts}
        try:
            with self._db() as db:
                if row["account_id"]:
                    account = db.execute("SELECT owner FROM finance_account WHERE account_id=?", (row["account_id"],)).fetchone()
                    if not account or str(account["owner"]) != actor: raise ValueError("account not found")
                db.execute("INSERT INTO finance_entry(entry_id,owner,account_id,direction,amount_cents,currency,booking_date,value_date,description,category,source_type,source_id,status,tax_year,created_at,updated_at) VALUES(:entry_id,:owner,:account_id,:direction,:amount_cents,:currency,:booking_date,:value_date,:description,:category,:source_type,:source_id,:status,:tax_year,:created_at,:updated_at)", row)
                self._audit(db, actor, "entry_created", "entry", row["entry_id"], {"source_type": source_type, "source_id": row["source_id"]})
        except sqlite3.IntegrityError as exc: raise ValueError("source is already linked to another finance entry") from exc
        return row

    def entries(self, actor: str, *, year: int | None = None, is_admin: bool = False, limit: int = 1000) -> list[dict[str, Any]]:
        actor, limit = self._actor(actor), max(1, min(int(limit), 5000)); where, params = [], []
        if not is_admin: where.append("owner=?"); params.append(actor)
        if year is not None: where.append("booking_date>=? AND booking_date<?"); params.extend([f"{int(year):04d}-01-01", f"{int(year)+1:04d}-01-01"])
        sql = "SELECT * FROM finance_entry" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY booking_date DESC,created_at DESC,entry_id DESC LIMIT ?"; params.append(limit)
        with self._db() as db: rows = db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def set_tags(self, entry_id: str, labels: list[str], actor: str) -> list[str]:
        actor = self._actor(actor); normalized: list[str] = []
        for value in labels:
            label = text(value, 80)
            if not label or not TAG_RE.fullmatch(label): raise ValueError("tag is invalid")
            if label.casefold() not in {item.casefold() for item in normalized}: normalized.append(label)
        with self._db() as db:
            entry = db.execute("SELECT owner FROM finance_entry WHERE entry_id=?", (text(entry_id, 100),)).fetchone()
            if not entry or str(entry["owner"]) != actor: raise ValueError("entry not found")
            db.execute("DELETE FROM finance_entry_tag WHERE entry_id=?", (entry_id,))
            for label in normalized:
                tag = db.execute("SELECT tag_id FROM finance_tag WHERE owner=? AND label=? COLLATE NOCASE", (actor, label)).fetchone(); tag_id = str(tag["tag_id"]) if tag else new_id("tag")
                if not tag: db.execute("INSERT INTO finance_tag(tag_id,owner,label,created_at) VALUES(?,?,?,?)", (tag_id, actor, label, _now()))
                db.execute("INSERT INTO finance_entry_tag(entry_id,tag_id) VALUES(?,?)", (entry_id, tag_id))
            self._audit(db, actor, "entry_tags_replaced", "entry", entry_id, {"tags": normalized})
        return normalized

    def replace_allocations(self, entry_id: str, allocations: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
        actor = self._actor(actor); prepared: list[dict[str, Any]] = []; modes: set[str] = set(); total_amount = total_bp = 0
        with self._db() as db:
            entry = db.execute("SELECT owner,amount_cents FROM finance_entry WHERE entry_id=?", (text(entry_id, 100),)).fetchone()
            if not entry or str(entry["owner"]) != actor: raise ValueError("entry not found")
            for value in allocations:
                target_type, target_id = text(value.get("target_type"), 30).casefold(), text(value.get("target_id"), 120)
                if target_type not in ALLOCATION_TARGETS or not target_id: raise ValueError("allocation target is invalid")
                amount, share = value.get("amount_cents"), value.get("share_basis_points")
                if (amount in {None, ""}) == (share in {None, ""}): raise ValueError("allocation requires either amount_cents or share_basis_points")
                row = {"allocation_id": new_id("alloc"), "entry_id": entry_id, "target_type": target_type, "target_id": target_id, "label": text(value.get("label"), 200), "amount_cents": None, "share_basis_points": None, "created_at": _now()}
                if amount not in {None, ""}: row["amount_cents"] = positive_cents(amount, "allocation amount"); total_amount += int(row["amount_cents"]); modes.add("amount")
                else:
                    points = int(share)
                    if points <= 0 or points > 10_000: raise ValueError("share_basis_points is outside the supported range")
                    row["share_basis_points"] = points; total_bp += points; modes.add("share")
                prepared.append(row)
            if len(modes) > 1: raise ValueError("allocation modes cannot be mixed")
            if total_amount > int(entry["amount_cents"]): raise ValueError("allocated amount exceeds entry amount")
            if total_bp > 10_000: raise ValueError("allocated share exceeds 100 percent")
            db.execute("DELETE FROM finance_allocation WHERE entry_id=?", (entry_id,))
            for row in prepared: db.execute("INSERT INTO finance_allocation(allocation_id,entry_id,target_type,target_id,label,amount_cents,share_basis_points,created_at) VALUES(:allocation_id,:entry_id,:target_type,:target_id,:label,:amount_cents,:share_basis_points,:created_at)", row)
            self._audit(db, actor, "entry_allocations_replaced", "entry", entry_id, {"count": len(prepared), "mode": next(iter(modes), "none")})
        return prepared

    def create_obligation(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        actor = self._actor(actor)
        kind = text(values.get("kind", "other"), 30).casefold()
        recurrence = text(values.get("recurrence_unit", "monthly"), 20).casefold()
        direction = text(values.get("direction", "expense"), 20).casefold()
        if kind not in OBLIGATION_KINDS: raise ValueError("obligation kind is invalid")
        if recurrence not in RECURRENCE_UNITS: raise ValueError("recurrence unit is invalid")
        if direction not in {"income", "expense"}: raise ValueError("obligation direction is invalid")
        name = text(values.get("name"), 200)
        if not name: raise ValueError("obligation name is required")
        interval_count = int(values.get("interval_count", 1) or 1)
        if not 1 <= interval_count <= 120: raise ValueError("interval_count is outside the supported range")
        due_raw = values.get("due_day")
        due_day = None if due_raw in {None, ""} else int(due_raw)
        if due_day is not None and not 1 <= due_day <= 31: raise ValueError("due_day is outside the supported range")
        starts_on = iso_date(values.get("starts_on"), "starts_on")
        ends_on = iso_date(values.get("ends_on"), "ends_on", allow_empty=True)
        if ends_on and ends_on < starts_on: raise ValueError("ends_on cannot be before starts_on")
        ts = _now()
        row = {"obligation_id": new_id("obl"), "owner": actor, "name": name, "kind": kind,
               "direction": direction, "amount_cents": positive_cents(values.get("amount_cents")),
               "currency": currency(values.get("currency", "EUR")), "recurrence_unit": recurrence,
               "interval_count": interval_count, "due_day": due_day, "starts_on": starts_on,
               "ends_on": ends_on, "category": text(values.get("category"), 120),
               "counterparty_name": text(values.get("counterparty_name"), 300),
               "counterparty_iban": normalize_iban(values.get("counterparty_iban")),
               "reference": text(values.get("reference"), 300),
               "contract_document_id": text(values.get("contract_document_id"), 200),
               "active": 1, "created_at": ts, "updated_at": ts}
        with self._db() as db:
            db.execute("""INSERT INTO finance_obligation(obligation_id,owner,name,kind,direction,amount_cents,currency,recurrence_unit,interval_count,due_day,starts_on,ends_on,category,counterparty_name,counterparty_iban,reference,contract_document_id,active,created_at,updated_at)
                          VALUES(:obligation_id,:owner,:name,:kind,:direction,:amount_cents,:currency,:recurrence_unit,:interval_count,:due_day,:starts_on,:ends_on,:category,:counterparty_name,:counterparty_iban,:reference,:contract_document_id,:active,:created_at,:updated_at)""", row)
            self._audit(db, actor, "obligation_created", "obligation", row["obligation_id"], {"kind": kind, "recurrence": recurrence})
        return row

    def obligations(self, actor: str, *, active_only: bool = True) -> list[dict[str, Any]]:
        actor = self._actor(actor)
        sql = "SELECT * FROM finance_obligation WHERE owner=?"
        params: list[Any] = [actor]
        if active_only: sql += " AND active=1"
        sql += " ORDER BY name COLLATE NOCASE,obligation_id"
        with self._db() as db: return [dict(row) for row in db.execute(sql, params).fetchall()]

    def confirm_transaction_match(self, transaction_id: str, source_type: str, source_id: str, actor: str, *, score: int = 0, note: str = "") -> dict[str, Any]:
        actor = self._actor(actor); source_type = text(source_type, 40).casefold(); source_id = text(source_id, 200)
        if source_type not in SOURCE_TYPES or source_type in {"manual", "bank_transaction", "internal_allocation"}:
            raise ValueError("match source type is invalid")
        if not source_id: raise ValueError("match source id is required")
        score = max(0, min(int(score), 100))
        with self._db() as db:
            tx = db.execute("""SELECT t.transaction_id FROM finance_bank_transaction t
                               JOIN finance_account a ON a.account_id=t.account_id
                               WHERE t.transaction_id=? AND a.owner=?""", (text(transaction_id, 100), actor)).fetchone()
            if not tx: raise ValueError("bank transaction not found")
            existing = db.execute("SELECT * FROM finance_transaction_match WHERE owner=? AND transaction_id=? AND source_type=? AND source_id=?", (actor, transaction_id, source_type, source_id)).fetchone()
            if existing: return dict(existing)
            row = {"match_id": new_id("match"), "owner": actor, "transaction_id": transaction_id,
                   "source_type": source_type, "source_id": source_id, "score": score,
                   "state": "confirmed", "note": text(note, 500), "created_at": _now()}
            db.execute("""INSERT INTO finance_transaction_match(match_id,owner,transaction_id,source_type,source_id,score,state,note,created_at)
                          VALUES(:match_id,:owner,:transaction_id,:source_type,:source_id,:score,:state,:note,:created_at)""", row)
            self._audit(db, actor, "transaction_match_confirmed", "bank_transaction", transaction_id, {"source_type": source_type, "source_id": source_id, "score": score})
        return row

    def transaction_matches(self, transaction_id: str, actor: str) -> list[dict[str, Any]]:
        actor = self._actor(actor)
        with self._db() as db:
            rows = db.execute("SELECT * FROM finance_transaction_match WHERE owner=? AND transaction_id=? ORDER BY created_at,match_id", (actor, text(transaction_id, 100))).fetchall()
        return [dict(row) for row in rows]

    def save_bank_connection(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        """Persist connection metadata only. PIN and TAN are deliberately unsupported."""
        actor = self._actor(actor)
        forbidden = {"pin", "tan", "password", "secret"}
        if any(text(values.get(key), 1000) for key in forbidden):
            raise ValueError("PIN, TAN and banking secrets must never be stored")
        provider = text(values.get("provider"), 30).casefold()
        login_id = text(values.get("login_id"), 200)
        if not provider or not login_id: raise ValueError("provider and login_id are required")
        status = text(values.get("status", "configured"), 40).casefold()
        if status not in BANK_CONNECTION_STATUSES: raise ValueError("bank connection status is invalid")
        institution = text(values.get("institution"), 200)
        with self._db() as db:
            existing = db.execute("SELECT * FROM finance_bank_connection WHERE owner=? AND provider=? AND institution=? AND login_id=?", (actor, provider, institution, login_id)).fetchone()
            if existing: return dict(existing)
            ts = _now(); row = {"connection_id": new_id("bank"), "owner": actor, "provider": provider,
                "institution": institution, "bank_code": text(values.get("bank_code"), 20), "endpoint": text(values.get("endpoint"), 500), "login_id": login_id,
                "status": status, "last_successful_sync": None, "last_error": "", "created_at": ts, "updated_at": ts}
            db.execute("""INSERT INTO finance_bank_connection(connection_id,owner,provider,institution,endpoint,login_id,status,last_successful_sync,last_error,created_at,updated_at)
                          VALUES(:connection_id,:owner,:provider,:institution,:endpoint,:login_id,:status,:last_successful_sync,:last_error,:created_at,:updated_at)""", row)
            self._audit(db, actor, "bank_connection_created", "bank_connection", row["connection_id"], {"provider": provider, "institution": institution})
        return row

    def bank_connections(self, actor: str) -> list[dict[str, Any]]:
        actor = self._actor(actor)
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM finance_bank_connection WHERE owner=? ORDER BY institution COLLATE NOCASE,connection_id", (actor,)).fetchall()]

    def bank_connection(self, connection_id: str, actor: str) -> dict[str, Any]:
        actor = self._actor(actor)
        with self._db() as db:
            row = db.execute("SELECT * FROM finance_bank_connection WHERE connection_id=? AND owner=?", (text(connection_id, 100), actor)).fetchone()
        if not row: raise ValueError("bank connection not found")
        return dict(row)

    def map_remote_account(self, connection_id: str, remote_account_id: str, account_id: str, remote_iban: str, actor: str) -> dict[str, Any]:
        actor = self._actor(actor); connection = self.bank_connection(connection_id, actor)
        remote_account_id = text(remote_account_id, 200)
        if not remote_account_id: raise ValueError("remote account id is required")
        with self._db() as db:
            account = db.execute("SELECT account_id FROM finance_account WHERE account_id=? AND owner=?", (text(account_id, 100), actor)).fetchone()
            if not account: raise ValueError("account not found")
            row = {"connection_id": connection["connection_id"], "remote_account_id": remote_account_id,
                   "account_id": account_id, "remote_iban": normalize_iban(remote_iban)}
            db.execute("""INSERT INTO finance_bank_connection_account(connection_id,remote_account_id,account_id,remote_iban)
                          VALUES(:connection_id,:remote_account_id,:account_id,:remote_iban)
                          ON CONFLICT(connection_id,remote_account_id) DO UPDATE SET account_id=excluded.account_id,remote_iban=excluded.remote_iban""", row)
            self._audit(db, actor, "bank_account_mapped", "bank_connection", connection_id, {"remote_account_id": remote_account_id, "account_id": account_id})
        return row

    def bank_account_mappings(self, connection_id: str, actor: str) -> list[dict[str, Any]]:
        connection = self.bank_connection(connection_id, actor)
        with self._db() as db:
            rows = db.execute("""SELECT m.*,a.name,a.institution,a.iban,a.currency FROM finance_bank_connection_account m
                                 JOIN finance_account a ON a.account_id=m.account_id WHERE m.connection_id=? ORDER BY a.name COLLATE NOCASE""", (connection["connection_id"],)).fetchall()
        return [dict(row) for row in rows]

    def set_tax_year_status(self, tax_year: int, status: str, actor: str, *, submitted_on: str = "", advisor_note: str = "") -> dict[str, Any]:
        actor = self._actor(actor); tax_year = int(tax_year); status = text(status, 40).casefold()
        if not 1900 <= tax_year <= 2200: raise ValueError("tax year is outside the supported range")
        if status not in TAX_YEAR_STATUSES: raise ValueError("tax year status is invalid")
        submitted = iso_date(submitted_on, "submitted_on", allow_empty=True)
        if status in {"submitted", "assessment_received", "archived"} and not submitted:
            raise ValueError("submitted_on is required after submission")
        row = {"owner": actor, "tax_year": tax_year, "status": status, "submitted_on": submitted,
               "advisor_note": text(advisor_note, 1000), "updated_at": _now()}
        with self._db() as db:
            db.execute("""INSERT INTO finance_tax_year(owner,tax_year,status,submitted_on,advisor_note,updated_at)
                          VALUES(:owner,:tax_year,:status,:submitted_on,:advisor_note,:updated_at)
                          ON CONFLICT(owner,tax_year) DO UPDATE SET status=excluded.status,submitted_on=excluded.submitted_on,advisor_note=excluded.advisor_note,updated_at=excluded.updated_at""", row)
            self._audit(db, actor, "tax_year_status_changed", "tax_year", str(tax_year), {"status": status})
        return row

    def audit(self, actor: str, *, limit: int = 200) -> list[dict[str, Any]]:
        actor, limit = self._actor(actor), max(1, min(int(limit), 1000))
        with self._db() as db: rows = db.execute("SELECT * FROM finance_audit WHERE actor=? ORDER BY audit_id DESC LIMIT ?", (actor, limit)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try: item["data"] = json.loads(item.pop("data_json"))
            except json.JSONDecodeError: item["data"] = {}
            result.append(item)
        return result
