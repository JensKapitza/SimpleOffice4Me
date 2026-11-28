"""Private, append-only SMS archive. No Android, network, mail or deletion operations."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
from time import time as _clock
from pathlib import Path

from .sqlite_utils import connect

MAX_RECORDS = 50
MAX_BACKUP_BYTES = 6 * 1024 * 1024
EXPORT_TTL = 300
_EXPORTS_PER_OWNER = 8
_EXPORTS_TOTAL = 1024
_MAX_ID = (1 << 63) - 1
_MAX_DATE_MS = 253402300799999
_FIELDS = {"source", "provider_id", "date_ms", "direction", "address", "body"}
_VERSION = 1
MANUAL_SOURCE = hashlib.sha256(b"simpleoffice-sms-manual-v1").hexdigest()[:32]


class ArchiveConflict(ValueError):
    """A source row was reused or changed. The earlier archive remains immutable."""


class ArchiveIntegrityError(ValueError):
    """The archive database or backup could not be verified."""


class ArchiveInvalidBackup(ArchiveIntegrityError):
    """The selected recovery file is invalid or belongs to another account."""


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _unique_json_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate recovery file field")
        result[key] = value
    return result


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text(value, maximum: int) -> str:
    if type(value) is not str or len(value) > maximum:
        raise ValueError("SMS text exceeds the allowed length or is invalid")
    try:
        if len(value.encode("utf-8")) > maximum:
            raise ValueError("SMS text exceeds the allowed length or is invalid")
    except UnicodeEncodeError:
        raise ValueError("SMS text must be valid Unicode") from None
    return value


def _key(source, provider_id) -> tuple[str, int]:
    if type(source) is not str or re.fullmatch(r"[a-f0-9]{32}", source) is None:
        raise ValueError("An opaque source enrollment identifier is required")
    if type(provider_id) is not int or not 1 <= provider_id <= _MAX_ID:
        raise ValueError("Invalid SMS provider record ID")
    return source, provider_id


def _message(record) -> dict:
    if type(record) is not dict or record.keys() != _FIELDS:
        raise ValueError("Invalid SMS archive record")
    source, provider_id = _key(record["source"], record["provider_id"])
    stamp, direction = record["date_ms"], record["direction"]
    if type(stamp) is not int or not 0 <= stamp <= _MAX_DATE_MS or direction not in ("inbox", "sent"):
        raise ValueError("Invalid SMS date or direction")
    return {
        "source": source, "provider_id": provider_id, "date_ms": stamp, "direction": direction,
        "address": _text(record["address"], 512), "body": _text(record["body"], 16000),
    }


def manual_message(date_ms: int, direction: str, address: str, body: str) -> dict:
    """Create a stable identity for explicitly supplied text, separate from device IDs."""
    record = _message({"source": MANUAL_SOURCE, "provider_id": 1, "date_ms": date_ms,
                       "direction": direction, "address": address, "body": body})
    if not record["body"].strip() or not record["address"]:
        raise ValueError("Message text and address are required")
    material = {k: record[k] for k in ("date_ms", "direction", "address", "body")}
    record["provider_id"] = (int(_digest(_canonical(material))[:16], 16) % _MAX_ID) + 1
    return record


def _batch(records) -> list[dict]:
    if type(records) not in (list, tuple) or len(records) > MAX_RECORDS:
        raise ValueError("Select at most 50 SMS messages per operation")
    return [_message(record) for record in records]


def _db_path(document_root: str | Path) -> Path:
    root = Path(document_root).resolve()
    directory = root / ".simpleoffice-meta"
    directory.mkdir(exist_ok=True, parents=True, mode=0o700)
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise OSError("SMS archive metadata directory must be a real directory")
    path = directory / "sms-archive.sqlite3"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError("SMS archive must be a private regular database file")
        if os.name == "posix" and metadata.st_mode & 0o077:
            raise OSError("SMS archive database permissions are too broad")
    else:
        os.close(descriptor)
    return path


class SmsArchive:
    """Instantiate with the authenticated principal, not a client-selected owner."""

    def __init__(self, document_root: str | Path, owner: str):
        self.owner = _text(owner, 256)
        if not self.owner or "\x00" in self.owner:
            raise ValueError("An authenticated owner is required")
        self._path = _db_path(document_root)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS archived_sms (
                    owner TEXT NOT NULL, source TEXT NOT NULL, provider_id INTEGER NOT NULL,
                    date_ms INTEGER NOT NULL, direction TEXT NOT NULL, address TEXT NOT NULL,
                    body TEXT NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY (owner, source, provider_id)
                ) WITHOUT ROWID
            """)
            db.execute("CREATE INDEX IF NOT EXISTS sms_owner_date ON archived_sms(owner, date_ms DESC)")

    @staticmethod
    def _ensure_exports(db):
        db.execute("""
            CREATE TABLE IF NOT EXISTS sms_archive_export (
                ticket_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, session_hash TEXT NOT NULL,
                expires_at INTEGER NOT NULL, selection TEXT NOT NULL
            ) WITHOUT ROWID
        """)
        db.execute("CREATE INDEX IF NOT EXISTS sms_archive_export_expiry ON sms_archive_export(expires_at)")
        db.execute("CREATE INDEX IF NOT EXISTS sms_archive_export_owner ON sms_archive_export(owner,expires_at)")

    def prepare_export(self, keys, session_key: str, *, now: int | None = None) -> str:
        """Persist only checked selection references so WebView can fetch by GET."""
        items = [(row["source"], row["provider_id"]) for row in self.selected(keys)]
        if not session_key:
            raise ValueError("An authenticated download session is required")
        ticket = secrets.token_hex(24)
        instant = int(_clock()) if now is None else now
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_exports(db)
            db.execute("DELETE FROM sms_archive_export WHERE expires_at<=?", (instant,))
            db.execute(
                """DELETE FROM sms_archive_export WHERE ticket_hash IN (
                       SELECT ticket_hash FROM sms_archive_export WHERE owner=?
                       ORDER BY expires_at DESC, ticket_hash DESC LIMIT -1 OFFSET ?
                   )""",
                (self.owner, _EXPORTS_PER_OWNER - 1),
            )
            if db.execute("SELECT COUNT(*) FROM sms_archive_export").fetchone()[0] >= _EXPORTS_TOTAL:
                raise OSError("SMS download preparation is at capacity")
            db.execute("INSERT INTO sms_archive_export VALUES(?,?,?,?,?)",
                       (_digest(ticket.encode()), self.owner, _digest(session_key.encode()),
                        instant + EXPORT_TTL, _canonical(items).decode()))
        return ticket

    def download_export(self, ticket: str, session_key: str, *, now: int | None = None) -> bytes:
        """Reauthorize an unexpired opaque ticket; WebView and the native saver may both fetch it."""
        if not re.fullmatch(r"[0-9a-f]{48}", ticket) or not session_key:
            raise ValueError("SMS download not found")
        instant = int(_clock()) if now is None else now
        with self._connect() as db:
            self._ensure_exports(db)
            row = db.execute(
                "SELECT selection FROM sms_archive_export "
                "WHERE ticket_hash=? AND owner=? AND session_hash=? AND expires_at>?",
                (_digest(ticket.encode()), self.owner, _digest(session_key.encode()), instant),
            ).fetchone()
        if row is None:
            raise ValueError("SMS download not found")
        return self.export(json.loads(row["selection"]))

    def _connect(self):
        db = connect(str(self._path), timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _stored(self, db, source, provider_id):
        return db.execute(
            "SELECT source,provider_id,date_ms,direction,address,body,digest FROM archived_sms "
            "WHERE owner=? AND source=? AND provider_id=?",
            (self.owner, source, provider_id),
        ).fetchone()

    @staticmethod
    def _checked(row) -> dict:
        try:
            value = _message({key: row[key] for key in _FIELDS})
            digest = row["digest"]
            if not isinstance(digest, str) or not digest.isascii():
                raise ValueError("Invalid SMS checksum")
            if not hmac.compare_digest(digest, _digest(_canonical(value))):
                raise ValueError("SMS checksum mismatch")
            return value
        except (KeyError, IndexError, TypeError, ValueError):
            raise ArchiveIntegrityError("Archived SMS failed its integrity check") from None

    def append(self, records) -> int:
        """Write all or none; exact replays do not create duplicates."""
        records = _batch(records)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            added = 0
            for now in records:
                previous = self._stored(db, now["source"], now["provider_id"])
                if previous is not None:
                    if self._checked(previous) != now:
                        raise ArchiveConflict("SMS source row differs from its earlier archive")
                    continue
                db.execute(
                    "INSERT INTO archived_sms(owner,source,provider_id,date_ms,direction,address,body,digest) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (self.owner, now["source"], now["provider_id"], now["date_ms"], now["direction"],
                     now["address"], now["body"], _digest(_canonical(now))),
                )
                added += 1
        return added

    def states(self, records) -> list[str]:
        """Mark a visible source page without falsely marking a reused provider ID."""
        records = _batch(records)
        with self._connect() as db:
            db.execute("BEGIN")
            states = []
            for record in records:
                row = self._stored(db, record["source"], record["provider_id"])
                if row is None:
                    states.append("unarchived")
                else:
                    states.append("archived" if self._checked(row) == record else "conflict")
        return states

    def page(self, *, query: str = "", direction: str = "", first_ms: int | None = None, last_ms: int | None = None,
             limit: int = MAX_RECORDS, offset: int = 0) -> list[dict]:
        query = _text(query, 256)
        if type(limit) is not int or not 1 <= limit <= MAX_RECORDS or type(offset) is not int or not 0 <= offset <= _MAX_ID:
            raise ValueError("Invalid SMS archive page")
        first_ms = 0 if first_ms is None else first_ms
        last_ms = _MAX_DATE_MS if last_ms is None else last_ms
        if (direction not in ("", "inbox", "sent") or type(first_ms) is not int or type(last_ms) is not int
                or not 0 <= first_ms <= last_ms <= _MAX_DATE_MS):
            raise ValueError("Invalid SMS archive filters")
        with self._connect() as db:
            db.create_function("sms_casefold", 1, str.casefold, deterministic=True)
            rows = db.execute(
                "SELECT source,provider_id,date_ms,direction,address,body,digest FROM archived_sms "
                "WHERE owner=? AND date_ms BETWEEN ? AND ? AND (?='' OR direction=?) "
                "AND (?='' OR instr(sms_casefold(address),?) OR instr(sms_casefold(body),?)) "
                "ORDER BY date_ms DESC, source, provider_id LIMIT ? OFFSET ?",
                (self.owner, first_ms, last_ms, direction, direction, query, query.casefold(), query.casefold(), limit, offset),
            )
            return [self._checked(row) for row in rows]

    def selected(self, keys) -> list[dict]:
        """Load and check only an explicitly selected batch belonging to this account."""
        if type(keys) not in (list, tuple) or not 1 <= len(keys) <= MAX_RECORDS:
            raise ValueError("Select 1 to 50 archived SMS messages")
        selected = []
        for key in keys:
            if type(key) not in (list, tuple) or len(key) != 2:
                raise ValueError("Invalid SMS selection")
            selected.append(_key(*key))
        records = []
        with self._connect() as db:
            db.execute("BEGIN")
            for source, provider_id in dict.fromkeys(selected):
                row = self._stored(db, source, provider_id)
                if row is None:
                    raise ValueError("A selected message is not in this archive")
                records.append(self._checked(row))
        return records

    def export(self, keys) -> bytes:
        """Produce a verified, versioned recovery file for an explicitly selected batch."""
        payload = {"version": _VERSION, "owner": self.owner, "records": self.selected(keys)}
        return _canonical({"payload": payload, "sha256": _digest(_canonical(payload))})

    def _recovery_records(self, backup: bytes) -> list[dict]:
        """Decode only a bounded, checksummed recovery file for this account."""
        if type(backup) is not bytes or len(backup) > MAX_BACKUP_BYTES:
            raise ArchiveInvalidBackup("Invalid SMS archive recovery file size")
        try:
            envelope = json.loads(backup, object_pairs_hook=_unique_json_fields)
            if type(envelope) is not dict or envelope.keys() != {"payload", "sha256"}:
                raise ValueError
            payload, digest = envelope["payload"], envelope["sha256"]
            if type(payload) is not dict or payload.keys() != {"version", "owner", "records"}:
                raise ValueError
            if type(payload["version"]) is not int or payload["version"] != _VERSION or payload["owner"] != self.owner:
                raise ValueError
            if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                raise ValueError
            records = _batch(payload["records"])
            if not records or not hmac.compare_digest(digest, _digest(_canonical(payload))):
                raise ValueError
        except (TypeError, ValueError, RecursionError):
            raise ArchiveInvalidBackup("Invalid or mismatched SMS archive recovery file") from None
        unique = {}
        for record in records:
            key = (record["source"], record["provider_id"])
            if unique.setdefault(key, record) != record:
                raise ArchiveConflict("Recovery contains conflicting records for the same SMS")
        return list(unique.values())

    def verify_backup(self, backup: bytes) -> dict[str, int]:
        """Check a selected file against a read-only owned archive snapshot."""
        states = self.states(self._recovery_records(backup))
        return {"matching": states.count("archived"), "missing": states.count("unarchived"),
                "conflicting": states.count("conflict")}

    def restore(self, backup: bytes) -> int:
        """Restore valid records without overwriting any existing entry."""
        return self.append(self._recovery_records(backup))
