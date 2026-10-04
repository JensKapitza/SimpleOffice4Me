"""Local bans and explicitly subscribed, signed federation blacklists."""
from __future__ import annotations

import hashlib
import time
import uuid

from .federation_core import canonical_json, sanitize_peer_id
from .federation_identity import FederationIdentity, public_key_fingerprint, verify
from .federation_local_profile import local_peer_id
from .federation_store import FederationStore

LOCAL_SOURCE = "@local"
MAX_BANS = 5000
MAX_REPORTS = 1000
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
SNAPSHOT_TTL = 86400
REVISION_KEY = "moderation_blacklist_revision"


def peer_id(value):
    if not isinstance(value, str) or sanitize_peer_id(value) != value:
        raise ValueError("invalid peer id")
    return value


def reason_text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError("reason must contain 1 to 500 characters")
    if any(ord(char) < 32 for char in value):
        raise ValueError("invalid reason")
    return value.strip()


def _bump_revision(db):
    db.execute(
        """INSERT INTO federation_meta(key,value) VALUES(?, '1')
        ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1""",
        (REVISION_KEY,),
    )


class FederationModerationStore:
    def __init__(self, root):
        self.store = FederationStore(root)
        self.root = self.store.root

    def bans(self):
        with self.store._db() as db:
            rows = db.execute(
                """SELECT * FROM federation_ban WHERE expires_at IS NULL OR expires_at>?
                ORDER BY peer_id,source_peer""", (int(time.time()),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _target(self, target):
        target = peer_id(target)
        if target == local_peer_id():
            raise ValueError("cannot ban this instance")
        return target

    def ban(self, target, reason, *, actor, expires_at=None, published=False):
        target, reason = self._target(target), reason_text(reason)
        now = int(time.time())
        if expires_at is not None and (type(expires_at) is not int or expires_at <= now):
            raise ValueError("invalid ban expiry")
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._put_ban(db, target, reason, actor, now, expires_at, published)
        self._enforce()
        self.store.record_event("peer_banned", peer_id=target, detail={"actor": str(actor)[:160], "published": bool(published)})

    def _put_ban(self, db, target, reason, actor, now, expires_at, published):
        db.execute("DELETE FROM federation_ban WHERE source_peer=? AND expires_at<=?", (LOCAL_SOURCE, now))
        count = db.execute("SELECT COUNT(*) FROM federation_ban WHERE source_peer=?", (LOCAL_SOURCE,)).fetchone()[0]
        exists = db.execute("SELECT 1 FROM federation_ban WHERE source_peer=? AND peer_id=?", (LOCAL_SOURCE, target)).fetchone()
        if count >= MAX_BANS and not exists:
            raise ValueError("blacklist capacity exceeded")
        db.execute(
            """INSERT INTO federation_ban VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(source_peer,peer_id) DO UPDATE SET reason=excluded.reason,
            created_by=excluded.created_by,created_at=excluded.created_at,
            expires_at=excluded.expires_at,published=excluded.published""",
            (LOCAL_SOURCE, target, reason, str(actor)[:160], now, expires_at, int(bool(published))),
        )
        _bump_revision(db)
        self._stop_transfers(db, {target})

    def unban(self, target, *, actor):
        target = peer_id(target)
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM federation_ban WHERE source_peer=? AND peer_id=?", (LOCAL_SOURCE, target))
            _bump_revision(db)
        self.store.record_event("peer_unbanned", peer_id=target, detail={"actor": str(actor)[:160]})

    @staticmethod
    def _stop_transfers(db, targets):
        db.executemany(
            """UPDATE federation_transfer SET status='failed',error='peer_banned',updated_at=?
            WHERE (source_peer=? OR target_peer=?)
            AND status NOT IN ('complete','failed','cancelled')""",
            [(int(time.time()), target, target) for target in targets],
        )

    def _enforce(self):
        # Existing V2 stores stay optional. Removing a ban never resurrects grants/jobs.
        from .v2.authorization import AuthorizationStore
        from .v2.federation_policy import FederationPolicyStore
        from .v2.jobs import FederationJobService, PersistentJobStore
        authorization = None
        if (self.root / '.simpleoffice-v2' / 'authorization.sqlite3').is_file():
            authorization = AuthorizationStore(self.root)
            for target in self.store.banned_peer_ids():
                authorization.revoke_for_peer(target)
        if (self.root / '.simpleoffice-v2' / 'jobs.sqlite3').is_file():
            FederationJobService(PersistentJobStore(self.root)).stop_blocked_jobs(
                policy_store=FederationPolicyStore(self.root), authorization_store=authorization,
            )

    def sources(self):
        with self.store._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM federation_blacklist_source ORDER BY peer_id")]

    def subscribe(self, source, public_key, *, actor):
        source = self._target(source)
        if not isinstance(public_key, str) or len(public_key) > 64:
            raise ValueError("invalid source key")
        # Validate the complete Ed25519 key before pinning it. Discovery cannot replace it.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from .federation_identity import _unb64
        Ed25519PublicKey.from_public_bytes(_unb64(public_key))
        with self.store._db() as db:
            existing = db.execute("SELECT public_key FROM federation_blacklist_source WHERE peer_id=?", (source,)).fetchone()
            if existing and existing["public_key"] != public_key:
                raise ValueError("unsubscribe before replacing a pinned key")
            db.execute("INSERT OR IGNORE INTO federation_blacklist_source(peer_id,public_key) VALUES(?,?)", (source, public_key))
        self.store.record_event("blacklist_source_added", peer_id=source, detail={"actor": str(actor)[:160], "fingerprint": public_key_fingerprint(public_key)})

    def unsubscribe(self, source, *, actor):
        source = peer_id(source)
        with self.store._db() as db:
            db.execute("DELETE FROM federation_blacklist_source WHERE peer_id=?", (source,))
            db.execute("DELETE FROM federation_ban WHERE source_peer=?", (source,))
        self.store.record_event("blacklist_source_removed", peer_id=source, detail={"actor": str(actor)[:160]})

    def snapshot(self):
        now = int(time.time())
        with self.store._db() as db:
            db.execute("BEGIN")
            revision = db.execute("SELECT value FROM federation_meta WHERE key=?", (REVISION_KEY,)).fetchone()
            rows = db.execute(
                """SELECT peer_id,reason,created_at,expires_at FROM federation_ban
                WHERE source_peer=? AND published=1 ORDER BY peer_id""", (LOCAL_SOURCE,),
            ).fetchall()
        payload = {
            "version": 1, "issuer_peer": local_peer_id(), "revision": int(revision[0]) if revision else 0,
            "issued_at": now, "expires_at": now + SNAPSHOT_TTL,
            "bans": [dict(row) for row in rows],
        }
        return {"payload": payload, "signature": FederationIdentity(self.root).sign(canonical_json(payload))}

    def import_snapshot(self, source, envelope):
        source = self._target(source)
        payload, signature = self._validate_snapshot(source, envelope)
        digest = hashlib.sha256(canonical_json(payload["bans"])).hexdigest()
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            pinned = db.execute("SELECT * FROM federation_blacklist_source WHERE peer_id=?", (source,)).fetchone()
            if not pinned or not verify(pinned["public_key"], canonical_json(payload), signature):
                raise ValueError("unknown blacklist source or invalid signature")
            if payload["revision"] < pinned["revision"]:
                raise ValueError("blacklist rollback rejected")
            if payload["revision"] == pinned["revision"] and digest != pinned["digest"]:
                raise ValueError("conflicting blacklist revision")
            db.execute("DELETE FROM federation_ban WHERE source_peer=?", (source,))
            db.executemany(
                "INSERT INTO federation_ban VALUES(?,?,?,?,?,?,0)",
                [(source, row["peer_id"], row["reason"], source, row["created_at"], row["expires_at"]) for row in payload["bans"]],
            )
            db.execute("UPDATE federation_blacklist_source SET revision=?,digest=?,synced_at=? WHERE peer_id=?",
                       (payload["revision"], digest, int(time.time()), source))
            self._stop_transfers(db, {row["peer_id"] for row in payload["bans"] if row["expires_at"] is None or row["expires_at"] > int(time.time())})
        self._enforce()
        self.store.record_event("blacklist_synced", peer_id=source, detail={"revision": payload["revision"], "entries": len(payload["bans"])})
        return len(payload["bans"])

    def _validate_snapshot(self, source, envelope):
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
            raise ValueError("invalid blacklist envelope")
        payload, signature = envelope["payload"], envelope["signature"]
        if not isinstance(payload, dict) or set(payload) != {"version", "issuer_peer", "revision", "issued_at", "expires_at", "bans"}:
            raise ValueError("invalid blacklist payload")
        now = int(time.time())
        if payload["version"] != 1 or payload["issuer_peer"] != source:
            raise ValueError("invalid blacklist issuer")
        if any(type(payload[key]) is not int for key in ("revision", "issued_at", "expires_at")):
            raise ValueError("invalid blacklist times or revision")
        if not 0 <= payload["revision"] < 2 ** 63 or payload["issued_at"] > now + 300 or not now < payload["expires_at"] <= payload["issued_at"] + SNAPSHOT_TTL:
            raise ValueError("expired or invalid blacklist")
        if not isinstance(signature, str) or len(signature) > 128:
            raise ValueError("invalid blacklist signature")
        bans = payload["bans"]
        if not isinstance(bans, list) or len(bans) > MAX_BANS or len(canonical_json(envelope)) > MAX_SNAPSHOT_BYTES:
            raise ValueError("blacklist capacity exceeded")
        seen = set()
        for row in bans:
            if not isinstance(row, dict) or set(row) != {"peer_id", "reason", "created_at", "expires_at"}:
                raise ValueError("invalid blacklist entry")
            target = self._target(row["peer_id"])
            if target == source or target in seen:
                raise ValueError("invalid blacklist target")
            seen.add(target)
            reason_text(row["reason"])
            if type(row["created_at"]) is not int or not 0 <= row["created_at"] <= payload["issued_at"]:
                raise ValueError("invalid ban time")
            expiry = row["expires_at"]
            if expiry is not None and (type(expiry) is not int or not row["created_at"] < expiry <= payload["issued_at"] + 8760 * 3600):
                raise ValueError("invalid ban expiry")
        return payload, signature

    def report(self, reporter, target, reason):
        reporter, target, reason = peer_id(reporter), self._target(target), reason_text(reason)
        if reporter == target:
            raise ValueError("cannot report yourself")
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT report_id FROM federation_peer_report WHERE reporter_peer=? AND peer_id=?", (reporter, target)).fetchone()
            if existing:
                return str(existing[0])
            count = db.execute("SELECT COUNT(*) FROM federation_peer_report").fetchone()[0]
            pending = db.execute("SELECT COUNT(*) FROM federation_peer_report WHERE reporter_peer=? AND status='pending'", (reporter,)).fetchone()[0]
            if count >= MAX_REPORTS or pending >= 50:
                raise ValueError("report capacity exceeded")
            report_id = str(uuid.uuid4())
            db.execute("INSERT INTO federation_peer_report(report_id,reporter_peer,peer_id,reason,created_at) VALUES(?,?,?,?,?)",
                       (report_id, reporter, target, reason, int(time.time())))
        self.store.record_event("peer_reported", peer_id=target, detail={"reporter_peer": reporter, "report_id": report_id})
        return report_id

    def reports(self):
        with self.store._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM federation_peer_report ORDER BY created_at DESC LIMIT ?", (MAX_REPORTS,))]

    def review(self, report_id, decision, *, actor, published=False):
        if decision not in {"approved", "rejected"}:
            raise ValueError("invalid review decision")
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM federation_peer_report WHERE report_id=?", (report_id,)).fetchone()
            if not row or row["status"] != "pending":
                raise ValueError("unknown or already reviewed report")
            if decision == "approved":
                self._put_ban(db, self._target(row["peer_id"]), reason_text(row["reason"]), actor, int(time.time()), None, published)
            db.execute("UPDATE federation_peer_report SET status=?,reviewed_by=? WHERE report_id=? AND status='pending'", (decision, str(actor)[:160], report_id))
        if decision == "approved":
            self._enforce()
        self.store.record_event("peer_report_reviewed", peer_id=row["peer_id"], detail={"report_id": report_id, "decision": decision, "actor": str(actor)[:160]})

    def delete_report(self, report_id, *, actor):
        with self.store._db() as db:
            db.execute("DELETE FROM federation_peer_report WHERE report_id=? AND status<>'pending'", (report_id,))
        self.store.record_event("peer_report_deleted", detail={"report_id": str(report_id)[:36], "actor": str(actor)[:160]})
