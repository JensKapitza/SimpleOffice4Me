"""Persistent audio output nodes, presets and announcement queue."""
from __future__ import annotations

import json
import sqlite3
import time
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


DEFAULT_PRESETS = {
    "gong": {"kind": "sound", "asset": "gong.wav", "priority": 50},
    "doorbell": {"kind": "sound", "asset": "doorbell.wav", "priority": 70},
    "alarm.fire": {"kind": "sound", "asset": "alarm-fire.wav", "priority": 100},
    "alarm.warning": {"kind": "sound", "asset": "alarm-warning.wav", "priority": 90},
    "baby.cry": {"kind": "sound", "asset": "baby-cry.wav", "priority": 60},
}


def _now() -> int:
    return int(time.time())


class AudioOutputStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.root / "audio-output.sqlite3"
        if self.db_path.is_symlink():
            raise ValueError("Audio-Datenbank darf kein symbolischer Link sein")
        try:
            descriptor = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._init_db()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _init_db(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS audio_output_node(
                    node_id TEXT NOT NULL,
                    output_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    device TEXT NOT NULL DEFAULT '',
                    channels INTEGER NOT NULL DEFAULT 2,
                    online INTEGER NOT NULL DEFAULT 1 CHECK(online IN (0,1)),
                    volume INTEGER NOT NULL DEFAULT 100 CHECK(volume BETWEEN 0 AND 100),
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(node_id, output_id)
                );
                CREATE TABLE IF NOT EXISTS audio_output_group(
                    group_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    members_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audio_service_setting(
                    service TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audio_announcement(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL CHECK(kind IN ('tts','sound','stream')),
                    payload_json TEXT NOT NULL,
                    targets_json TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 50 CHECK(priority BETWEEN 0 AND 100),
                    state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','playing','done','failed','cancelled')),
                    source TEXT NOT NULL DEFAULT 'manual',
                    source_ref TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    finished_at INTEGER,
                    error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(audio_announcement)")}
            for column, definition in (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("next_attempt_at", "REAL NOT NULL DEFAULT 0")):
                if column not in columns:
                    db.execute(f"ALTER TABLE audio_announcement ADD COLUMN {column} {definition}")

    @staticmethod
    def _clean_id(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not text or len(text) > 120 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in text):
            raise ValueError(f"{label} ist ungueltig")
        return text

    @staticmethod
    def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{label} muss eine ganze Zahl zwischen {minimum} und {maximum} sein")
        return value

    def register_output(self, node_id: str, output_id: str, name: str, *, device: str = "", channels: int = 2, online: bool = True, volume: int = 100) -> dict:
        node = self._clean_id(node_id, "node_id")
        output = self._clean_id(output_id, "output_id")
        label = str(name or output).strip()[:200]
        channels = self._integer(channels, "Kanäle", 1, 16)
        volume = self._integer(volume, "Lautstärke", 0, 100)
        if type(online) is not bool:
            raise ValueError("Online muss true oder false sein")
        if not isinstance(device, str) or len(device) > 500 or any(ord(ch) < 32 or ord(ch) == 127 for ch in device):
            raise ValueError("Gerät muss Text mit höchstens 500 Zeichen ohne Steuerzeichen sein")
        with self._db() as db:
            db.execute(
                """INSERT INTO audio_output_node(node_id,output_id,name,device,channels,online,volume,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(node_id,output_id) DO UPDATE SET
                     name=excluded.name,device=excluded.device,channels=excluded.channels,
                     online=excluded.online,volume=excluded.volume,updated_at=excluded.updated_at""",
                (node, output, label, device, channels, int(online), volume, _now()),
            )
        return self.output(node, output)

    def output(self, node_id: str, output_id: str) -> dict:
        with self._db() as db:
            row = db.execute("SELECT * FROM audio_output_node WHERE node_id=? AND output_id=?", (node_id, output_id)).fetchone()
        if not row:
            raise KeyError("Audio-Ausgang nicht gefunden")
        result = dict(row)
        result["online"] = bool(result["online"])
        return result

    def outputs(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM audio_output_node ORDER BY node_id,name,output_id").fetchall()
        return [{**dict(row), "online": bool(row["online"])} for row in rows]

    def sync_local_outputs(self, devices: list[dict]) -> list[dict]:
        """Update only discovered local records, preserving names and volume."""
        with self._db() as db:
            db.execute("UPDATE audio_output_node SET online=0 WHERE node_id='local' AND output_id LIKE 'discovered-%'")
            for device in devices[:64]:
                ident = "discovered-" + hashlib.sha256(device["id"].encode()).hexdigest()[:24]
                db.execute("""INSERT INTO audio_output_node(node_id,output_id,name,device,channels,online,volume,updated_at)
                    VALUES('local',?,?,?,2,1,100,?) ON CONFLICT(node_id,output_id) DO UPDATE SET
                    online=1,device=excluded.device,updated_at=excluded.updated_at""",
                    (ident, device["id"][:200], device["id"], _now()))
        return [row for row in self.outputs() if row["node_id"] == "local"]

    def set_group(self, group_id: str, name: str, members: list[str]) -> dict:
        group = self._clean_id(group_id, "group_id")
        if not isinstance(members, list) or len(members) > 128:
            raise ValueError("Gruppen benötigen eine Liste mit höchstens 128 Mitgliedern")
        clean_members = sorted(set(self._clean_id(item, "member") for item in members))
        with self._db() as db:
            db.execute(
                """INSERT INTO audio_output_group(group_id,name,members_json,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(group_id) DO UPDATE SET name=excluded.name,members_json=excluded.members_json,updated_at=excluded.updated_at""",
                (group, str(name or group)[:200], json.dumps(clean_members), _now()),
            )
        return {"group_id": group, "name": str(name or group)[:200], "members": clean_members}

    def groups(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM audio_output_group ORDER BY name,group_id").fetchall()
        return [{"group_id": row["group_id"], "name": row["name"], "members": json.loads(row["members_json"])} for row in rows]

    def queue_tts(self, text: str, targets: list[str], *, priority: int = 50, voice: str = "de_DE", source: str = "manual", source_ref: str = "") -> dict:
        message = " ".join(str(text or "").split()).strip()
        if not message or len(message) > 2000:
            raise ValueError("TTS-Text muss 1 bis 2000 Zeichen enthalten")
        return self._queue("tts", {"text": message, "voice": str(voice or "de_DE")[:80]}, targets, priority, source, source_ref)

    def queue_sound(self, preset: str, targets: list[str], *, priority: int | None = None, source: str = "manual", source_ref: str = "") -> dict:
        name = self._clean_id(preset, "preset")
        if name not in DEFAULT_PRESETS:
            raise ValueError("Unbekannter Audio-Preset")
        definition = DEFAULT_PRESETS[name]
        return self._queue("sound", {"preset": name, "asset": definition["asset"]}, targets, definition["priority"] if priority is None else priority, source, source_ref)

    def _queue(self, kind: str, payload: dict, targets: list[str], priority: int, source: str, source_ref: str) -> dict:
        if not isinstance(targets, list) or len(targets) > 128:
            raise ValueError("Höchstens 128 Audio-Ziele sind erlaubt")
        clean_targets = sorted(set(self._clean_id(item, "target") for item in targets))
        if not clean_targets:
            raise ValueError("Mindestens ein Audio-Ziel ist erforderlich")
        priority = self._integer(priority, "Priorität", 0, 100)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if source_ref:
                existing = db.execute("SELECT id FROM audio_announcement WHERE source=? AND source_ref=? LIMIT 1", (str(source)[:80], str(source_ref)[:200])).fetchone()
                if existing:
                    return self.announcement(existing["id"])
            if db.execute("SELECT COUNT(*) FROM audio_announcement WHERE state IN ('queued','playing')").fetchone()[0] >= 1000:
                raise ValueError("Audio-Warteschlange ist voll; alte Aufträge prüfen")
            cursor = db.execute(
                "INSERT INTO audio_announcement(kind,payload_json,targets_json,priority,source,source_ref,created_at) VALUES(?,?,?,?,?,?,?)",
                (kind, json.dumps(payload, ensure_ascii=False), json.dumps(clean_targets), priority, str(source)[:80], str(source_ref)[:200], _now()),
            )
            announcement_id = int(cursor.lastrowid)
        return self.announcement(announcement_id)

    def announcement(self, announcement_id: int) -> dict:
        with self._db() as db:
            row = db.execute("SELECT * FROM audio_announcement WHERE id=?", (int(announcement_id),)).fetchone()
        if not row:
            raise KeyError("Audio-Ereignis nicht gefunden")
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        result["targets"] = json.loads(result.pop("targets_json"))
        return result

    def pending(self, limit: int = 100) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT id FROM audio_announcement WHERE state='queued' ORDER BY priority DESC,id ASC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
        return [self.announcement(row["id"]) for row in rows]

    def service_settings(self, service: str, value=None):
        if service not in {"sender", "receiver", "announcements"}:
            raise ValueError("Unbekannter Audio-Dienst")
        with self._db() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO audio_service_setting VALUES (?,?)", (service, json.dumps(value)))
            row = db.execute("SELECT data_json FROM audio_service_setting WHERE service=?", (service,)).fetchone()
        return json.loads(row[0]) if row else {}

    def claim(self):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM audio_announcement WHERE state='queued' AND next_attempt_at<=? ORDER BY priority DESC,id LIMIT 1", (time.time(),)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE audio_announcement SET state='playing',started_at=?,attempts=attempts+1 WHERE id=?", (_now(), row["id"]))
        return self.announcement(row["id"])

    def finish(self, ident, *, state="done", error="", retry_seconds=0):
        if state not in {"done", "failed", "cancelled", "queued"}:
            raise ValueError("Ungültiger Auftragsstatus")
        with self._db() as db:
            db.execute("UPDATE audio_announcement SET state=?,error=?,finished_at=?,next_attempt_at=? WHERE id=? AND state='playing'",
                (state, error[:300], None if state == "queued" else _now(), time.time() + retry_seconds, int(ident)))
            db.execute("DELETE FROM audio_announcement WHERE state IN ('done','failed','cancelled') AND id NOT IN (SELECT id FROM audio_announcement ORDER BY id DESC LIMIT 1000)")

    def cancel(self, ident):
        with self._db() as db:
            changed = db.execute("UPDATE audio_announcement SET state='cancelled',finished_at=? WHERE id=? AND state='queued'", (_now(), int(ident))).rowcount
        return bool(changed)

    def recover_interrupted(self):
        with self._db() as db:
            db.execute("UPDATE audio_announcement SET state='failed',finished_at=?,error=? WHERE state='playing'", (_now(), "Wiedergabe wurde unterbrochen. Vor erneutem Abspielen prüfen."))

    def history(self, limit=100):
        with self._db() as db:
            rows = db.execute("SELECT id FROM audio_announcement ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 1000)),)).fetchall()
        return [self.announcement(row["id"]) for row in rows]

    def local_targets(self, targets):
        groups = {group["group_id"]: group["members"] for group in self.groups()}
        outputs = self.outputs()
        selected = {}
        expanded = set()
        def expand(target, visited):
            if target in visited or len(visited) > 8:
                raise ValueError("Zyklische oder zu tief verschachtelte Audiogruppe")
            if target in groups:
                if target in expanded:
                    return
                for member in groups[target]:
                    expand(member, visited | {target})
                expanded.add(target)
                return
            matches = [row for row in outputs if row["output_id"] == target]
            if len(matches) != 1:
                raise ValueError("Audio-Ziel ist nicht vorhanden oder nicht eindeutig")
            row = matches[0]
            if row["node_id"] != "local":
                raise ValueError("Für diesen externen Audioknoten ist kein Wiedergabe-Transport eingerichtet")
            if not row["online"]:
                raise OSError("Lokaler Audio-Ausgang ist offline")
            selected[target] = row
            if len(selected) > 16:
                raise ValueError("Höchstens 16 lokale Ausgänge pro Auftrag")
        for target in targets:
            expand(target, set())
        if not selected:
            raise ValueError("Kein Audio-Ausgang ausgewählt")
        return list(selected.values())
