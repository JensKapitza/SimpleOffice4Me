"""Persistent audio output nodes, presets and announcement queue."""
from __future__ import annotations

import json
import sqlite3
import time
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
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "audio-output.sqlite3"
        self._init_db()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path)
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

    @staticmethod
    def _clean_id(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not text or len(text) > 120 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in text):
            raise ValueError(f"{label} ist ungueltig")
        return text

    def register_output(self, node_id: str, output_id: str, name: str, *, device: str = "", channels: int = 2, online: bool = True, volume: int = 100) -> dict:
        node = self._clean_id(node_id, "node_id")
        output = self._clean_id(output_id, "output_id")
        label = str(name or output).strip()[:200]
        channels = max(1, min(int(channels), 16))
        volume = max(0, min(int(volume), 100))
        with self._db() as db:
            db.execute(
                """INSERT INTO audio_output_node(node_id,output_id,name,device,channels,online,volume,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(node_id,output_id) DO UPDATE SET
                     name=excluded.name,device=excluded.device,channels=excluded.channels,
                     online=excluded.online,volume=excluded.volume,updated_at=excluded.updated_at""",
                (node, output, label, str(device)[:500], channels, int(bool(online)), volume, _now()),
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

    def set_group(self, group_id: str, name: str, members: list[str]) -> dict:
        group = self._clean_id(group_id, "group_id")
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
        clean_targets = sorted(set(self._clean_id(item, "target") for item in targets))
        if not clean_targets:
            raise ValueError("Mindestens ein Audio-Ziel ist erforderlich")
        priority = max(0, min(int(priority), 100))
        with self._db() as db:
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
