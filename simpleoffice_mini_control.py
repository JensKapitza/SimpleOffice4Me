"""Private local command mailbox for the existing worker, not a service host."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

from simpleoffice_mini_core import state_dir

NETWORK_SERVICES = ("dhcp", "dns", "tftp", "sip", "gateway", "relay")
ACTIONS = ("start", "stop", "restart")


class ControlStore:
    def __init__(self, config_path):
        self.path = state_dir(config_path) / "control.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("Steuerdatei darf kein symbolischer Link sein")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS preferences (service TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, service TEXT NOT NULL, action TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, result TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS scans (service TEXT PRIMARY KEY, data TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def preferences(self):
        with self.connect() as db:
            data = {row["service"]: json.loads(row["data"]) for row in db.execute("SELECT * FROM preferences")}
        return {name: data.get(name, {"enabled": True, "autostart": True}) for name in NETWORK_SERVICES}

    def save_preferences(self, service, value):
        if service not in NETWORK_SERVICES or not isinstance(value, dict) or set(value) != {"enabled", "autostart"} or any(type(v) is not bool for v in value.values()):
            raise ValueError("Aktiviert und Autostart müssen boolesche Werte sein")
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO preferences VALUES (?, ?)", (service, json.dumps(value)))
        return value

    def enqueue(self, service, action):
        if service not in NETWORK_SERVICES or action not in ACTIONS:
            raise ValueError("Unbekannter Dienst oder Befehl")
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Bound storage, including completed history; no credentials/args.
            db.execute("DELETE FROM operations WHERE state IN ('completed','failed') AND id NOT IN (SELECT id FROM operations ORDER BY created DESC LIMIT 100)")
            db.execute("UPDATE operations SET state='failed', result=?, updated=? WHERE state='queued' AND created<?", (json.dumps({"error": "Befehl ist abgelaufen; erneut versuchen."}), now, now - 60))
            if db.execute("SELECT COUNT(*) FROM operations WHERE state IN ('queued','running')").fetchone()[0] >= 32:
                raise ValueError("Zu viele offene Aktionen; kurz warten")
            # Repeated click while pending is idempotent; stop/start order stays intact.
            row = db.execute("SELECT * FROM operations WHERE service=? AND state IN ('queued','running') ORDER BY created DESC LIMIT 1", (service,)).fetchone()
            if row and row["action"] == action:
                return dict(row)
            ident = uuid.uuid4().hex
            db.execute("INSERT INTO operations VALUES (?,?,?,'queued',?,?,NULL)", (ident, service, action, now, now))
        return {"id": ident, "service": service, "action": action, "state": "queued"}

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.execute("UPDATE operations SET state='failed', result=?, updated=? WHERE state='queued' AND created<?", (json.dumps({"error": "Befehl ist abgelaufen; erneut versuchen."}), now, now - 60))
            row = db.execute("SELECT * FROM operations WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE operations SET state='running', updated=? WHERE id=?", (now, row["id"]))
            return dict(row) if row else None

    def finish(self, ident, ok, result):
        with self.connect() as db:
            db.execute("UPDATE operations SET state=?, updated=?, result=? WHERE id=?", ("completed" if ok else "failed", time.time(), json.dumps(result), ident))

    def recover_interrupted(self):
        with self.connect() as db:
            db.execute("UPDATE operations SET state='failed', updated=?, result=? WHERE state IN ('running','queued')", (time.time(), json.dumps({"error": "Worker wurde neu gestartet; Aktion bitte erneut ausführen."})))

    def operation(self, ident):
        with self.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (str(ident)[:64],)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    def scan(self, service, data=None):
        with self.connect() as db:
            if data is not None:
                db.execute("INSERT OR REPLACE INTO scans VALUES (?,?)", (service, json.dumps(data)))
            row = db.execute("SELECT data FROM scans WHERE service=?", (service,)).fetchone()
        return json.loads(row[0]) if row else {"state": "waiting", "count": 0, "updated_at": None, "targets": []}
