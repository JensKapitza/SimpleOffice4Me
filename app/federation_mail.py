"""Privacy-first federation for persistent mail-index tombstones.

Discovery exchanges fingerprints only.  Subjects, addresses and bodies are not
part of federation lookup responses.  EML bytes are available only for mail
accounts whose owner explicitly enabled export, and every recovered payload is
verified again before it enters the private local archive.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .federation_store import FederationStore
from .federation_worker import _json_request, _request
from .file_lock import exclusive_file_lock
from .mail_client import ImapArchive, MailStore, MAX_MESSAGE_BYTES, _owner_key
from .mail_index import FUZZY_MAX_HAMMING, MIN_FUZZY_CHARS, MailSearchIndex, fingerprints, hamming_distance
from .mail_webclient import _encode_modified_utf7

MAX_LOCATE_QUERIES = 100
MAX_MATCHES_PER_QUERY = 20
MAX_FUZZY_CANDIDATES = 5000
_HASH512_RE = re.compile(r"^[0-9a-f]{128}$")
_SIMHASH_RE = re.compile(r"^[0-9a-f]{16}$")
_LOCATOR_RE = re.compile(r"^[A-Za-z0-9_-]{20,96}$")


def _now() -> int:
    return int(time.time())


def _owner(actor: str) -> str:
    return hashlib.sha256(actor.encode("utf-8")).hexdigest()[:32]


def _hash512(value: Any) -> str:
    value = str(value or "").strip().casefold()
    return value if _HASH512_RE.fullmatch(value) else ""


def _simhash(value: Any) -> str:
    value = str(value or "").strip().casefold()
    return value if _SIMHASH_RE.fullmatch(value) else ""


def _query_id(value: Any) -> str:
    value = str(value or "")[:80]
    return value if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value) else secrets.token_hex(8)


def _mail_index_db(root: Path) -> sqlite3.Connection:
    """Open the same DB used by MailSearchIndex (below CONTROL_DIR/mail)."""
    path = root / CONTROL_DIR / "mail" / "mail-index.sqlite3"
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    return db


class MailFederationPolicy:
    """Explicit per-account export policy; missing entries are deny."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "mail" / "federation-policy.json"
        self.lock = self.path.with_suffix(".lock")

    @staticmethod
    def _key(actor: str, account_id: str) -> str:
        return hashlib.sha256(f"{actor}\0{account_id}".encode("utf-8")).hexdigest()

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"version": 1, "accounts": {}}
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "accounts": {}}

    def export_enabled(self, actor: str, account_id: str) -> bool:
        row = self._read().get("accounts", {}).get(self._key(actor, account_id), {})
        return bool(isinstance(row, dict) and row.get("export_enabled") is True)

    def account_policy(self, actor: str, account_id: str) -> dict[str, Any]:
        row = self._read().get("accounts", {}).get(self._key(actor, account_id), {})
        return dict(row) if isinstance(row, dict) else {}

    def set_export(self, actor: str, account_id: str, enabled: bool, updated_by: str) -> dict[str, Any]:
        actor, account_id = actor.strip(), str(account_id).strip()[:80]
        if not actor or not account_id:
            raise ValueError("owner and account are required")
        with exclusive_file_lock(self.lock):
            payload = self._read()
            payload.setdefault("version", 1)
            payload.setdefault("accounts", {})[self._key(actor, account_id)] = {
                "owner": actor,
                "owner_key": _owner(actor),
                "account_id": account_id,
                "export_enabled": bool(enabled),
                "updated_at": utc_now(),
                "updated_by": str(updated_by)[:160],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json_write(self.path, payload)
        return self.account_policy(actor, account_id)

    def exported_accounts(self) -> list[dict[str, Any]]:
        accounts = self._read().get("accounts", {})
        if not isinstance(accounts, dict):
            return []
        return [
            dict(row) for row in accounts.values()
            if isinstance(row, dict) and row.get("export_enabled") is True
            and row.get("owner") and row.get("owner_key") and row.get("account_id")
        ]

    def owner_for(self, owner_key: str, account_id: str) -> str | None:
        for row in self.exported_accounts():
            if row["owner_key"] == owner_key and row["account_id"] == account_id:
                return str(row["owner"])
        return None


class MailFederationStore:
    """Persist opaque exported locators and learned remote sources."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "mail" / "federation.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS mail_federation_locator(
                    locator TEXT PRIMARY KEY,
                    owner_key TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    message_row_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    UNIQUE(owner_key, account_id, message_row_id)
                );
                CREATE TABLE IF NOT EXISTS mail_federation_source(
                    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_key TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    message_row_id INTEGER NOT NULL,
                    peer_id TEXT NOT NULL,
                    remote_locator TEXT NOT NULL,
                    match_kind TEXT NOT NULL,
                    distance INTEGER NOT NULL DEFAULT 0,
                    confidence INTEGER NOT NULL DEFAULT 0,
                    remote_size INTEGER NOT NULL DEFAULT 0,
                    availability TEXT NOT NULL DEFAULT 'index_only',
                    remote_raw_sha512 TEXT NOT NULL DEFAULT '',
                    remote_content_sha512 TEXT NOT NULL DEFAULT '',
                    remote_simhash64 TEXT NOT NULL DEFAULT '',
                    canonical_chars INTEGER NOT NULL DEFAULT 0,
                    last_seen_at INTEGER NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    recovered_document_id TEXT NOT NULL DEFAULT '',
                    UNIQUE(owner_key, account_id, message_row_id, peer_id, remote_locator)
                );
                CREATE INDEX IF NOT EXISTS mail_fed_source_message
                    ON mail_federation_source(owner_key, account_id, message_row_id, last_seen_at DESC);
                """
            )

    def locator_for(self, owner_key: str, account_id: str, row_id: int) -> str:
        with self._db() as db:
            row = db.execute(
                "SELECT locator FROM mail_federation_locator WHERE owner_key=? AND account_id=? AND message_row_id=?",
                (owner_key, account_id, int(row_id)),
            ).fetchone()
            if row:
                locator = str(row["locator"])
                db.execute("UPDATE mail_federation_locator SET last_seen_at=? WHERE locator=?", (_now(), locator))
                return locator
            locator = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO mail_federation_locator(locator,owner_key,account_id,message_row_id,created_at,last_seen_at) VALUES(?,?,?,?,?,?)",
                (locator, owner_key, account_id, int(row_id), _now(), _now()),
            )
            return locator

    def resolve_locator(self, locator: str) -> dict[str, Any] | None:
        if not _LOCATOR_RE.fullmatch(str(locator or "")):
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM mail_federation_locator WHERE locator=?", (locator,)).fetchone()
            if row:
                db.execute("UPDATE mail_federation_locator SET last_seen_at=? WHERE locator=?", (_now(), locator))
        return dict(row) if row else None

    def save_source(self, actor: str, account_id: str, row_id: int, peer_id: str, match: dict[str, Any]) -> dict[str, Any]:
        locator = str(match.get("locator") or "")
        kind = str(match.get("match_kind") or "")
        availability = str(match.get("availability") or "index_only")
        if not _LOCATOR_RE.fullmatch(locator) or kind not in {"raw", "content", "similar"}:
            raise ValueError("invalid remote mail match")
        if availability not in {"archive", "imap", "index_only"}:
            availability = "index_only"
        with self._db() as db:
            db.execute(
                """INSERT INTO mail_federation_source(
                       owner_key,account_id,message_row_id,peer_id,remote_locator,match_kind,distance,
                       confidence,remote_size,availability,remote_raw_sha512,remote_content_sha512,
                       remote_simhash64,canonical_chars,last_seen_at,last_error
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'')
                   ON CONFLICT(owner_key,account_id,message_row_id,peer_id,remote_locator) DO UPDATE SET
                     match_kind=excluded.match_kind,distance=excluded.distance,confidence=excluded.confidence,
                     remote_size=excluded.remote_size,availability=excluded.availability,
                     remote_raw_sha512=excluded.remote_raw_sha512,
                     remote_content_sha512=excluded.remote_content_sha512,
                     remote_simhash64=excluded.remote_simhash64,
                     canonical_chars=excluded.canonical_chars,last_seen_at=excluded.last_seen_at,last_error=''""",
                (
                    _owner(actor), account_id, int(row_id), str(peer_id)[:128], locator, kind,
                    max(0, min(int(match.get("distance") or 0), 64)),
                    max(0, min(int(match.get("confidence") or 0), 100)),
                    max(0, int(match.get("size") or 0)), availability,
                    _hash512(match.get("raw_sha512")), _hash512(match.get("content_sha512")),
                    _simhash(match.get("simhash64")), max(0, int(match.get("canonical_chars") or 0)), _now(),
                ),
            )
            row = db.execute(
                "SELECT * FROM mail_federation_source WHERE owner_key=? AND account_id=? AND message_row_id=? AND peer_id=? AND remote_locator=?",
                (_owner(actor), account_id, int(row_id), str(peer_id)[:128], locator),
            ).fetchone()
        return dict(row) if row else {}

    def list_sources(self, actor: str, account_id: str, *, message_row_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 2000))
        params: list[Any] = [_owner(actor), account_id]
        condition = ""
        if message_row_id is not None:
            condition = " AND message_row_id=?"
            params.append(int(message_row_id))
        params.append(limit)
        with self._db() as db:
            rows = db.execute(
                f"SELECT * FROM mail_federation_source WHERE owner_key=? AND account_id=?{condition} ORDER BY availability='index_only',confidence DESC,last_seen_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_source(self, actor: str, account_id: str, source_id: int) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM mail_federation_source WHERE source_id=? AND owner_key=? AND account_id=?",
                (int(source_id), _owner(actor), account_id),
            ).fetchone()
        return dict(row) if row else None

    def mark_recovered(self, source_id: int, document_id: str) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE mail_federation_source SET recovered_document_id=?,last_error='',last_seen_at=? WHERE source_id=?",
                (str(document_id)[:240], _now(), int(source_id)),
            )

    def set_source_error(self, source_id: int, error: str) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE mail_federation_source SET last_error=?,last_seen_at=? WHERE source_id=?",
                (str(error)[:500], _now(), int(source_id)),
            )


def _row_by_id(root: Path, owner_key: str, account_id: str, row_id: int) -> dict[str, Any] | None:
    try:
        with _mail_index_db(root) as db:
            row = db.execute(
                "SELECT * FROM mail_message_index WHERE id=? AND owner_key=? AND account_id=?",
                (int(row_id), owner_key, account_id),
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def missing_rows(root: str | Path, actor: str, account_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    root = Path(root).expanduser().resolve()
    try:
        with _mail_index_db(root) as db:
            rows = db.execute(
                "SELECT * FROM mail_message_index WHERE owner_key=? AND account_id=? AND present=0 ORDER BY last_seen_at DESC LIMIT ?",
                (_owner(actor), account_id, max(1, min(int(limit), 2000))),
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []


def _archive_path(root: Path, actor: str, account_id: str, digest: str) -> Path | None:
    digest = _hash512(digest)
    if not digest:
        return None
    base = root / "email" / _owner_key(actor) / account_id
    if not base.is_dir():
        return None
    for path in base.glob(f"*/{digest}.eml"):
        try:
            if path.is_file() and not path.is_symlink() and hashlib.sha512(path.read_bytes()).hexdigest() == digest:
                return path
        except OSError:
            pass
    return None


def _availability(root: Path, actor: str, row: dict[str, Any]) -> str:
    if _archive_path(root, actor, str(row["account_id"]), str(row.get("raw_sha512") or "")):
        return "archive"
    if int(row.get("present") or 0) == 1 and str(row.get("source_kind") or "imap") == "imap":
        return "imap"
    return "index_only"


def locate_local(root: str | Path, queries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    root = Path(root).expanduser().resolve()
    policy = MailFederationPolicy(root)
    exported = policy.exported_accounts()
    empty = {_query_id(query.get("query_id")): [] for query in queries[:MAX_LOCATE_QUERIES]}
    if not exported or not (root / CONTROL_DIR / "mail" / "mail-index.sqlite3").is_file():
        return empty
    locator_store = MailFederationStore(root)
    result: dict[str, list[dict[str, Any]]] = {}
    try:
        db = _mail_index_db(root)
        for query in queries[:MAX_LOCATE_QUERIES]:
            query_id = _query_id(query.get("query_id"))
            raw_hash, content_hash = _hash512(query.get("raw_sha512")), _hash512(query.get("content_sha512"))
            simhash = _simhash(query.get("simhash64"))
            chars = max(0, int(query.get("canonical_chars") or 0))
            found: dict[int, dict[str, Any]] = {}
            for exported_account in exported:
                owner_key, actor = str(exported_account["owner_key"]), str(exported_account["owner"])
                account_id = str(exported_account["account_id"])
                for digest, column, kind in ((raw_hash, "raw_sha512", "raw"), (content_hash, "content_sha512", "content")):
                    if not digest:
                        continue
                    rows = db.execute(
                        f"SELECT * FROM mail_message_index WHERE owner_key=? AND account_id=? AND {column}=? ORDER BY present DESC,last_seen_at DESC LIMIT ?",
                        (owner_key, account_id, digest, MAX_MATCHES_PER_QUERY),
                    ).fetchall()
                    for dbrow in rows:
                        row = dict(dbrow)
                        found.setdefault(int(row["id"]), {"row": row, "actor": actor, "kind": kind, "distance": 0, "confidence": 100})
                if simhash and chars >= MIN_FUZZY_CHARS:
                    rows = db.execute(
                        "SELECT * FROM mail_message_index WHERE owner_key=? AND account_id=? AND simhash64<>'' AND canonical_chars>=? ORDER BY present DESC,last_seen_at DESC LIMIT ?",
                        (owner_key, account_id, MIN_FUZZY_CHARS, MAX_FUZZY_CANDIDATES),
                    ).fetchall()
                    for dbrow in rows:
                        row = dict(dbrow); row_id = int(row["id"])
                        if row_id in found:
                            continue
                        other_chars = max(0, int(row.get("canonical_chars") or 0))
                        if min(chars, other_chars) / max(chars, other_chars, 1) < 0.82:
                            continue
                        distance = hamming_distance(simhash, str(row.get("simhash64") or ""))
                        if distance <= FUZZY_MAX_HAMMING:
                            found[row_id] = {
                                "row": row, "actor": actor, "kind": "similar", "distance": distance,
                                "confidence": max(85, round((64 - distance) / 64 * 100)),
                            }
            output = []
            for match in sorted(found.values(), key=lambda item: (item["kind"] == "raw", item["kind"] == "content", item["confidence"]), reverse=True):
                row = match["row"]
                output.append({
                    "locator": locator_store.locator_for(str(row["owner_key"]), str(row["account_id"]), int(row["id"])),
                    "match_kind": match["kind"], "distance": match["distance"], "confidence": match["confidence"],
                    "size": max(0, int(row.get("size") or 0)), "availability": _availability(root, match["actor"], row),
                    "raw_sha512": _hash512(row.get("raw_sha512")), "content_sha512": _hash512(row.get("content_sha512")),
                    "simhash64": _simhash(row.get("simhash64")), "canonical_chars": max(0, int(row.get("canonical_chars") or 0)),
                })
                if len(output) >= MAX_MATCHES_PER_QUERY:
                    break
            result[query_id] = output
        return result
    except sqlite3.OperationalError:
        return empty
    finally:
        try:
            db.close()
        except Exception:
            pass


def _live_eml(mail_store: MailStore, actor: str, row: dict[str, Any]) -> bytes:
    account = mail_store.account(actor, str(row["account_id"]))
    connection = ImapArchive(mail_store)._connect(account)
    try:
        status, _ = connection.select(_encode_modified_utf7(str(row["folder"])), readonly=True)
        if status != "OK":
            raise FileNotFoundError("mailbox unavailable")
        validity_raw = connection.untagged_responses.get("UIDVALIDITY", [b""])[0]
        validity = validity_raw.decode("ascii", "replace") if isinstance(validity_raw, bytes) else str(validity_raw or "")
        if row.get("uidvalidity") and validity and str(row["uidvalidity"]) != validity:
            raise FileNotFoundError("UIDVALIDITY changed")
        uid = str(row.get("uid") or "")
        if not uid.isdigit():
            raise FileNotFoundError("UID unavailable")
        status, fetched = connection.uid("fetch", uid, "(UID RFC822.SIZE BODY.PEEK[])")
        if status != "OK":
            raise FileNotFoundError("mail unavailable")
        raw = ImapArchive._literal(fetched)
        if not raw or len(raw) > MAX_MESSAGE_BYTES:
            raise FileNotFoundError("mail unavailable")
        expected = _hash512(row.get("raw_sha512"))
        if expected and hashlib.sha512(raw).hexdigest() != expected:
            raise FileNotFoundError("mail changed")
        return raw
    finally:
        try: connection.logout()
        except Exception: pass


def eml_for_locator(root: str | Path, mail_store: MailStore, locator: str) -> tuple[bytes, dict[str, Any]]:
    root = Path(root).expanduser().resolve()
    locator_row = MailFederationStore(root).resolve_locator(locator)
    if not locator_row:
        raise FileNotFoundError("unknown locator")
    owner_key, account_id = str(locator_row["owner_key"]), str(locator_row["account_id"])
    actor = MailFederationPolicy(root).owner_for(owner_key, account_id)
    if not actor:
        raise PermissionError("mail export disabled")
    row = _row_by_id(root, owner_key, account_id, int(locator_row["message_row_id"]))
    if not row:
        raise FileNotFoundError("index row unavailable")
    archived = _archive_path(root, actor, account_id, str(row.get("raw_sha512") or ""))
    if archived is not None:
        return archived.read_bytes(), row
    if int(row.get("present") or 0) != 1 or str(row.get("source_kind") or "imap") != "imap":
        raise FileNotFoundError("only tombstone remains")
    return _live_eml(mail_store, actor, row), row


def _peer_accepts_mail(peer: dict[str, Any]) -> bool:
    policy = peer.get("policy") or {}
    if not isinstance(policy, dict):
        return True
    mail = policy.get("mails")
    return not isinstance(mail, dict) or mail.get("receive") is not False


def discover_missing(root: str | Path, actor: str, account_id: str, *, row_ids: Iterable[int] | None = None) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    rows = missing_rows(root, actor, account_id, limit=1000)
    selected = {int(value) for value in row_ids or [] if int(value) > 0}
    if selected:
        rows = [row for row in rows if int(row["id"]) in selected]
    rows = rows[:MAX_LOCATE_QUERIES]
    if not rows:
        return {"queried": 0, "peers": 0, "matches": 0, "errors": []}
    queries = [{
        "query_id": str(row["id"]), "raw_sha512": _hash512(row.get("raw_sha512")),
        "content_sha512": _hash512(row.get("content_sha512")), "simhash64": _simhash(row.get("simhash64")),
        "canonical_chars": max(0, int(row.get("canonical_chars") or 0)),
    } for row in rows]
    federation, sources = FederationStore(root), MailFederationStore(root)
    peers = matches = 0; errors: list[str] = []
    for peer in federation.list_peers():
        if not peer.get("enabled") or not _peer_accepts_mail(peer):
            continue
        peer_id = str(peer["peer_id"])
        try:
            token = federation.peer_token(peer_id)
            caps = _json_request(peer["base_url"] + "/federation/v1/mails/capabilities", token=token, timeout=15)
            if caps.get("resource") != "mails" or caps.get("fingerprint_lookup") is not True:
                continue
            response = _json_request(peer["base_url"] + "/federation/v1/mails/locate", method="POST", token=token, payload={"queries": queries}, timeout=30)
            values = response.get("matches")
            if not isinstance(values, dict):
                raise ValueError("invalid mail federation response")
            peers += 1
            peer_matches = 0
            for row in rows:
                remote = values.get(str(row["id"]), [])
                if not isinstance(remote, list):
                    continue
                for match in remote[:MAX_MATCHES_PER_QUERY]:
                    if isinstance(match, dict):
                        sources.save_source(actor, account_id, int(row["id"]), peer_id, match)
                        peer_matches += 1; matches += 1
            federation.set_peer_health(peer_id, seen=True)
            federation.record_event("mail_federation_located", peer_id=peer_id, detail={"account_id": account_id, "queries": len(rows), "matches": peer_matches})
        except Exception as exc:
            federation.set_peer_health(peer_id, error=type(exc).__name__)
            errors.append(f"{peer_id}:{type(exc).__name__}")
    return {"queried": len(rows), "peers": peers, "matches": matches, "errors": errors}


def _verify(local_row: dict[str, Any], source: dict[str, Any], raw: bytes) -> dict[str, Any]:
    values = fingerprints(raw); kind = str(source.get("match_kind") or "")
    if kind == "raw":
        if not _hash512(local_row.get("raw_sha512")) or values["raw_sha512"] != _hash512(local_row.get("raw_sha512")):
            raise ValueError("raw SHA-512 mismatch")
    elif kind == "content":
        if not _hash512(local_row.get("content_sha512")) or values["content_sha512"] != _hash512(local_row.get("content_sha512")):
            raise ValueError("content SHA-512 mismatch")
    elif kind == "similar":
        left, right = _simhash(local_row.get("simhash64")), _simhash(values.get("simhash64"))
        left_chars, right_chars = max(0, int(local_row.get("canonical_chars") or 0)), max(0, int(values.get("canonical_chars") or 0))
        if not left or not right or min(left_chars, right_chars) / max(left_chars, right_chars, 1) < 0.82 or hamming_distance(left, right) > FUZZY_MAX_HAMMING:
            raise ValueError("similarity mismatch")
    else:
        raise ValueError("invalid match kind")
    return values


def recover_source(root: str | Path, mail_store: MailStore, actor: str, account_id: str, source_id: int) -> dict[str, Any]:
    root = Path(root).expanduser().resolve(); source_store = MailFederationStore(root)
    source = source_store.get_source(actor, account_id, source_id)
    if not source:
        raise ValueError("unknown federation mail source")
    if source["availability"] == "index_only":
        raise ValueError("peer has index only")
    local_row = _row_by_id(root, _owner(actor), account_id, int(source["message_row_id"]))
    if not local_row:
        raise ValueError("local mail index row missing")
    federation = FederationStore(root); peer = federation.get_peer(str(source["peer_id"]))
    if not peer or not peer.get("enabled") or not _peer_accepts_mail(peer):
        raise ValueError("peer unavailable for mail recovery")
    try:
        with _request(f"{peer['base_url']}/federation/v1/mails/{source['remote_locator']}/eml", token=federation.peer_token(str(peer["peer_id"])), headers={"Accept": "message/rfc822"}, timeout=60) as response:
            raw = response.read(MAX_MESSAGE_BYTES + 1)
        if not raw or len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("invalid federation EML size")
        values = _verify(local_row, source, raw)
        mail_store._owned_row(actor, account_id)
        mail_store.ensure_private_archive(actor, account_id)
        digest = str(values["raw_sha512"]); year = datetime.now(timezone.utc).strftime("%Y")
        relative = f"email/{_owner_key(actor)}/{account_id}/{year}/{digest}.eml"
        documents = DocumentStore(root); target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True); documents.ensure_folder_policy(target.parent, actor)
        if target.is_file() and not target.is_symlink() and hashlib.sha512(target.read_bytes()).hexdigest() == digest:
            document = documents.get_document(relative); duplicate = True
        else:
            document = documents.create_document_at(relative, raw, actor, max_bytes=MAX_MESSAGE_BYTES); duplicate = False
            documents.set_tags(document["document_id"], ["email", "source:federation", f"federation-peer:{peer['peer_id']}", "federation-recovered"], actor)
        documents.set_attribute(document["document_id"], "email_federation_origin", {
            "peer_id": peer["peer_id"], "remote_locator": source["remote_locator"], "match_kind": source["match_kind"],
            "raw_sha512": digest, "recovered_at": utc_now(), "local_index_row": int(local_row["id"]),
        }, actor)
        MailSearchIndex(mail_store).upsert_raw(actor, account_id, f"Federation/{peer['peer_id']}", f"federation:{peer['peer_id']}", digest, raw, source_kind="federation", source_peer=str(peer["peer_id"]), resource_uri=f"sofp://{peer['peer_id']}/mails/{source['remote_locator']}")
        source_store.mark_recovered(int(source["source_id"]), str(document["document_id"]))
        mail_store.history.record("mail_federation_recovered", actor, "mail-archive", str(document["document_id"]), {"account_id": account_id, "peer_id": peer["peer_id"], "match_kind": source["match_kind"], "raw_sha512": digest, "duplicate": duplicate})
        federation.record_event("mail_federation_recovered", peer_id=str(peer["peer_id"]), detail={"account_id": account_id, "match_kind": source["match_kind"], "raw_sha512": digest})
        federation.set_peer_health(str(peer["peer_id"]), seen=True)
        return {"document_id": document["document_id"], "path": relative, "duplicate": duplicate, "peer_id": peer["peer_id"]}
    except Exception as exc:
        source_store.set_source_error(int(source["source_id"]), type(exc).__name__)
        federation.set_peer_health(str(peer["peer_id"]), error=type(exc).__name__)
        raise
