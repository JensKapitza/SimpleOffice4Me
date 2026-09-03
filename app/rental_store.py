"""SQLite persistence and source validation for rental billing."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from .rental_types import (
    ALLOCATION_METHODS, EDITABLE_STATUSES, LEDGER_KINDS, METRIC_TYPES, SOURCE_KINDS, STATUSES,
    intersection, iso, money, number, parse_date, utc_now,
)


class RentalStoreBase:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / ".simpleoffice-meta"
        self.db_path = self.control / "rental-billing.sqlite3"
        self.approval_root = self.control / "rental-approvals"
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        self.approval_root.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS rental_group(
              group_id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,created_by TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rental_group_unit(
              group_id TEXT NOT NULL,object_id TEXT NOT NULL,label TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,
              PRIMARY KEY(group_id,object_id),FOREIGN KEY(group_id) REFERENCES rental_group(group_id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS rental_tenancy(
              tenancy_id TEXT PRIMARY KEY,object_id TEXT NOT NULL,contact_id TEXT NOT NULL,starts_on TEXT NOT NULL,ends_on TEXT NOT NULL DEFAULT '',
              federation_peer_id TEXT NOT NULL DEFAULT '',contract_document_id TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,created_by TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS rental_tenancy_object_dates ON rental_tenancy(object_id,starts_on,ends_on);
            CREATE TABLE IF NOT EXISTS rental_metric(
              metric_id TEXT PRIMARY KEY,object_id TEXT NOT NULL,metric_type TEXT NOT NULL,value TEXT NOT NULL,valid_from TEXT NOT NULL,valid_to TEXT NOT NULL DEFAULT '',
              source_kind TEXT NOT NULL,source_note TEXT NOT NULL DEFAULT '',source_document_id TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,created_by TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS rental_metric_lookup ON rental_metric(object_id,metric_type,valid_from,valid_to);
            CREATE TABLE IF NOT EXISTS rental_ledger(
              ledger_id TEXT PRIMARY KEY,object_id TEXT NOT NULL,contact_id TEXT NOT NULL,booked_on TEXT NOT NULL,kind TEXT NOT NULL,amount TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '',document_id TEXT NOT NULL DEFAULT '',source_kind TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS rental_ledger_contact_date ON rental_ledger(contact_id,booked_on);
            CREATE TABLE IF NOT EXISTS rental_settlement(
              settlement_id TEXT PRIMARY KEY,label TEXT NOT NULL,year INTEGER NOT NULL,starts_on TEXT NOT NULL,ends_on TEXT NOT NULL,
              group_id TEXT NOT NULL DEFAULT '',object_id TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'draft',version INTEGER NOT NULL DEFAULT 1,
              supersedes_id TEXT NOT NULL DEFAULT '',approved_at TEXT NOT NULL DEFAULT '',approved_by TEXT NOT NULL DEFAULT '',snapshot_sha256 TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,created_by TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS rental_settlement_year ON rental_settlement(year,status);
            CREATE TABLE IF NOT EXISTS rental_cost(
              cost_id TEXT PRIMARY KEY,settlement_id TEXT NOT NULL,cost_group TEXT NOT NULL,description TEXT NOT NULL,amount TEXT NOT NULL,
              starts_on TEXT NOT NULL,ends_on TEXT NOT NULL,allocation_method TEXT NOT NULL,direct_object_id TEXT NOT NULL DEFAULT '',
              source_kind TEXT NOT NULL,source_note TEXT NOT NULL DEFAULT '',source_document_id TEXT NOT NULL DEFAULT '',tenant_visible INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(settlement_id) REFERENCES rental_settlement(settlement_id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS rental_cost_settlement ON rental_cost(settlement_id,cost_group);
            CREATE TABLE IF NOT EXISTS rental_cost_weight(
              cost_id TEXT NOT NULL,object_id TEXT NOT NULL,weight TEXT NOT NULL,source_kind TEXT NOT NULL,source_note TEXT NOT NULL DEFAULT '',
              source_document_id TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,created_by TEXT NOT NULL,
              PRIMARY KEY(cost_id,object_id),FOREIGN KEY(cost_id) REFERENCES rental_cost(cost_id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS rental_export(
              export_id TEXT PRIMARY KEY,settlement_id TEXT NOT NULL,contact_id TEXT NOT NULL,kind TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,
              document_id TEXT NOT NULL DEFAULT '',peer_id TEXT NOT NULL DEFAULT '',transfer_id TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,created_by TEXT NOT NULL);
            """)

    def groups(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM rental_group ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]

    def group(self, group_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM rental_group WHERE group_id=?", (group_id,)).fetchone()
            units = db.execute("SELECT * FROM rental_group_unit WHERE group_id=? AND active=1 ORDER BY label COLLATE NOCASE,object_id", (group_id,)).fetchall()
        if not row: raise ValueError("Unbekannte Mietobjektgruppe")
        result = dict(row); result["units"] = [dict(item) for item in units]; return result

    def create_group(self, name: str, description: str, actor: str) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name: raise ValueError("Gruppenname fehlt")
        group_id, now = str(uuid.uuid4()), utc_now()
        with self._db() as db:
            db.execute("INSERT INTO rental_group VALUES(?,?,?,?,?,?)", (group_id,name[:200],str(description or "").strip()[:2000],now,actor,now))
        self._revision("rental_group_created", actor, "rental-groups", group_id, {"name": name})
        return self.group(group_id)

    def add_group_unit(self, group_id: str, object_id: str, label: str, actor: str) -> None:
        self.group(group_id); self._object(object_id)
        with self._db() as db:
            db.execute("INSERT INTO rental_group_unit(group_id,object_id,label,active) VALUES(?,?,?,1) ON CONFLICT(group_id,object_id) DO UPDATE SET label=excluded.label,active=1", (group_id,object_id,str(label or "")[:200]))
        self._revision("rental_group_unit_added", actor, "rental-groups", group_id, {"object_id": object_id})

    def remove_group_unit(self, group_id: str, object_id: str, actor: str) -> None:
        with self._db() as db: db.execute("UPDATE rental_group_unit SET active=0 WHERE group_id=? AND object_id=?", (group_id,object_id))
        self._revision("rental_group_unit_removed", actor, "rental-groups", group_id, {"object_id": object_id})

    def tenancies(self, object_id: str = "") -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM rental_tenancy" + (" WHERE object_id=?" if object_id else "") + " ORDER BY object_id,starts_on", ((object_id,) if object_id else ())).fetchall()
        return [dict(row) for row in rows]

    def add_tenancy(self, object_id: str, contact_id: str, starts_on: str, ends_on: str, actor: str, *, federation_peer_id: str = "", contract_document_id: str = "", note: str = "") -> dict[str, Any]:
        self._object(object_id); self._contact(contact_id)
        start, end = parse_date(starts_on), parse_date(ends_on, optional=True)
        if end and end < start: raise ValueError("Mietende liegt vor Mietbeginn")
        if contract_document_id: self._document_snapshot(contract_document_id)
        for existing in self.tenancies(object_id):
            old_start = parse_date(existing["starts_on"]); old_end = parse_date(existing["ends_on"], optional=True) or date.max
            if intersection(start, end or date.max, old_start, old_end): raise ValueError("Für das Objekt existiert in diesem Zeitraum bereits ein Mietverhältnis")
        tenancy_id, now = str(uuid.uuid4()), utc_now()
        with self._db() as db:
            db.execute("INSERT INTO rental_tenancy VALUES(?,?,?,?,?,?,?,?,?,?,?)", (tenancy_id,object_id,contact_id,iso(start),iso(end),str(federation_peer_id or "")[:160],str(contract_document_id or ""),str(note or "")[:2000],now,actor,now))
        self._revision("rental_tenancy_created", actor, "rental-tenancies", tenancy_id, {"object_id": object_id, "contact_id": contact_id})
        return next(item for item in self.tenancies(object_id) if item["tenancy_id"] == tenancy_id)

    def metrics(self, object_id: str = "") -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM rental_metric" + (" WHERE object_id=?" if object_id else "") + " ORDER BY object_id,metric_type,valid_from", ((object_id,) if object_id else ())).fetchall()
        return [dict(row) for row in rows]

    def add_metric(self, object_id: str, metric_type: str, value: Any, valid_from: str, valid_to: str, actor: str, *, source_kind: str = "manual", source_note: str = "", source_document_id: str = "") -> dict[str, Any]:
        self._object(object_id)
        if metric_type not in METRIC_TYPES: raise ValueError("Unbekannter Schlüsselwert")
        value_num = number(value)
        if value_num < 0: raise ValueError("Schlüsselwert darf nicht negativ sein")
        start, end = parse_date(valid_from), parse_date(valid_to, optional=True)
        if end and end < start: raise ValueError("Gültigkeitsende liegt vor Beginn")
        for existing in self.metrics(object_id):
            if existing["metric_type"] != metric_type: continue
            old_start = parse_date(existing["valid_from"]); old_end = parse_date(existing["valid_to"], optional=True) or date.max
            if intersection(start,end or date.max,old_start,old_end): raise ValueError("Für diesen Schlüssel existiert bereits ein überlappender Gültigkeitszeitraum")
        self._validate_source(source_kind, source_note, source_document_id)
        metric_id, now = str(uuid.uuid4()), utc_now()
        with self._db() as db:
            db.execute("INSERT INTO rental_metric VALUES(?,?,?,?,?,?,?,?,?,?,?)", (metric_id,object_id,metric_type,str(value_num),iso(start),iso(end),source_kind,str(source_note or "")[:2000],str(source_document_id or ""),now,actor))
        self._revision("rental_metric_created", actor, "rental-metrics", metric_id, {"object_id":object_id,"metric_type":metric_type,"value":str(value_num)})
        return next(item for item in self.metrics(object_id) if item["metric_id"] == metric_id)

    def ledger(self, *, contact_id: str = "", object_id: str = "") -> list[dict[str, Any]]:
        clauses=[]; args=[]
        if contact_id: clauses.append("contact_id=?"); args.append(contact_id)
        if object_id: clauses.append("object_id=?"); args.append(object_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._db() as db: rows=db.execute(f"SELECT * FROM rental_ledger{where} ORDER BY booked_on,created_at",args).fetchall()
        return [dict(row) for row in rows]

    def add_ledger_entry(self, object_id: str, contact_id: str, booked_on: str, kind: str, amount: Any, actor: str, *, note: str = "", document_id: str = "", source_kind: str = "manual") -> dict[str, Any]:
        self._object(object_id); self._contact(contact_id); booked=parse_date(booked_on)
        if kind not in LEDGER_KINDS: raise ValueError("Unbekannte Kontobuchung")
        value=money(amount)
        if kind in {"advance","payment","credit"}: value=-abs(value)
        elif kind in {"opening_balance","charge"}: value=abs(value)
        self._validate_source(source_kind,note,document_id)
        entry_id,now=str(uuid.uuid4()),utc_now()
        with self._db() as db: db.execute("INSERT INTO rental_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?)",(entry_id,object_id,contact_id,iso(booked),kind,str(value),str(note or "")[:2000],str(document_id or ""),source_kind,now,actor))
        self._revision("rental_ledger_entry_created",actor,"rental-ledger",entry_id,{"object_id":object_id,"contact_id":contact_id,"kind":kind,"amount":str(value)})
        return next(item for item in self.ledger(contact_id=contact_id,object_id=object_id) if item["ledger_id"]==entry_id)

    def settlements(self) -> list[dict[str, Any]]:
        with self._db() as db: rows=db.execute("SELECT * FROM rental_settlement ORDER BY year DESC,created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def settlement(self, settlement_id: str) -> dict[str, Any]:
        with self._db() as db: row=db.execute("SELECT * FROM rental_settlement WHERE settlement_id=?",(settlement_id,)).fetchone()
        if not row: raise ValueError("Unbekannte Abrechnung")
        return dict(row)

    def create_settlement(self, label: str, year: int, starts_on: str, ends_on: str, actor: str, *, group_id: str = "", object_id: str = "", supersedes_id: str = "") -> dict[str, Any]:
        start,end=parse_date(starts_on),parse_date(ends_on)
        if end < start: raise ValueError("Abrechnungsende liegt vor Beginn")
        if bool(group_id)==bool(object_id): raise ValueError("Genau eine Gruppe oder ein Einzelobjekt auswählen")
        if group_id: self.group(group_id)
        if object_id: self._object(object_id)
        version=1
        if supersedes_id: version=int(self.settlement(supersedes_id)["version"])+1
        settlement_id,now=str(uuid.uuid4()),utc_now()
        with self._db() as db:
            db.execute("INSERT INTO rental_settlement(settlement_id,label,year,starts_on,ends_on,group_id,object_id,status,version,supersedes_id,created_at,created_by,updated_at) VALUES(?,?,?,?,?,?,?,'draft',?,?,?,?,?)",(settlement_id,str(label or f"Abrechnung {year}")[:240],int(year),iso(start),iso(end),str(group_id or ""),str(object_id or ""),version,str(supersedes_id or ""),now,actor,now))
        self._revision("rental_settlement_created",actor,"rental-settlements",settlement_id,{"year":int(year),"version":version})
        return self.settlement(settlement_id)

    def set_status(self, settlement_id: str, status: str, actor: str) -> dict[str, Any]:
        current=self.settlement(settlement_id)
        if status not in STATUSES: raise ValueError("Unbekannter Abrechnungsstatus")
        if current["status"] in {"approved","sent","corrected","void"} and status not in {"sent","corrected","void"}: raise ValueError("Freigegebene Abrechnung ist unveränderlich")
        with self._db() as db: db.execute("UPDATE rental_settlement SET status=?,updated_at=? WHERE settlement_id=?",(status,utc_now(),settlement_id))
        self._revision("rental_settlement_status",actor,"rental-settlements",settlement_id,{"from":current["status"],"to":status})
        return self.settlement(settlement_id)

    def _require_editable(self, settlement_id: str) -> dict[str, Any]:
        settlement=self.settlement(settlement_id)
        if settlement["status"] not in EDITABLE_STATUSES: raise ValueError("Nur Entwürfe bzw. Abrechnungen in Prüfung dürfen geändert werden")
        return settlement

    def costs(self, settlement_id: str) -> list[dict[str, Any]]:
        with self._db() as db: rows=db.execute("SELECT * FROM rental_cost WHERE settlement_id=? ORDER BY cost_group COLLATE NOCASE,created_at,cost_id",(settlement_id,)).fetchall()
        return [dict(row) for row in rows]

    def add_cost(self, settlement_id: str, cost_group: str, description: str, amount: Any, starts_on: str, ends_on: str, allocation_method: str, actor: str, *, direct_object_id: str = "", source_kind: str = "manual", source_note: str = "", source_document_id: str = "", tenant_visible: bool = True) -> dict[str, Any]:
        self._require_editable(settlement_id)
        if not str(cost_group).strip() or not str(description).strip(): raise ValueError("Kostengruppe und Beschreibung sind erforderlich")
        value=money(amount); start,end=parse_date(starts_on),parse_date(ends_on)
        if value < 0: raise ValueError("Kostenbetrag darf nicht negativ sein")
        if end < start: raise ValueError("Kostenende liegt vor Kostenbeginn")
        if allocation_method not in ALLOCATION_METHODS: raise ValueError("Unbekannter Verteilungsschlüssel")
        if allocation_method=="direct" and (not direct_object_id or direct_object_id not in self._settlement_unit_ids(settlement_id)): raise ValueError("Bei direkter Zuordnung fehlt ein gültiges Objekt")
        self._validate_source(source_kind,source_note,source_document_id)
        cost_id,now=str(uuid.uuid4()),utc_now()
        with self._db() as db: db.execute("INSERT INTO rental_cost VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(cost_id,settlement_id,str(cost_group).strip()[:200],str(description).strip()[:500],str(value),iso(start),iso(end),allocation_method,str(direct_object_id or ""),source_kind,str(source_note or "")[:2000],str(source_document_id or ""),1 if tenant_visible else 0,now,actor))
        self._revision("rental_cost_created",actor,"rental-costs",cost_id,{"settlement_id":settlement_id,"cost_group":cost_group,"amount":str(value),"allocation_method":allocation_method})
        return next(item for item in self.costs(settlement_id) if item["cost_id"]==cost_id)

    def delete_cost(self, settlement_id: str, cost_id: str, actor: str) -> None:
        self._require_editable(settlement_id)
        with self._db() as db:
            if not db.execute("SELECT 1 FROM rental_cost WHERE cost_id=? AND settlement_id=?",(cost_id,settlement_id)).fetchone(): raise ValueError("Kostenposition nicht gefunden")
            db.execute("DELETE FROM rental_cost WHERE cost_id=?",(cost_id,))
        self._revision("rental_cost_deleted",actor,"rental-costs",cost_id,{"settlement_id":settlement_id})

    def manual_weights(self, cost_id: str) -> list[dict[str, Any]]:
        with self._db() as db: rows=db.execute("SELECT * FROM rental_cost_weight WHERE cost_id=? ORDER BY object_id",(cost_id,)).fetchall()
        return [dict(row) for row in rows]

    def set_manual_weight(self, settlement_id: str, cost_id: str, object_id: str, weight: Any, actor: str, *, source_kind: str = "manual", source_note: str = "", source_document_id: str = "") -> None:
        self._require_editable(settlement_id)
        cost=next((item for item in self.costs(settlement_id) if item["cost_id"]==cost_id),None)
        if not cost or cost["allocation_method"]!="manual": raise ValueError("Kostenposition verwendet keinen manuellen Schlüssel")
        if object_id not in self._settlement_unit_ids(settlement_id): raise ValueError("Objekt gehört nicht zur Abrechnung")
        value=number(weight)
        if value < 0: raise ValueError("Schlüsselwert darf nicht negativ sein")
        self._validate_source(source_kind,source_note,source_document_id)
        with self._db() as db: db.execute("INSERT INTO rental_cost_weight VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(cost_id,object_id) DO UPDATE SET weight=excluded.weight,source_kind=excluded.source_kind,source_note=excluded.source_note,source_document_id=excluded.source_document_id,created_at=excluded.created_at,created_by=excluded.created_by",(cost_id,object_id,str(value),source_kind,str(source_note or "")[:2000],str(source_document_id or ""),utc_now(),actor))
        self._revision("rental_manual_weight_set",actor,"rental-costs",cost_id,{"object_id":object_id,"weight":str(value)})

    def exports(self, settlement_id: str) -> list[dict[str, Any]]:
        with self._db() as db: rows=db.execute("SELECT * FROM rental_export WHERE settlement_id=? ORDER BY created_at DESC",(settlement_id,)).fetchall()
        return [dict(row) for row in rows]

    def record_export(self, settlement_id: str, contact_id: str, kind: str, path: Path, actor: str, *, document_id: str = "", peer_id: str = "", transfer_id: str = "") -> dict[str, Any]:
        settlement=self.settlement(settlement_id)
        if settlement["status"] not in {"approved","sent","corrected"}: raise ValueError("Exporte sind vor Freigabe gesperrt")
        export_id=str(uuid.uuid4()); digest=self._sha256_file(path); now=utc_now()
        with self._db() as db:
            db.execute("INSERT INTO rental_export VALUES(?,?,?,?,?,?,?,?,?,?,?)",(export_id,settlement_id,contact_id,kind[:80],str(path),digest,document_id,peer_id,transfer_id,now,actor))
            if kind in {"federation","tenant_delivery"}: db.execute("UPDATE rental_settlement SET status=CASE WHEN status='approved' THEN 'sent' ELSE status END,updated_at=? WHERE settlement_id=?",(now,settlement_id))
        self._revision("rental_settlement_exported",actor,"rental-settlements",settlement_id,{"export_id":export_id,"kind":kind,"contact_id":contact_id,"sha256":digest,"peer_id":peer_id,"transfer_id":transfer_id})
        return next(row for row in self.exports(settlement_id) if row["export_id"]==export_id)

    def _object(self, object_id: str) -> dict[str, Any]:
        from .object_store import ObjectStore
        return ObjectStore(self.root).object(object_id)

    def _contact(self, contact_id: str) -> dict[str, Any]:
        from .contact_store import ContactStore
        return ContactStore(self.root).get(contact_id)

    def _safe_document_path(self, relative: str) -> Path:
        candidate=(self.root/str(relative or "")).resolve()
        if self.root not in (candidate,*candidate.parents) or not candidate.is_file() or candidate.is_symlink(): raise ValueError("Belegdatei ist nicht sicher verfügbar")
        return candidate

    def _document_snapshot(self, document_id: str) -> dict[str, Any]:
        from .document_store import DocumentStore
        document=DocumentStore(self.root).get_document(document_id); path=self._safe_document_path(document.get("last_path",""))
        digest=str(document.get("sha256") or "").strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}",digest): digest=self._sha256_file(path)
        return {"document_id":document_id,"path":str(document.get("last_path","")),"name":path.name,"sha256":digest,"size":path.stat().st_size}

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest=hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda:source.read(1024*1024),b""): digest.update(block)
        return digest.hexdigest()

    def _validate_source(self, source_kind: str, source_note: str, source_document_id: str) -> None:
        if source_kind not in SOURCE_KINDS: raise ValueError("Unbekannte Quellenart")
        if source_kind=="manual" and not str(source_note or "").strip(): raise ValueError("Bei Handeingaben ist ein kurzer Herkunfts-/Begründungstext erforderlich")
        if source_kind=="document" and not source_document_id: raise ValueError("Bei Belegquelle fehlt die Dokument-ID")
        if source_document_id: self._document_snapshot(source_document_id)

    def _revision(self, action: str, actor: str, category: str, item_id: str, details: dict[str, Any]) -> None:
        try:
            from .revision_history import RevisionHistory
            RevisionHistory(self.root).record(action,actor or "system",category,item_id,details)
        except Exception:
            pass
