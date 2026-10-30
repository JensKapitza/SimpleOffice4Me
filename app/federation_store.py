"""Persistent peer, transfer and replay state for SOFP federation."""
from __future__ import annotations

import json
import sqlite3
from .sqlite_utils import connect as sqlite_connect
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .document_store import CONTROL_DIR
from .federation_core import bitmap_decode, bitmap_encode, normalize_sha256, sanitize_peer_id
from .security_controls import protect_value, unprotect_value


class PeerBanned(ValueError):
    """A persisted moderation ban blocks transfer progress."""


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
        db = sqlite_connect(self.path, timeout=30)
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
                CREATE TABLE IF NOT EXISTS federation_peer_compatibility(
                    peer_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    checked_at INTEGER NOT NULL,
                    FOREIGN KEY(peer_id) REFERENCES federation_peer(peer_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS federation_network_diagnostic(
                    peer_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    source TEXT NOT NULL,
                    checked_at INTEGER NOT NULL,
                    last_success_at INTEGER,
                    status TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(peer_id,direction,source),
                    FOREIGN KEY(peer_id) REFERENCES federation_peer(peer_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS federation_diagnostic_attempt(
                    peer_id TEXT NOT NULL,
                    attempted_at INTEGER NOT NULL,
                    FOREIGN KEY(peer_id) REFERENCES federation_peer(peer_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS federation_diagnostic_attempt_time_idx
                    ON federation_diagnostic_attempt(peer_id,attempted_at);
                CREATE TABLE IF NOT EXISTS federation_network_diagnostic_history(
                    diagnostic_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    source TEXT NOT NULL,
                    checked_at INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(peer_id) REFERENCES federation_peer(peer_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS federation_network_diagnostic_history_key_idx
                    ON federation_network_diagnostic_history(peer_id,direction,source,diagnostic_id DESC);
                CREATE TABLE IF NOT EXISTS federation_network_manual_confirmation(
                    peer_id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    confirmed_at INTEGER NOT NULL,
                    FOREIGN KEY(peer_id) REFERENCES federation_peer(peer_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS federation_network_manual_history(
                    confirmation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    confirmed_at INTEGER NOT NULL,
                    FOREIGN KEY(peer_id) REFERENCES federation_peer(peer_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS federation_network_manual_history_peer_idx
                    ON federation_network_manual_history(peer_id,confirmation_id DESC);
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
            from .federation_moderation_schema import SCHEMA
            db.executescript(SCHEMA)

    def banned_peer_ids(self, *, now: int | None = None) -> set[str]:
        with self._db() as db:
            rows = db.execute(
                "SELECT DISTINCT peer_id FROM federation_ban WHERE expires_at IS NULL OR expires_at>?",
                (_now() if now is None else int(now),),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def _apply_ban(self, row, banned):
        peer = self._peer(row)
        peer["configured_enabled"] = peer["enabled"]
        peer["banned"] = peer["peer_id"] in banned
        peer["enabled"] = peer["enabled"] and not peer["banned"]
        return peer

    def list_peers(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM federation_peer ORDER BY label COLLATE NOCASE, peer_id").fetchall()
        banned = self.banned_peer_ids()
        return [self._apply_ban(row, banned) for row in rows]

    def get_peer(self, peer_id: str) -> dict[str, Any] | None:
        peer_id = sanitize_peer_id(peer_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM federation_peer WHERE peer_id=?", (peer_id,)).fetchone()
        return self._apply_ban(row, self.banned_peer_ids()) if row else None

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
                    _json(policy if policy is not None else {}),
                    current.get("created_at", timestamp) if current else timestamp,
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

    def set_peer_compatibility(self, peer_id: str, result: dict[str, Any]) -> None:
        peer_id = sanitize_peer_id(peer_id)
        clean = result if isinstance(result, dict) else {}
        with self._db() as db:
            db.execute(
                """INSERT INTO federation_peer_compatibility(peer_id,result_json,checked_at)
                   VALUES(?,?,?)
                   ON CONFLICT(peer_id) DO UPDATE SET
                     result_json=excluded.result_json, checked_at=excluded.checked_at""",
                (peer_id, _json(clean), _now()),
            )

    def peer_compatibility(self, peer_id: str) -> dict[str, Any]:
        peer_id = sanitize_peer_id(peer_id)
        with self._db() as db:
            row = db.execute(
                "SELECT result_json,checked_at FROM federation_peer_compatibility WHERE peer_id=?",
                (peer_id,),
            ).fetchone()
        if row is None:
            return {}
        result = _load(row["result_json"], {})
        if not isinstance(result, dict):
            result = {}
        return {**result, "checked_at": int(row["checked_at"])}

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

    def claim_diagnostic_attempt(
        self, peer_id: str, *, limit: int = 10, window_seconds: int = 60,
        now: int | None = None,
    ) -> bool:
        peer_id = sanitize_peer_id(peer_id)
        timestamp = _now() if now is None else int(now)
        limit = max(1, min(int(limit), 60))
        window_seconds = max(1, min(int(window_seconds), 3600))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM federation_diagnostic_attempt WHERE attempted_at<?",
                (timestamp - window_seconds,),
            )
            count = db.execute(
                "SELECT COUNT(*) FROM federation_diagnostic_attempt WHERE peer_id=? AND attempted_at>=?",
                (peer_id, timestamp - window_seconds),
            ).fetchone()[0]
            if int(count) >= limit:
                return False
            db.execute(
                "INSERT INTO federation_diagnostic_attempt(peer_id,attempted_at) VALUES(?,?)",
                (peer_id, timestamp),
            )
        return True

    def record_network_diagnostic(
        self, peer_id: str, *, direction: str, source: str, status: str,
        category: str = "", latency_ms: int = 0, detail: dict[str, Any] | None = None,
        checked_at: int | None = None,
    ) -> dict[str, Any]:
        peer_id = sanitize_peer_id(peer_id)
        if direction not in {"inbound", "outbound"} or status not in {"success", "failure"}:
            raise ValueError("invalid federation diagnostic state")
        source = str(source or "")[:128]
        if not source:
            raise ValueError("diagnostic source is required")
        timestamp = _now() if checked_at is None else int(checked_at)
        clean_detail = detail if isinstance(detail, dict) else {}
        serialized = _json(clean_detail)
        if len(serialized.encode("utf-8")) > 2048:
            raise ValueError("federation diagnostic detail is too large")
        with self._db() as db:
            db.execute(
                """INSERT INTO federation_network_diagnostic(
                       peer_id,direction,source,checked_at,last_success_at,status,category,latency_ms,detail_json
                   ) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(peer_id,direction,source) DO UPDATE SET
                       checked_at=excluded.checked_at,
                       last_success_at=CASE WHEN excluded.status='success' THEN excluded.checked_at
                                            ELSE federation_network_diagnostic.last_success_at END,
                       status=excluded.status,category=excluded.category,
                       latency_ms=excluded.latency_ms,detail_json=excluded.detail_json""",
                (
                    peer_id, direction, source, timestamp,
                    timestamp if status == "success" else None,
                    status, str(category or "")[:80], max(0, min(int(latency_ms), 300_000)), serialized,
                ),
            )
            db.execute(
                """INSERT INTO federation_network_diagnostic_history(
                       peer_id,direction,source,checked_at,status,category,latency_ms,detail_json
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (peer_id, direction, source, timestamp, status, str(category or "")[:80],
                 max(0, min(int(latency_ms), 300_000)), serialized),
            )
            db.execute(
                """DELETE FROM federation_network_diagnostic_history
                   WHERE peer_id=? AND direction=? AND source=? AND diagnostic_id NOT IN (
                       SELECT diagnostic_id FROM federation_network_diagnostic_history
                       WHERE peer_id=? AND direction=? AND source=?
                       ORDER BY diagnostic_id DESC LIMIT 20
                   )""",
                (peer_id, direction, source, peer_id, direction, source),
            )
        return self.network_diagnostic(peer_id, direction=direction, source=source) or {}

    def network_diagnostic(
        self, peer_id: str, *, direction: str, source: str = "local",
    ) -> dict[str, Any] | None:
        peer_id = sanitize_peer_id(peer_id)
        if direction not in {"inbound", "outbound"}:
            raise ValueError("invalid federation diagnostic direction")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM federation_network_diagnostic WHERE peer_id=? AND direction=? AND source=?",
                (peer_id, direction, str(source or "")[:128]),
            ).fetchone()
        if row is None:
            return None
        return {
            "peer_id": str(row["peer_id"]), "direction": str(row["direction"]),
            "source": str(row["source"]), "checked_at": int(row["checked_at"]),
            "last_success_at": int(row["last_success_at"]) if row["last_success_at"] is not None else None,
            "status": str(row["status"]), "category": str(row["category"]),
            "latency_ms": int(row["latency_ms"]), "detail": _load(row["detail_json"], {}),
        }

    def network_diagnostics(self, peer_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        ids = list(dict.fromkeys(sanitize_peer_id(value) for value in peer_ids if str(value or "")))
        if not ids:
            return {}
        with self._db() as db:
            db.execute("CREATE TEMP TABLE selected_diagnostic_peers(peer_id TEXT PRIMARY KEY)")
            db.executemany(
                "INSERT INTO selected_diagnostic_peers(peer_id) VALUES(?)",
                ((peer_id,) for peer_id in ids),
            )
            rows = db.execute(
                """SELECT diagnostic.* FROM federation_network_diagnostic AS diagnostic
                   JOIN selected_diagnostic_peers AS selected USING(peer_id)
                   ORDER BY diagnostic.peer_id,diagnostic.direction,diagnostic.source""",
            ).fetchall()
        result = {peer_id: [] for peer_id in ids}
        for row in rows:
            detail = _load(row["detail_json"], {})
            result[str(row["peer_id"])].append({
                "direction": str(row["direction"]), "source": str(row["source"]),
                "checked_at": int(row["checked_at"]),
                "last_success_at": int(row["last_success_at"]) if row["last_success_at"] is not None else None,
                "status": str(row["status"]), "category": str(row["category"]),
                "latency_ms": int(row["latency_ms"]), "detail": detail if isinstance(detail, dict) else {},
            })
        return result

    def network_diagnostic_history(
        self, peer_id: str, *, direction: str, source: str = "local", limit: int = 5,
    ) -> list[dict[str, Any]]:
        peer_id = sanitize_peer_id(peer_id)
        if direction not in {"inbound", "outbound"}:
            raise ValueError("invalid federation diagnostic direction")
        limit = max(1, min(int(limit), 20))
        with self._db() as db:
            rows = db.execute(
                """SELECT checked_at,status,category,latency_ms,detail_json
                   FROM federation_network_diagnostic_history
                   WHERE peer_id=? AND direction=? AND source=?
                   ORDER BY diagnostic_id DESC LIMIT ?""",
                (peer_id, direction, str(source or "")[:128], limit),
            ).fetchall()
        return [
            {
                "checked_at": int(row["checked_at"]), "status": str(row["status"]),
                "category": str(row["category"]), "latency_ms": int(row["latency_ms"]),
                "detail": _load(row["detail_json"], {}),
            }
            for row in rows
        ]

    def record_manual_network_confirmation(
        self, peer_id: str, *, actor: str, outcome: str, confirmed_at: int | None = None,
    ) -> dict[str, Any]:
        peer_id = sanitize_peer_id(peer_id)
        actor = str(actor or "").strip()[:128]
        if not actor or outcome not in {"reachable", "unreachable"}:
            raise ValueError("invalid manual network confirmation")
        timestamp = _now() if confirmed_at is None else int(confirmed_at)
        with self._db() as db:
            db.execute(
                """INSERT INTO federation_network_manual_confirmation(peer_id,actor,outcome,confirmed_at)
                   VALUES(?,?,?,?) ON CONFLICT(peer_id) DO UPDATE SET
                   actor=excluded.actor,outcome=excluded.outcome,confirmed_at=excluded.confirmed_at""",
                (peer_id, actor, outcome, timestamp),
            )
            db.execute(
                "INSERT INTO federation_network_manual_history(peer_id,actor,outcome,confirmed_at) VALUES(?,?,?,?)",
                (peer_id, actor, outcome, timestamp),
            )
            db.execute(
                """DELETE FROM federation_network_manual_history WHERE peer_id=? AND confirmation_id NOT IN (
                   SELECT confirmation_id FROM federation_network_manual_history
                   WHERE peer_id=? ORDER BY confirmation_id DESC LIMIT 20
                )""",
                (peer_id, peer_id),
            )
        return self.manual_network_confirmation(peer_id) or {}

    def manual_network_confirmation(self, peer_id: str) -> dict[str, Any] | None:
        peer_id = sanitize_peer_id(peer_id)
        with self._db() as db:
            row = db.execute(
                "SELECT actor,outcome,confirmed_at FROM federation_network_manual_confirmation WHERE peer_id=?",
                (peer_id,),
            ).fetchone()
        if row is None:
            return None
        return {"actor": str(row["actor"]), "outcome": str(row["outcome"]), "confirmed_at": int(row["confirmed_at"])}

    def manual_network_confirmations(self, peer_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(sanitize_peer_id(value) for value in peer_ids if str(value or "")))
        if not ids:
            return {}
        with self._db() as db:
            db.execute("CREATE TEMP TABLE selected_manual_confirmation_peers(peer_id TEXT PRIMARY KEY)")
            db.executemany(
                "INSERT INTO selected_manual_confirmation_peers(peer_id) VALUES(?)",
                ((peer_id,) for peer_id in ids),
            )
            rows = db.execute(
                """SELECT confirmation.peer_id,confirmation.actor,confirmation.outcome,confirmation.confirmed_at
                   FROM federation_network_manual_confirmation AS confirmation
                   JOIN selected_manual_confirmation_peers AS selected USING(peer_id)""",
            ).fetchall()
        return {
            str(row["peer_id"]): {
                "actor": str(row["actor"]), "outcome": str(row["outcome"]),
                "confirmed_at": int(row["confirmed_at"]),
            }
            for row in rows
        }

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
        blocked = False
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM federation_transfer WHERE transfer_id=?", (transfer_id,)).fetchone()
            if current is None:
                return {}
            values = {key: updates[key] if key in updates else current[key] for key in allowed}
            values["updated_at"] = _now()
            peers = (current["source_peer"], current["target_peer"], values["source_peer"], values["target_peer"])
            banned = db.execute(
                """SELECT 1 FROM federation_ban WHERE peer_id IN (?,?,?,?)
                AND (expires_at IS NULL OR expires_at>?) LIMIT 1""", (*peers, _now()),
            ).fetchone()
            if banned and updates.get("status") not in {"failed", "cancelled"}:
                db.execute("UPDATE federation_transfer SET status='failed',error='peer_banned',updated_at=? WHERE transfer_id=? AND status NOT IN ('complete','cancelled')",
                           (_now(), transfer_id))
                blocked = True
            else:
                db.execute(
                    """UPDATE federation_transfer SET
                           status=?,transferred_bytes=?,have_bitmap=?,final_path=?,error=?,
                           target_url=?,target_peer=?,source_peer=?,updated_at=?
                       WHERE transfer_id=?""",
                    (
                        values["status"], values["transferred_bytes"], values["have_bitmap"],
                        values["final_path"], values["error"], values["target_url"],
                        values["target_peer"], values["source_peer"], values["updated_at"], transfer_id,
                    ),
                )
        if blocked:
            raise PeerBanned("peer_banned")
        self.record_event("transfer_updated", transfer_id=transfer_id, detail={k: v for k, v in updates.items() if k != "have_bitmap"})
        return self.get_transfer(transfer_id) or {}

    def require_transfer_peers_allowed(self, transfer_id: str) -> None:
        with self._db() as db:
            banned = db.execute(
                """SELECT 1 FROM federation_transfer t JOIN federation_ban b
                ON b.peer_id=t.source_peer OR b.peer_id=t.target_peer
                WHERE t.transfer_id=? AND (b.expires_at IS NULL OR b.expires_at>?) LIMIT 1""",
                (transfer_id, _now()),
            ).fetchone()
        if banned:
            raise PeerBanned("peer_banned")

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

    def recent_event_count(
        self,
        action: str,
        *,
        peer_id: str,
        since: int,
    ) -> int:
        with self._db() as db:
            row = db.execute(
                """SELECT COUNT(*) FROM federation_event
                   WHERE action=? AND peer_id=? AND created_at>=?""",
                (str(action)[:160], str(peer_id)[:240], int(since)),
            ).fetchone()
        return int(row[0]) if row else 0

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
            enabled = db.execute(
                """SELECT COUNT(*) FROM federation_peer p WHERE enabled=1 AND NOT EXISTS(
                SELECT 1 FROM federation_ban b WHERE b.peer_id=p.peer_id
                AND (b.expires_at IS NULL OR b.expires_at>?))""", (_now(),),
            ).fetchone()[0]
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
