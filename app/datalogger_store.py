"""Persistent, permission-aware time-series storage for the data logger."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


KINDS = {"manual", "linux", "file", "http_json", "lm_sensors"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DataLoggerStore:
    def __init__(self, document_root: str | Path):
        self.root = Path(document_root).resolve()
        self.meta = self.root / ".simpleoffice-meta"
        self.meta.mkdir(parents=True, exist_ok=True)
        self.path = self.meta / "datalogger.sqlite3"
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metric_channel (
                  channel_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                  unit TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '#0d6efd', owner TEXT NOT NULL,
                  readers TEXT NOT NULL DEFAULT '[]', editors TEXT NOT NULL DEFAULT '[]',
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metric_source (
                  source_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL REFERENCES metric_channel(channel_id) ON DELETE CASCADE,
                  name TEXT NOT NULL, kind TEXT NOT NULL, config TEXT NOT NULL DEFAULT '{}', interval_seconds INTEGER NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 1, next_run_at TEXT, last_run_at TEXT, last_status TEXT, last_error_code TEXT,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS source_due ON metric_source(enabled, next_run_at);
                CREATE TABLE IF NOT EXISTS metric_sample (
                  sample_id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL REFERENCES metric_channel(channel_id) ON DELETE CASCADE,
                  source_id TEXT REFERENCES metric_source(source_id) ON DELETE SET NULL, measured_at TEXT NOT NULL,
                  value REAL NOT NULL, quality TEXT NOT NULL DEFAULT 'good', metadata TEXT NOT NULL DEFAULT '{}',
                  UNIQUE(channel_id, source_id, measured_at)
                );
                CREATE INDEX IF NOT EXISTS sample_channel_time ON metric_sample(channel_id, measured_at DESC);
                CREATE TABLE IF NOT EXISTS metric_event (
                  event_id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, actor TEXT NOT NULL,
                  action TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                  outcome TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS event_target ON metric_event(target_type, target_id, occurred_at DESC);
            """)

    @staticmethod
    def _names(value: str) -> set[str]:
        try:
            data = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return set()
        return {str(item) for item in data if isinstance(item, str)} if isinstance(data, list) else set()

    @classmethod
    def can_read(cls, channel, actor: str, admin: bool = False) -> bool:
        return admin or channel["owner"] == actor or actor in cls._names(channel["readers"]) or actor in cls._names(channel["editors"])

    @classmethod
    def can_edit(cls, channel, actor: str, admin: bool = False) -> bool:
        return admin or channel["owner"] == actor or actor in cls._names(channel["editors"])

    def _event(self, db, actor, action, target_type, target_id, outcome="success", detail=None):
        db.execute("INSERT INTO metric_event(occurred_at,actor,action,target_type,target_id,outcome,detail) VALUES(?,?,?,?,?,?,?)",
                   (utc_now(), actor, action, target_type, target_id, outcome, json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)))

    def create_channel(self, name: str, owner: str, description="", unit="", color="#0d6efd", readers=(), editors=()) -> str:
        name = name.strip()[:160]
        if not name:
            raise ValueError("Ein Name ist erforderlich.")
        if not color.startswith("#") or len(color) != 7:
            color = "#0d6efd"
        channel_id, now = str(uuid.uuid4()), utc_now()
        with self.connect() as db:
            db.execute("INSERT INTO metric_channel VALUES(?,?,?,?,?,?,?,?,?,?)", (channel_id, name, description.strip()[:2000], unit.strip()[:40], color, owner, json.dumps(sorted(set(readers))), json.dumps(sorted(set(editors))), now, now))
            self._event(db, owner, "channel_created", "channel", channel_id)
        return channel_id

    def channel(self, channel_id: str):
        with self.connect() as db:
            return db.execute("SELECT * FROM metric_channel WHERE channel_id=?", (channel_id,)).fetchone()

    def channels_for(self, actor: str, admin=False):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM metric_channel ORDER BY name COLLATE NOCASE").fetchall()
        return [row for row in rows if self.can_read(row, actor, admin)]

    def update_channel(self, channel_id, actor, admin=False, **values):
        channel = self.channel(channel_id)
        if not channel or not self.can_edit(channel, actor, admin):
            raise PermissionError
        readers = sorted(set(values.get("readers", self._names(channel["readers"]))))
        editors = sorted(set(values.get("editors", self._names(channel["editors"]))))
        with self.connect() as db:
            db.execute("UPDATE metric_channel SET name=?,description=?,unit=?,color=?,readers=?,editors=?,updated_at=? WHERE channel_id=?",
                       (str(values.get("name", channel["name"])).strip()[:160], str(values.get("description", channel["description"])).strip()[:2000], str(values.get("unit", channel["unit"])).strip()[:40], str(values.get("color", channel["color"]))[:7], json.dumps(readers), json.dumps(editors), utc_now(), channel_id))
            self._event(db, actor, "channel_updated", "channel", channel_id, detail={"readers": readers, "editors": editors})

    def add_source(self, channel_id, actor, kind, name, config, interval_seconds=60, admin=False):
        channel = self.channel(channel_id)
        if not channel or not self.can_edit(channel, actor, admin):
            raise PermissionError
        if kind not in KINDS or kind == "manual":
            raise ValueError("Unbekannte automatische Quelle.")
        interval = max(10, min(int(interval_seconds), 86400))
        source_id, now = str(uuid.uuid4()), utc_now()
        with self.connect() as db:
            db.execute("INSERT INTO metric_source(source_id,channel_id,name,kind,config,interval_seconds,enabled,next_run_at,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?,?)", (source_id, channel_id, name.strip()[:160] or kind, kind, json.dumps(config, sort_keys=True), interval, now, now, now))
            self._event(db, actor, "source_created", "source", source_id, detail={"channel_id": channel_id, "kind": kind, "interval_seconds": interval})
        return source_id

    def sources(self, channel_id):
        with self.connect() as db:
            return db.execute("SELECT * FROM metric_source WHERE channel_id=? ORDER BY name", (channel_id,)).fetchall()

    def set_source_enabled(self, source_id, actor, enabled, admin=False):
        with self.connect() as db:
            source = db.execute("SELECT * FROM metric_source WHERE source_id=?", (source_id,)).fetchone()
        channel = self.channel(source["channel_id"]) if source else None
        if not channel or not self.can_edit(channel, actor, admin):
            raise PermissionError
        with self.connect() as db:
            db.execute("UPDATE metric_source SET enabled=?,next_run_at=?,updated_at=? WHERE source_id=?", (int(bool(enabled)), utc_now(), utc_now(), source_id))
            self._event(db, actor, "source_enabled" if enabled else "source_disabled", "source", source_id)

    def add_sample(self, channel_id, value, actor, source_id=None, measured_at=None, metadata=None, admin=False):
        channel = self.channel(channel_id)
        if not channel or (source_id is None and not self.can_edit(channel, actor, admin)):
            raise PermissionError
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Messwert muss endlich sein.")
        when = measured_at or utc_now()
        datetime.fromisoformat(when.replace("Z", "+00:00"))
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO metric_sample(channel_id,source_id,measured_at,value,metadata) VALUES(?,?,?,?,?)", (channel_id, source_id, when, number, json.dumps(metadata or {}, ensure_ascii=False)))
            self._event(db, actor, "sample_recorded", "channel", channel_id, detail={"source_id": source_id or "manual"})

    def sample_count(self, channel_id, start=None, end=None):
        clauses, args = ["channel_id=?"], [channel_id]
        if start:
            clauses.append("measured_at>=?")
            args.append(start)
        if end:
            clauses.append("measured_at<=?")
            args.append(end)
        with self.connect() as db:
            return int(db.execute(f"SELECT COUNT(*) FROM metric_sample WHERE {' AND '.join(clauses)}", args).fetchone()[0])

    def samples(self, channel_id, limit=1000, start=None, end=None, offset=0):
        limit = max(1, min(int(limit), 10000))
        offset = max(0, int(offset))
        clauses, args = ["channel_id=?"], [channel_id]
        if start:
            clauses.append("measured_at>=?")
            args.append(start)
        if end:
            clauses.append("measured_at<=?")
            args.append(end)
        args.extend((limit, offset))
        with self.connect() as db:
            rows = db.execute(f"""SELECT sample.measured_at,sample.value,sample.quality,sample.source_id,
                       sample.metadata,source.name AS source_name,source.kind AS source_kind,
                       source.config AS source_config
                  FROM metric_sample AS sample
                  LEFT JOIN metric_source AS source ON source.source_id=sample.source_id
                 WHERE {' AND '.join('sample.' + clause for clause in clauses)}
                 ORDER BY sample.measured_at DESC LIMIT ? OFFSET ?""", args).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            try:
                config = json.loads(item.pop("source_config") or "{}")
            except (TypeError, json.JSONDecodeError):
                config = {}
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["metadata"] = {}
            item["source_name"] = item.get("source_name") or "Manuelle Eingabe"
            item["source_kind"] = item.get("source_kind") or "manual"
            item["source_metric"] = str(config.get("json_path") or config.get("metric") or config.get("path") or "").strip()
            result.append(item)
        return result

    def due_sources(self, limit=100):
        with self.connect() as db:
            return db.execute("SELECT * FROM metric_source WHERE enabled=1 AND (next_run_at IS NULL OR next_run_at<=?) ORDER BY next_run_at LIMIT ?", (utc_now(), limit)).fetchall()

    def finish_source(self, source_id, status, error_code=""):
        from datetime import timedelta
        with self.connect() as db:
            row = db.execute("SELECT interval_seconds FROM metric_source WHERE source_id=?", (source_id,)).fetchone()
            if not row: return
            now = datetime.now(timezone.utc).replace(microsecond=0)
            db.execute("UPDATE metric_source SET last_run_at=?,next_run_at=?,last_status=?,last_error_code=?,updated_at=? WHERE source_id=?", (now.isoformat(), (now + timedelta(seconds=row["interval_seconds"])).isoformat(), status, error_code[:80], now.isoformat(), source_id))
