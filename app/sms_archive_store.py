"""Private, append-only SMS archive. No Android, network, mail or deletion operations."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
from pathlib import Path

from .sqlite_utils import connect

MAX_RECORDS = 50
MAX_BACKUP_BYTES = 6 * 1024 * 1024
_MAX_ID = (1 << 63) - 1
_MAX_DATE_MS = 253402300799999
_FIELDS = {"source", "provider_id", "date_ms", "direction", "address", "body"}
_VERSION = 1


class ArchiveConflict(ValueError):
    """A source row was reused or changed. The earlier archive remains immutable."""


class ArchiveIntegrityError(ValueError):
    """The archive database or backup could not be verified."""


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

    def page(self, *, query: str = "", limit: int = MAX_RECORDS, offset: int = 0) -> list[dict]:
        query = _text(query, 256)
        if type(limit) is not int or not 1 <= limit <= MAX_RECORDS or type(offset) is not int or not 0 <= offset <= _MAX_ID:
            raise ValueError("Invalid SMS archive page")
        with self._connect() as db:
            db.create_function("sms_casefold", 1, str.casefold, deterministic=True)
            rows = db.execute(
                "SELECT source,provider_id,date_ms,direction,address,body,digest FROM archived_sms "
                "WHERE owner=? AND (?='' OR instr(sms_casefold(address),?) OR instr(sms_casefold(body),?)) "
                "ORDER BY date_ms DESC, source, provider_id LIMIT ? OFFSET ?",
                (self.owner, query, query.casefold(), query.casefold(), limit, offset),
            )
            return [self._checked(row) for row in rows]

    def export(self, keys) -> bytes:
        """Produce a verified, versioned recovery file for an explicitly selected batch."""
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
        payload = {"version": _VERSION, "owner": self.owner, "records": records}
        return _canonical({"payload": payload, "sha256": _digest(_canonical(payload))})

    def restore(self, backup: bytes) -> int:
        """Verify a selected recovery file, then restore without overwriting any entry."""
        if type(backup) is not bytes or len(backup) > MAX_BACKUP_BYTES:
            raise ArchiveIntegrityError("Invalid SMS archive recovery file size")
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
            raise ArchiveIntegrityError("Invalid or mismatched SMS archive recovery file") from None
        return self.append(records)
