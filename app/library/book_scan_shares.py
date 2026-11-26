"""Narrow local scan publication leases, separate from the managed catalog."""

from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import logging
from pathlib import Path
import os
import sqlite3
from threading import Event, Thread
import time
import uuid
from urllib.parse import urlsplit

from ..federation_local_profile import local_peer_id
from ..federation_store import FederationStore
from ..sqlite_utils import connect
from ..v2.authorization import AuthorizationStore, GrantRight
from ..v2.cutover import load_cutover_state
from ..v2.federation_policy import FederationPolicyStore
from .book_scan_pdf import MAX_REQUEST, ScanError

MAX_TTL = 60 * 60
MAX_ACTIVE = 8
MAX_TOTAL_BYTES = 64 * 1024 * 1024
SCOPE = "book-scan"
MAX_OWNER_ACTIVE = 3
MAX_OWNER_BYTES = 32 * 1024 * 1024
RATE_SECONDS = 15 * 60
MAX_OWNER_NEW = 5
MAX_GLOBAL_NEW = 20
PRUNE_INTERVAL = 60
logger = logging.getLogger(__name__)

_MAINTENANCE = """CREATE TABLE IF NOT EXISTS book_scan_maintenance (
    job TEXT PRIMARY KEY, checked_at INTEGER NOT NULL)"""
_RATE = """CREATE TABLE IF NOT EXISTS book_scan_publish_rate (
    owner_id INTEGER NOT NULL, created_at INTEGER NOT NULL)"""

_TABLE = """CREATE TABLE IF NOT EXISTS book_scan_share(
    offer_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, recipient TEXT NOT NULL,
    digest TEXT NOT NULL, expires_at INTEGER NOT NULL, payload BLOB NOT NULL)"""


def offer_id(raw: str) -> str:
    try:
        value = str(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ScanError("offer", 404) from exc
    if raw != value:
        raise ScanError("offer", 404)
    return value


def scope_ref(digest: str) -> str:
    return f"{SCOPE}:sha256:{digest}"


def supported_storage(root) -> bool:
    # Do not silently create plaintext snapshots in encrypted/V2 deployments.
    try:
        return load_cutover_state(root).mode == "v1"
    except (OSError, ValueError):
        return False


def eligible_peer(root, peer_id: str, direction: str, *, object_ref: str = "") -> dict:
    federation = FederationStore(root)
    try:
        peer = federation.get_peer(peer_id)
    except (TypeError, ValueError) as exc:
        raise ScanError("peer", 403) from exc
    local = local_peer_id()
    if not peer or peer["peer_id"] != peer_id or not peer["enabled"] or peer_id == local or not peer.get("has_token"):
        raise ScanError("peer", 403)
    documents = (peer.get("policy") or {}).get("documents") or {}
    if documents.get(direction) is not True or peer_id in federation.banned_peer_ids():
        raise ScanError("peer", 403)
    try:
        endpoint = urlsplit(peer["base_url"])
        if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password
                or endpoint.query or endpoint.fragment):
            raise ValueError("invalid peer HTTPS")
        policy_path = Path(root) / ".simpleoffice-v2" / "federation-policy.sqlite3"
        if policy_path.is_file() and not FederationPolicyStore(root).decision(
            [local, peer_id], scope="documents", target_peer=peer_id, object_refs=(object_ref,) if object_ref else (),
        ).allowed:
            raise ValueError("blocked route")
    except (ValueError, TypeError, OSError) as exc:
        raise ScanError("peer", 403) from exc
    return peer


def peers_for(root, direction: str) -> list[dict]:
    result = []
    for peer in FederationStore(root).list_peers():
        try:
            eligible_peer(root, peer["peer_id"], direction)
        except ScanError:
            continue
        result.append({"id": peer["peer_id"], "label": peer["label"]})
    return result


class Shares:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / ".simpleoffice-meta" / "book-scan-shares.sqlite"

    def _open(self):
        db = connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        return closing(db)

    def _ensure(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        with self._open() as db, db:
            db.execute(_TABLE)
            db.execute(_RATE)
            db.execute(_MAINTENANCE)
            db.execute("CREATE INDEX IF NOT EXISTS book_scan_share_expiry ON book_scan_share(expires_at)")
            db.execute("CREATE INDEX IF NOT EXISTS book_scan_rate_expiry ON book_scan_publish_rate(created_at)")

    @staticmethod
    def _prune_in(db, now: int) -> int:
        removed = db.execute("DELETE FROM book_scan_share WHERE expires_at<=?", (now,)).rowcount
        db.execute("DELETE FROM book_scan_publish_rate WHERE created_at<=?", (now - RATE_SECONDS,))
        db.execute("INSERT OR REPLACE INTO book_scan_maintenance(job,checked_at) VALUES('expire',?)", (now,))
        return removed

    def prune_if_due(self) -> int:
        """Delete only expired scanner leases, with at most one pass per minute across workers."""
        if not self.path.is_file():
            return 0
        self._ensure()
        now = int(time.time())
        with self._open() as db:
            row = db.execute("SELECT checked_at FROM book_scan_maintenance WHERE job='expire'").fetchone()
            if row and row[0] <= now < row[0] + PRUNE_INTERVAL:
                return 0
        with self._open() as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT checked_at FROM book_scan_maintenance WHERE job='expire'").fetchone()
            if row and row[0] <= now < row[0] + PRUNE_INTERVAL:
                return 0
            return self._prune_in(db, now)

    @staticmethod
    def _check_quota(db, owner, length, replaced):
        count, space, owner_count, owner_space = db.execute(
            """SELECT count(*),coalesce(sum(length(payload)),0),
                      count(CASE WHEN owner_id=? THEN 1 END),
                      coalesce(sum(CASE WHEN owner_id=? THEN length(payload) ELSE 0 END),0)
               FROM book_scan_share WHERE offer_id<>?""",
            (owner, owner, replaced),
        ).fetchone()
        if (count >= MAX_ACTIVE or space + length > MAX_TOTAL_BYTES
                or owner_count >= MAX_OWNER_ACTIVE or owner_space + length > MAX_OWNER_BYTES):
            raise ScanError("quota", 409)
        total, mine = db.execute(
            "SELECT count(*),count(CASE WHEN owner_id=? THEN 1 END) FROM book_scan_publish_rate", (owner,),
        ).fetchone()
        if total >= MAX_GLOBAL_NEW or mine >= MAX_OWNER_NEW:
            raise ScanError("rate", 429)

    def publish(self, owner: int, recipient: str, payload: bytes, expected: str, *, replace: str = "") -> dict:
        if not supported_storage(self.root):
            raise ScanError("storage", 409)
        if not payload or len(payload) > MAX_REQUEST:
            raise ScanError("size", 413)
        digest = sha256(payload).hexdigest()
        if digest != expected:
            raise ScanError("changed", 409)
        ref = scope_ref(digest)
        eligible_peer(self.root, recipient, "send", object_ref=ref)
        self._ensure()
        now = int(time.time())
        auth = AuthorizationStore(self.root)
        grant = None
        previous = offer_id(replace) if replace else ""
        try:
            with self._open() as db, db:
                db.execute("BEGIN IMMEDIATE")
                self._prune_in(db, now)
                if previous and db.execute(
                    "SELECT 1 FROM book_scan_share WHERE offer_id=? AND owner_id=? AND recipient=?",
                    (previous, owner, recipient),
                ).fetchone() is None:
                    raise ScanError("replace", 409)
                old = db.execute(
                    "SELECT offer_id,expires_at FROM book_scan_share WHERE owner_id=? AND recipient=? AND digest=?",
                    (owner, recipient, digest),
                ).fetchone()
                if old and (not previous or previous == old["offer_id"]) and auth.allows(
                    old["offer_id"], subject=recipient, right=GrantRight.READ, object_ref=ref,
                ):
                    return {"offer": old["offer_id"], "recipient": recipient, "sha256": digest,
                            "expires_at": old["expires_at"]}
                if old and previous and previous != old["offer_id"]:
                    raise ScanError("replace", 409)
                self._check_quota(db, owner, len(payload), previous)
                grant = auth.issue_root(issuer=local_peer_id(), subject=recipient, rights=(GrantRight.READ,),
                                        object_refs=(ref,), expires_at=now + MAX_TTL)
                db.execute("INSERT INTO book_scan_share VALUES (?,?,?,?,?,?)",
                           (grant.grant_id, owner, recipient, digest, grant.expires_at, payload))
                db.execute("INSERT INTO book_scan_publish_rate VALUES (?,?)", (owner, now))
                if previous:
                    # The source row is authoritative in addition to the ledger.
                    # Switch old and new access in this one transaction.
                    db.execute("DELETE FROM book_scan_share WHERE offer_id=?", (previous,))
        except (OSError, sqlite3.Error, ValueError):
            if grant is not None:
                auth.revoke(grant.grant_id)
            raise
        if previous:
            try:
                auth.revoke(previous)
            except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
                # Old content is already deleted from the source transaction.
                logger.warning("Scanner ledger retirement failed (%s)", type(exc).__name__)
        return {"offer": grant.grant_id, "recipient": recipient, "sha256": digest, "expires_at": grant.expires_at}

    def list_owned(self, owner: int) -> list[dict]:
        if not self.path.is_file():
            return []
        with self._open() as db:
            rows = db.execute(
                "SELECT offer_id,recipient,digest,expires_at FROM book_scan_share WHERE owner_id=? AND expires_at>?",
                (owner, int(time.time())),
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke(self, owner: int, offer: str):
        offer = offer_id(offer)
        if not self.path.is_file():
            raise ScanError("offer", 404)
        with self._open() as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT offer_id FROM book_scan_share WHERE offer_id=? AND owner_id=?",
                             (offer, owner)).fetchone()
            if row is None:
                raise ScanError("offer", 404)
            # Revoke first. If local deletion fails, the retained bytes are still unshareable.
            AuthorizationStore(self.root).revoke(offer)
            db.execute("DELETE FROM book_scan_share WHERE offer_id=?", (offer,))

    def read(self, recipient: str, offer: str) -> tuple[bytes, str]:
        offer = offer_id(offer)
        if not supported_storage(self.root) or not self.path.is_file():
            raise ScanError("offer", 404)
        with self._open() as db:
            row = db.execute(
                "SELECT digest,length(payload) AS size FROM book_scan_share "
                "WHERE offer_id=? AND recipient=? AND expires_at>?",
                (offer, recipient, int(time.time())),
            ).fetchone()
            if row is None or not 0 < row["size"] <= MAX_REQUEST:
                raise ScanError("offer", 404)
            digest = row["digest"]
            ref = scope_ref(digest)
            eligible_peer(self.root, recipient, "send", object_ref=ref)
            if not AuthorizationStore(self.root).allows(
                offer, subject=recipient, right=GrantRight.READ, object_ref=ref,
            ):
                raise ScanError("offer", 404)
            item = db.execute(
                "SELECT payload FROM book_scan_share WHERE offer_id=? AND recipient=? AND expires_at>?",
                (offer, recipient, int(time.time())),
            ).fetchone()
        if item is None or sha256(item["payload"]).hexdigest() != digest:
            raise ScanError("offer", 404)
        return item["payload"], digest


def start_share_maintenance(root):
    """Immediate restart cleanup and periodic low-frequency maintenance on normal hosts."""
    stop = Event()
    shares = Shares(root)

    def maintain():
        while not stop.is_set():
            try:
                shares.prune_if_due()
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                logger.warning("Scanner share cleanup failed (%s); will retry", type(exc).__name__)
            stop.wait(PRUNE_INTERVAL)

    Thread(target=maintain, name="book-scan-share-cleanup", daemon=True).start()
    return stop
