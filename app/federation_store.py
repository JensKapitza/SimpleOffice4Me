"""Persistent peer, transfer and replay state for SOFP federation."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .document_store import CONTROL_DIR
from .federation_core import bitmap_decode, bitmap_encode, normalize_sha256, sanitize_peer_id
from .security_controls import protect_value, unprotect_value


SCHEMA_VERSION = 1


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except (TypeError, json.JSONDecodeError):
        return default


class FederationStore:
    """SQLite-backed federation state kept below the SimpleOffice control dir."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "federation.sqlite3"
        self.incoming = self.control / "federation-incoming"
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
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
        self.incoming.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS federation_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS federation_peer(
                    peer_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    token_enc TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    policy_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_seen_at INTEGER,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS federation_transfer(
                    transfer_id TEXT PRIMARY KEY,
                    direction TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    blob_hash TEXT NOT NULL,
                    source_peer TEXT NOT NULL DEFAULT '',
                    target_peer TEXT NOT NULL DEFAULT '',
                    target_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    transferred_bytes INTEGER NOT NULL DEFAULT 0,
                    total_chunks INTEGER NOT NULL DEFAULT 0,
                    have_bitmap TEXT NOT NULL DEFAULT '',
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    capability_enc TEXT NOT NULL DEFAULT '',
                    final_path TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS federation_transfer_status_idx
                    ON federation_transfer(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS federation_transfer_blob_idx
                    ON federation_transfer(blob_hash, updated_at DESC);
                CREATE TABLE IF NOT EXISTS federation_event(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transfer_id TEXT NOT NULL DEFAULT '',
                    peer_id TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS federation_event_created_idx
                    ON federation_event(created_at DESC);
                CREATE TABLE IF NOT EXISTS federation_nonce(
                    nonce TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL,
                    claimed_at INTEGER NOT NULL
                );
                """
            )
            db.execute(
                "INSERT OR REPLACE INTO federation_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )

    def list_peers(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM federation_peer ORDER BY label COLLATE NOCASE, peer_id").fetchall()
        return [self._peer(row) for row in rows]

    def get_peer(self, peer_id: str) -> dict[str, Any] | None:
        peer_id = sanitize_peer_id(peer_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM federation_peer WHERE peer_id=?", (peer_id,)).fetchone()
        return self._peer(row) if row else None

    def save_peer(
        self,
        peer_id: str,
        label: str,
        base_url: str,
        token: str,
        policy: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        peer_id = sanitize_peer_id(peer_id)
        label = str(label or peer_id).strip()[:160] or peer_id
        base_url = str(base_url or "").strip().rstrip("/")[:1000]
        if not base_url:
            raise ValueError("Peer-URL fehlt")
        timestamp = _now()
        current = self.get_peer(peer_id)
        token_enc = current.get("token_enc", "") if current else ""
        if token:
            token_enc = protect_value(token, f"federation-peer:{peer_id}")
        with self._db() as db:
            db.execute(
                """INSERT INTO federation_peer(
                       peer_id,label,base_url,token_enc,enabled,policy_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(peer_id) DO UPDATE SET
                     label=excluded.label, base_url=excluded.base_url,
                     token_enc=excluded.token_enc, enabled=excluded.enabled,
                     policy_json=excluded.policy_json, updated_at=excluded.updated_at""",
                (
                    peer_id, label, base_url, token_enc, 1 if enabled else 0,
                    _json(policy or {}), current.get("created_at", timestamp) if current else timestamp,
                    timestamp,
                ),
            )
        self.record_event("peer_saved", peer_id=peer_id, detail={"base_url": base_url, "enabled": enabled})
        return self.get_peer(peer_id) or {}

    def delete_peer(self, peer_id: str) -> None:
        peer_id = sanitize_peer_id(peer_id)
        with self._db() as db:
            db.execute("DELETE FROM federation_peer WHERE peer_id=?", (peer_id,))
        self.record_event("peer_deleted", peer_id=peer_id)

    def peer_token(self, peer_id: str) -> str:
        peer = self.get_peer(peer_id)
        if not peer:
            raise ValueError("Unbekannter Federation-Peer")
        encrypted = str(peer.get("token_enc") or "")
        return unprotect_value(encrypted, f"federation-peer:{peer['peer_id']}")

    def set_peer_health(self, peer_id: str, *, error: str = "", seen: bool = False) -> None:
        peer_id = sanitize_peer_id(peer_id)
        with self._db() as db:
            if seen:
                db.execute(
                    "UPDATE federation_peer SET last_seen_at=?,last_error=?,updated_at=? WHERE peer_id=?",
                    (_now(), str(error)[:1000], _now(), peer_id),
                )
            else:
                db.execute(
                    "UPDATE federation_peer SET last_error=?,updated_at=? WHERE peer_id=?",
                    (str(error)[:1000], _now(), peer_id),
                )

    def create_transfer(
        self,
        transfer_id: str,
        *,
        direction: str,
        operation: str,
        blob_hash: str,
        status: str = "queued",
        source_peer: str = "",
        target_peer: str = "",
        target_url: str = "",
        total_bytes: int = 0,
        total_chunks: int = 0,
        manifest: dict[str, Any] | None = None,
        capability: str = "",
    ) -> dict[str, Any]:
        digest = normalize_sha256(blob_hash)
        timestamp = _now()
        capability_enc = protect_value(capability, f"federation-transfer:{transfer_id}") if capability else ""
        with self._db() as db:
            db.execute(
                """INSERT INTO federation_transfer(
                       transfer_id,direction,operation,blob_hash,source_peer,target_peer,target_url,
                       status,total_bytes,total_chunks,manifest_json,capability_enc,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    transfer_id, direction, operation, digest, source_peer, target_peer,
                    target_url.rstrip("/"), status, max(0, int(total_bytes)), max(0, int(total_chunks)),
                    _json(manifest or {}), capability_enc, timestamp, timestamp,
                ),
            )
        self.record_event("transfer_created", transfer_id=transfer_id, detail={"direction": direction, "status": status})
        return self.get_transfer(transfer_id) or {}

    def get_transfer(self, transfer_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM federation_transfer WHERE transfer_id=?", (transfer_id,)).fetchone()
        if row is None:
            return None
        result = self._transfer(row)
        if include_secret and result.get("capability_enc"):
            result["capability"] = unprotect_value(result["capability_enc"], f"federation-transfer:{transfer_id}")
        return result

    def list_transfers(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM federation_transfer ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._transfer(row) for row in rows]

    def update_transfer(self, transfer_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "status", "transferred_bytes", "have_bitmap", "final_path", "error",
            "target_url", "target_peer", "source_peer",
        }
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return self.get_transfer(transfer_id) or {}
        updates["updated_at"] = _now()
        sql = ",".join(f"{key}=?" for key in updates)
        with self._db() as db:
            db.execute(
                f"UPDATE federation_transfer SET {sql} WHERE transfer_id=?",
                (*updates.values(), transfer_id),
            )
        self.record_event("transfer_updated", transfer_id=transfer_id, detail={k: v for k, v in updates.items() if k != "have_bitmap"})
        return self.get_transfer(transfer_id) or {}

    def set_have(self, transfer_id: str, have: set[int], total_chunks: int) -> dict[str, Any]:
        return self.update_transfer(transfer_id, have_bitmap=bitmap_encode(have, total_chunks))

    def have(self, transfer_id: str) -> set[int]:
        transfer = self.get_transfer(transfer_id)
        if not transfer:
            return set()
        return bitmap_decode(str(transfer.get("have_bitmap") or ""), int(transfer.get("total_chunks") or 0))

    def record_event(
        self,
        action: str,
        *,
        transfer_id: str = "",
        peer_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO federation_event(transfer_id,peer_id,action,detail_json,created_at) VALUES(?,?,?,?,?)",
                (transfer_id, peer_id, str(action)[:160], _json(detail or {}), _now()),
            )

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM federation_event ORDER BY event_id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "event_id": row["event_id"], "transfer_id": row["transfer_id"],
                "peer_id": row["peer_id"], "action": row["action"],
                "detail": _load(row["detail_json"], {}), "created_at": row["created_at"],
            }
            for row in rows
        ]

    def claim_nonce(self, nonce: str, expires_at: int) -> bool:
        nonce = str(nonce or "")[:240]
        if not nonce or int(expires_at) < _now():
            return False
        with self._db() as db:
            db.execute("DELETE FROM federation_nonce WHERE expires_at < ?", (_now(),))
            try:
                db.execute(
                    "INSERT INTO federation_nonce(nonce,expires_at,claimed_at) VALUES(?,?,?)",
                    (nonce, int(expires_at), _now()),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def stats(self) -> dict[str, int]:
        with self._db() as db:
            peers = db.execute("SELECT COUNT(*) FROM federation_peer").fetchone()[0]
            enabled = db.execute("SELECT COUNT(*) FROM federation_peer WHERE enabled=1").fetchone()[0]
            active = db.execute(
                "SELECT COUNT(*) FROM federation_transfer WHERE status IN ('queued','prepared','running','receiving')"
            ).fetchone()[0]
            failed = db.execute("SELECT COUNT(*) FROM federation_transfer WHERE status='failed'").fetchone()[0]
            completed = db.execute("SELECT COUNT(*) FROM federation_transfer WHERE status='complete'").fetchone()[0]
        return {"peers": peers, "enabled_peers": enabled, "active_transfers": active, "failed_transfers": failed, "completed_transfers": completed}

    @staticmethod
    def _peer(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "peer_id": row["peer_id"], "label": row["label"], "base_url": row["base_url"],
            "token_enc": row["token_enc"], "has_token": bool(row["token_enc"]),
            "enabled": bool(row["enabled"]), "policy": _load(row["policy_json"], {}),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "last_seen_at": row["last_seen_at"], "last_error": row["last_error"],
        }

    @staticmethod
    def _transfer(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "transfer_id": row["transfer_id"], "direction": row["direction"],
            "operation": row["operation"], "blob_hash": row["blob_hash"],
            "source_peer": row["source_peer"], "target_peer": row["target_peer"],
            "target_url": row["target_url"], "status": row["status"],
            "total_bytes": row["total_bytes"], "transferred_bytes": row["transferred_bytes"],
            "total_chunks": row["total_chunks"], "have_bitmap": row["have_bitmap"],
            "manifest": _load(row["manifest_json"], {}), "capability_enc": row["capability_enc"],
            "has_capability": bool(row["capability_enc"]), "final_path": row["final_path"],
            "error": row["error"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
