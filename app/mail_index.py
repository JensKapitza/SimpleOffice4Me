"""Persistent mail search index and near-duplicate detection.

The index is deliberately not a mirror of the current IMAP state.  Once an
entry has been indexed it remains useful as evidence and as a future federation
locator even when the original target disappears.  Missing targets therefore
become tombstones (``target_not_found``) and are removed only by an explicit
administrator cleanup.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from .document_store import utc_now
from .mail_client import ImapArchive, MailStore, MAX_MESSAGE_BYTES
from .mail_webclient import MailAccountPolicy, MailReadOnlyError, ImapWebClient, _encode_modified_utf7

MAX_INDEX_BODY = 200_000
MAX_INDEX_ROWS = 10_000
MAX_REFRESH_PER_FOLDER = 500
FUZZY_MAX_HAMMING = 5
MIN_FUZZY_CHARS = 80

_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_EMAIL_RE = re.compile(r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_UUID_RE = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b")
_LONG_HEX_RE = re.compile(r"(?i)\b[0-9a-f]{16,}\b")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")
_LONG_NUMBER_RE = re.compile(r"\b\d{5,}\b")
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _owner_key(actor: str) -> str:
    if not actor.strip():
        raise ValueError("a named user is required")
    return hashlib.sha256(actor.encode("utf-8")).hexdigest()[:32]


def _header(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))[:2000]
    except (LookupError, UnicodeError, ValueError):
        return str(value)[:2000]


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "head", "svg"}:
            self.hidden_depth += 1
        if self.hidden_depth == 0 and tag.casefold() in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")
        if self.hidden_depth == 0 and tag.casefold() == "img":
            alt = next((value for key, value in attrs if key.casefold() == "alt" and value), "")
            if alt:
                self.parts.append(str(alt))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "head", "svg"} and self.hidden_depth:
            self.hidden_depth -= 1
        elif self.hidden_depth == 0 and tag.casefold() in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0 and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)


def visible_message_text(message: Any) -> str:
    plain: list[str] = []
    html: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type().casefold()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            text = part.get_content()
        except (LookupError, UnicodeError):
            raw = part.get_payload(decode=True) or b""
            text = raw.decode(part.get_content_charset() or "utf-8", "replace")
        if not isinstance(text, str):
            continue
        if content_type == "text/plain":
            plain.append(text)
        else:
            html.append(text)
    if plain:
        return "\n\n".join(plain)[:MAX_INDEX_BODY]
    if html:
        parser = _VisibleText()
        parser.feed("\n".join(html))
        return parser.text()[:MAX_INDEX_BODY]
    return ""


def normalize_visible_content(text: str) -> str:
    """Normalize visible content while removing common per-delivery noise.

    URLs, addresses, UUIDs, tracking IDs and long counters are placeholders so
    that the same spam campaign delivered through different relays or tracking
    links can still be recognized.  The original message is never modified.
    """
    value = unicodedata.normalize("NFKC", text).casefold()
    value = _URL_RE.sub(" url ", value)
    value = _EMAIL_RE.sub(" email ", value)
    value = _UUID_RE.sub(" token ", value)
    value = _LONG_HEX_RE.sub(" token ", value)
    value = _LONG_TOKEN_RE.sub(" token ", value)
    value = _LONG_NUMBER_RE.sub(" number ", value)
    words = _WORD_RE.findall(value)
    return " ".join(words)


def simhash64(normalized: str) -> str:
    words = normalized.split()
    if not words:
        return ""
    width = 4 if len(words) >= 4 else 1
    shingles = [" ".join(words[index:index + width]) for index in range(max(1, len(words) - width + 1))]
    counts = Counter(shingles)
    vector = [0] * 64
    for shingle, count in counts.items():
        digest = int.from_bytes(hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big")
        weight = min(int(count), 4)
        for bit in range(64):
            vector[bit] += weight if digest & (1 << bit) else -weight
    value = 0
    for bit, score in enumerate(vector):
        if score >= 0:
            value |= 1 << bit
    return f"{value:016x}"


def hamming_distance(left: str, right: str) -> int:
    if not left or not right or len(left) != 16 or len(right) != 16:
        return 64
    return (int(left, 16) ^ int(right, 16)).bit_count()


def fingerprints(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds 100 MiB index limit")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    text = visible_message_text(message)
    normalized = normalize_visible_content(text)
    return {
        "raw_sha512": hashlib.sha512(raw).hexdigest(),
        "content_sha512": hashlib.sha512(normalized.encode("utf-8")).hexdigest() if normalized else "",
        "simhash64": simhash64(normalized),
        "canonical_chars": len(normalized),
        "body": text,
        "subject": _header(message.get("Subject")) or "(ohne Betreff)",
        "sender": _header(message.get("From")),
        "recipients": " | ".join(filter(None, (_header(message.get("To")), _header(message.get("Cc"))))),
        "message_date": _header(message.get("Date")),
        "message_id": _header(message.get("Message-ID")),
    }


class MailSearchIndex:
    def __init__(self, store: MailStore):
        self.store = store
        self.path = store.control / "mail-index.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _db(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS mail_message_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_key TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    uidvalidity TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    source_kind TEXT NOT NULL DEFAULT 'imap',
                    source_peer TEXT NOT NULL DEFAULT '',
                    resource_uri TEXT NOT NULL DEFAULT '',
                    message_id TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '',
                    sender TEXT NOT NULL DEFAULT '',
                    recipients TEXT NOT NULL DEFAULT '',
                    message_date TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL DEFAULT 0,
                    raw_sha512 TEXT NOT NULL DEFAULT '',
                    content_sha512 TEXT NOT NULL DEFAULT '',
                    simhash64 TEXT NOT NULL DEFAULT '',
                    canonical_chars INTEGER NOT NULL DEFAULT 0,
                    search_text TEXT NOT NULL DEFAULT '',
                    present INTEGER NOT NULL DEFAULT 1,
                    presence_status TEXT NOT NULL DEFAULT 'present',
                    missing_reason TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    missing_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(owner_key, account_id, folder, uidvalidity, uid)
                );
                CREATE INDEX IF NOT EXISTS mail_index_account_presence
                    ON mail_message_index(owner_key, account_id, present, last_seen_at DESC);
                CREATE INDEX IF NOT EXISTS mail_index_content_sha
                    ON mail_message_index(owner_key, account_id, content_sha512);
                CREATE INDEX IF NOT EXISTS mail_index_simhash
                    ON mail_message_index(owner_key, account_id, simhash64);
                CREATE INDEX IF NOT EXISTS mail_index_message_id
                    ON mail_message_index(owner_key, account_id, message_id);
                CREATE INDEX IF NOT EXISTS mail_index_location
                    ON mail_message_index(owner_key, account_id, folder, uidvalidity, uid);
                """
            )
            try:
                db.execute(
                    """CREATE VIRTUAL TABLE IF NOT EXISTS mail_search_fts USING fts5(
                           message_row_id UNINDEXED, owner_key UNINDEXED, account_id UNINDEXED,
                           subject, sender, recipients, body,
                           tokenize='unicode61 remove_diacritics 2'
                       )"""
                )
                db.execute("CREATE TABLE IF NOT EXISTS mail_index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                db.execute("INSERT OR REPLACE INTO mail_index_meta(key, value) VALUES('fts5', '1')")
            except sqlite3.OperationalError:
                db.execute("CREATE TABLE IF NOT EXISTS mail_index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                db.execute("INSERT OR REPLACE INTO mail_index_meta(key, value) VALUES('fts5', '0')")

    def _fts_available(self, db: sqlite3.Connection) -> bool:
        row = db.execute("SELECT value FROM mail_index_meta WHERE key='fts5'").fetchone()
        return bool(row and row[0] == "1")

    def _replace_fts(self, db: sqlite3.Connection, row_id: int, owner: str, account_id: str, values: dict[str, Any]) -> None:
        if not self._fts_available(db):
            return
        db.execute("DELETE FROM mail_search_fts WHERE message_row_id = ?", (str(row_id),))
        db.execute(
            "INSERT INTO mail_search_fts(message_row_id, owner_key, account_id, subject, sender, recipients, body) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(row_id), owner, account_id, values["subject"], values["sender"], values["recipients"], values["body"][:MAX_INDEX_BODY]),
        )

    def upsert_raw(
        self,
        actor: str,
        account_id: str,
        folder: str,
        uidvalidity: str,
        uid: str,
        raw: bytes,
        *,
        source_kind: str = "imap",
        source_peer: str = "",
        resource_uri: str = "",
    ) -> dict[str, Any]:
        values = fingerprints(raw)
        owner = _owner_key(actor)
        now = utc_now()
        search_text = "\n".join((values["subject"], values["sender"], values["recipients"], values["body"][:MAX_INDEX_BODY]))
        with self._db() as db:
            db.execute(
                """INSERT INTO mail_message_index(
                       owner_key, account_id, folder, uidvalidity, uid, source_kind, source_peer,
                       resource_uri, message_id, subject, sender, recipients, message_date, size,
                       raw_sha512, content_sha512, simhash64, canonical_chars, search_text,
                       present, presence_status, missing_reason, indexed_at, last_seen_at, missing_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'present', '', ?, ?, '')
                   ON CONFLICT(owner_key, account_id, folder, uidvalidity, uid) DO UPDATE SET
                       source_kind=excluded.source_kind, source_peer=excluded.source_peer,
                       resource_uri=excluded.resource_uri, message_id=excluded.message_id,
                       subject=excluded.subject, sender=excluded.sender, recipients=excluded.recipients,
                       message_date=excluded.message_date, size=excluded.size,
                       raw_sha512=excluded.raw_sha512, content_sha512=excluded.content_sha512,
                       simhash64=excluded.simhash64, canonical_chars=excluded.canonical_chars,
                       search_text=excluded.search_text, present=1, presence_status='present',
                       missing_reason='', last_seen_at=excluded.last_seen_at, missing_at=''""",
                (
                    owner, account_id, folder, uidvalidity, uid, source_kind, source_peer,
                    resource_uri or f"imap:{account_id}:{folder}:{uidvalidity}:{uid}",
                    values["message_id"], values["subject"], values["sender"], values["recipients"],
                    values["message_date"], len(raw), values["raw_sha512"], values["content_sha512"],
                    values["simhash64"], values["canonical_chars"], search_text, now, now,
                ),
            )
            row = db.execute(
                "SELECT * FROM mail_message_index WHERE owner_key=? AND account_id=? AND folder=? AND uidvalidity=? AND uid=?",
                (owner, account_id, folder, uidvalidity, uid),
            ).fetchone()
            assert row is not None
            self._replace_fts(db, int(row["id"]), owner, account_id, values)
            return dict(row)

    def existing_uids(self, actor: str, account_id: str, folder: str, uidvalidity: str) -> set[str]:
        owner = _owner_key(actor)
        with self._db() as db:
            return {
                str(row[0]) for row in db.execute(
                    "SELECT uid FROM mail_message_index WHERE owner_key=? AND account_id=? AND folder=? AND uidvalidity=?",
                    (owner, account_id, folder, uidvalidity),
                )
            }

    def reconcile_folder(self, actor: str, account_id: str, folder: str, uidvalidity: str, current_uids: Iterable[str]) -> dict[str, int]:
        owner = _owner_key(actor)
        current = {str(uid) for uid in current_uids}
        now = utc_now()
        present = missing = 0
        with self._db() as db:
            rows = db.execute(
                "SELECT id, uid, uidvalidity, present FROM mail_message_index WHERE owner_key=? AND account_id=? AND folder=?",
                (owner, account_id, folder),
            ).fetchall()
            for row in rows:
                exists = str(row["uidvalidity"]) == uidvalidity and str(row["uid"]) in current
                if exists:
                    db.execute(
                        "UPDATE mail_message_index SET present=1, presence_status='present', missing_reason='', missing_at='', last_seen_at=? WHERE id=?",
                        (now, row["id"]),
                    )
                    present += 1
                else:
                    reason = "Ziel nicht gefunden"
                    if str(row["uidvalidity"]) != uidvalidity:
                        reason += " (UIDVALIDITY geändert)"
                    db.execute(
                        "UPDATE mail_message_index SET present=0, presence_status='target_not_found', missing_reason=?, missing_at=CASE WHEN missing_at='' THEN ? ELSE missing_at END WHERE id=?",
                        (reason, now, row["id"]),
                    )
                    missing += 1
        return {"present": present, "missing": missing}

    def mark_missing_ids(self, actor: str, account_id: str, row_ids: Iterable[int], reason: str = "Ziel nicht gefunden") -> int:
        owner = _owner_key(actor)
        ids = sorted({int(value) for value in row_ids if int(value) > 0})[:2000]
        if not ids:
            return 0
        now = utc_now()
        placeholders = ",".join("?" for _ in ids)
        with self._db() as db:
            cursor = db.execute(
                f"UPDATE mail_message_index SET present=0, presence_status='target_not_found', missing_reason=?, missing_at=CASE WHEN missing_at='' THEN ? ELSE missing_at END WHERE owner_key=? AND account_id=? AND id IN ({placeholders})",
                (reason[:500], now, owner, account_id, *ids),
            )
            return int(cursor.rowcount)

    def rows_by_ids(self, actor: str, account_id: str, row_ids: Iterable[int], *, present_only: bool = False) -> list[dict[str, Any]]:
        owner = _owner_key(actor)
        ids = sorted({int(value) for value in row_ids if int(value) > 0})[:2000]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        condition = " AND present=1" if present_only else ""
        with self._db() as db:
            rows = db.execute(
                f"SELECT * FROM mail_message_index WHERE owner_key=? AND account_id=? AND id IN ({placeholders}){condition}",
                (owner, account_id, *ids),
            ).fetchall()
            return [dict(row) for row in rows]

    def search(self, actor: str, account_id: str, query: str, *, include_missing: bool = True, limit: int = 250) -> list[dict[str, Any]]:
        owner = _owner_key(actor)
        limit = max(1, min(int(limit), 1000))
        query = query.strip()
        with self._db() as db:
            where_missing = "" if include_missing else " AND m.present=1"
            terms = [term for term in _WORD_RE.findall(unicodedata.normalize("NFKC", query).casefold()) if len(term) >= 2][:12]
            if terms and self._fts_available(db):
                expression = " AND ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
                try:
                    rows = db.execute(
                        f"""SELECT m.* FROM mail_search_fts f
                            JOIN mail_message_index m ON m.id=CAST(f.message_row_id AS INTEGER)
                            WHERE mail_search_fts MATCH ? AND m.owner_key=? AND m.account_id=?{where_missing}
                            ORDER BY m.present DESC, m.last_seen_at DESC LIMIT ?""",
                        (expression, owner, account_id, limit),
                    ).fetchall()
                    return [dict(row) for row in rows]
                except sqlite3.OperationalError:
                    pass
            if query:
                needle = f"%{query.casefold()}%"
                rows = db.execute(
                    f"""SELECT m.* FROM mail_message_index m
                        WHERE m.owner_key=? AND m.account_id=?{where_missing}
                          AND lower(m.search_text) LIKE ?
                        ORDER BY m.present DESC, m.last_seen_at DESC LIMIT ?""",
                    (owner, account_id, needle, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    f"SELECT m.* FROM mail_message_index m WHERE m.owner_key=? AND m.account_id=?{where_missing} ORDER BY m.present DESC, m.last_seen_at DESC LIMIT ?",
                    (owner, account_id, limit),
                ).fetchall()
            return [dict(row) for row in rows]

    def stats(self, actor: str, account_id: str) -> dict[str, Any]:
        owner = _owner_key(actor)
        with self._db() as db:
            row = db.execute(
                """SELECT COUNT(*) total, SUM(CASE WHEN present=1 THEN 1 ELSE 0 END) present,
                          SUM(CASE WHEN present=0 THEN 1 ELSE 0 END) missing,
                          MAX(last_seen_at) last_seen
                   FROM mail_message_index WHERE owner_key=? AND account_id=?""",
                (owner, account_id),
            ).fetchone()
            return {
                "total": int(row["total"] or 0),
                "present": int(row["present"] or 0),
                "missing": int(row["missing"] or 0),
                "last_seen": str(row["last_seen"] or ""),
                "fts5": self._fts_available(db),
            }

    def duplicate_groups(self, actor: str, account_id: str, *, limit: int = MAX_INDEX_ROWS) -> list[dict[str, Any]]:
        owner = _owner_key(actor)
        with self._db() as db:
            rows = [dict(row) for row in db.execute(
                """SELECT * FROM mail_message_index
                   WHERE owner_key=? AND account_id=? AND content_sha512<>''
                   ORDER BY present DESC, last_seen_at DESC LIMIT ?""",
                (owner, account_id, max(2, min(int(limit), MAX_INDEX_ROWS))),
            )]
        if len(rows) < 2:
            return []

        parent = list(range(len(rows)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        exact_rep: dict[str, int] = {}
        representative_indexes: list[int] = []
        for index, row in enumerate(rows):
            digest = str(row["content_sha512"])
            previous = exact_rep.get(digest)
            if previous is None:
                exact_rep[digest] = index
                representative_indexes.append(index)
            else:
                union(previous, index)

        buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        for index in representative_indexes:
            row = rows[index]
            simhash = str(row.get("simhash64") or "")
            chars = int(row.get("canonical_chars") or 0)
            if len(simhash) != 16 or chars < MIN_FUZZY_CHARS:
                continue
            value = int(simhash, 16)
            candidates: set[int] = set()
            for bucket in range(8):
                byte = (value >> (bucket * 8)) & 0xFF
                candidates.update(buckets[(bucket, byte)][-64:])
            for candidate in candidates:
                other = rows[candidate]
                other_chars = int(other.get("canonical_chars") or 0)
                if min(chars, other_chars) / max(chars, other_chars, 1) < 0.82:
                    continue
                if hamming_distance(simhash, str(other.get("simhash64") or "")) <= FUZZY_MAX_HAMMING:
                    union(index, candidate)
            for bucket in range(8):
                byte = (value >> (bucket * 8)) & 0xFF
                buckets[(bucket, byte)].append(index)

        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for index, row in enumerate(rows):
            grouped[find(index)].append(row)

        result: list[dict[str, Any]] = []
        for group_rows in grouped.values():
            if len(group_rows) < 2:
                continue
            digests = {str(row["content_sha512"]) for row in group_rows}
            hashes = [str(row.get("simhash64") or "") for row in group_rows if row.get("simhash64")]
            max_distance = 0
            if hashes:
                reference = hashes[0]
                max_distance = max((hamming_distance(reference, value) for value in hashes[1:]), default=0)
            confidence = 100 if len(digests) == 1 else max(85, round((64 - min(max_distance, 10)) / 64 * 100))
            present_rows = [row for row in group_rows if int(row.get("present") or 0) == 1]
            missing_rows = [row for row in group_rows if int(row.get("present") or 0) == 0]
            group_id = hashlib.sha256("\0".join(sorted(str(row["id"]) for row in group_rows)).encode("ascii")).hexdigest()[:20]
            result.append({
                "group_id": group_id,
                "rows": group_rows,
                "row_ids": [int(row["id"]) for row in present_rows],
                "count": len(group_rows),
                "present_count": len(present_rows),
                "missing_count": len(missing_rows),
                "confidence": confidence,
                "exact": len(digests) == 1,
                "subject": next((str(row.get("subject") or "") for row in group_rows if row.get("subject")), "(ohne Betreff)"),
                "sample_sender": next((str(row.get("sender") or "") for row in group_rows if row.get("sender")), ""),
            })
        result.sort(key=lambda group: (group["present_count"], group["count"], group["confidence"]), reverse=True)
        return result

    def cleanup_missing(self, actor: str, account_id: str) -> int:
        """Permanently remove tombstones.  Callers must enforce administrator access."""
        owner = _owner_key(actor)
        with self._db() as db:
            ids = [int(row[0]) for row in db.execute(
                "SELECT id FROM mail_message_index WHERE owner_key=? AND account_id=? AND present=0",
                (owner, account_id),
            )]
            if ids and self._fts_available(db):
                for row_id in ids:
                    db.execute("DELETE FROM mail_search_fts WHERE message_row_id=?", (str(row_id),))
            cursor = db.execute(
                "DELETE FROM mail_message_index WHERE owner_key=? AND account_id=? AND present=0",
                (owner, account_id),
            )
            return int(cursor.rowcount)


class MailIndexer:
    """Incrementally index IMAP bodies without changing flags."""

    def __init__(self, store: MailStore):
        self.store = store
        self.index = MailSearchIndex(store)
        self.imap = ImapArchive(store)

    @staticmethod
    def _uidvalidity(connection: Any) -> str:
        raw = connection.untagged_responses.get("UIDVALIDITY", [b""])[0]
        return raw.decode("ascii", "replace") if isinstance(raw, bytes) else str(raw or "")

    def refresh_folder(self, actor: str, account: dict[str, Any], folder: str, *, max_new: int = 50) -> dict[str, Any]:
        max_new = max(1, min(int(max_new), MAX_REFRESH_PER_FOLDER))
        connection = self.imap._connect(account)
        result = {"folder": folder, "indexed": 0, "known": 0, "missing": 0, "errors": 0}
        try:
            status, _ = connection.select(_encode_modified_utf7(folder), readonly=True)
            if status != "OK":
                raise RuntimeError("IMAP EXAMINE failed")
            uidvalidity = self._uidvalidity(connection)
            status, data = connection.uid("search", None, "ALL")
            if status != "OK":
                raise RuntimeError("IMAP UID SEARCH failed")
            current = [token.decode("ascii", "strict") for token in ((data or [b""])[0] or b"").split()]
            reconciled = self.index.reconcile_folder(actor, account["id"], folder, uidvalidity, current)
            result["missing"] = reconciled["missing"]
            known = self.index.existing_uids(actor, account["id"], folder, uidvalidity)
            result["known"] = len(known)
            pending = [uid for uid in reversed(current) if uid not in known][:max_new]
            for uid in pending:
                try:
                    status, fetched = connection.uid("fetch", uid, "(UID RFC822.SIZE BODY.PEEK[])")
                    if status != "OK":
                        result["errors"] += 1
                        continue
                    raw = self.imap._literal(fetched)
                    if not raw or len(raw) > MAX_MESSAGE_BYTES:
                        result["errors"] += 1
                        continue
                    self.index.upsert_raw(actor, account["id"], folder, uidvalidity, uid, raw)
                    result["indexed"] += 1
                except (OSError, RuntimeError, ValueError):
                    result["errors"] += 1
            return result
        finally:
            try:
                connection.logout()
            except Exception:
                pass

    def refresh_account(self, actor: str, account: dict[str, Any], *, per_folder: int = 100) -> dict[str, Any]:
        folders = [row for row in ImapWebClient(self.store).folders(account) if row.get("selectable")]
        results: list[dict[str, Any]] = []
        for row in folders[:200]:
            try:
                results.append(self.refresh_folder(actor, account, str(row["name"]), max_new=per_folder))
            except (OSError, RuntimeError, ValueError):
                results.append({"folder": str(row["name"]), "indexed": 0, "known": 0, "missing": 0, "errors": 1})
        return {
            "folders": len(results),
            "indexed": sum(int(row["indexed"]) for row in results),
            "missing": sum(int(row["missing"]) for row in results),
            "errors": sum(int(row["errors"]) for row in results),
            "results": results,
        }


class MailGroupMutator:
    """Perform target-scoped bulk IMAP mutations for indexed locations."""

    def __init__(self, store: MailStore):
        self.store = store
        self.index = MailSearchIndex(store)
        self.policy = MailAccountPolicy(store)
        self.imap = ImapArchive(store)

    @staticmethod
    def _capabilities(connection: Any) -> set[str]:
        return {
            item.decode("ascii", "replace").upper() if isinstance(item, bytes) else str(item).upper()
            for item in getattr(connection, "capabilities", ())
        }

    @staticmethod
    def _uidvalidity(connection: Any) -> str:
        raw = connection.untagged_responses.get("UIDVALIDITY", [b""])[0]
        return raw.decode("ascii", "replace") if isinstance(raw, bytes) else str(raw or "")

    @staticmethod
    def _chunks(values: list[str], size: int = 100) -> Iterable[list[str]]:
        for index in range(0, len(values), size):
            yield values[index:index + size]

    def _group_rows(self, actor: str, account_id: str, row_ids: Iterable[int]) -> dict[tuple[str, str], list[dict[str, Any]]]:
        rows = self.index.rows_by_ids(actor, account_id, row_ids, present_only=True)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(str(row["folder"]), str(row["uidvalidity"]))].append(row)
        return grouped

    def move(self, actor: str, account: dict[str, Any], row_ids: Iterable[int], target: str) -> dict[str, Any]:
        self.policy.require_writable(actor, account["id"])
        target = target.strip()
        if not target:
            raise ValueError("target folder is required")
        grouped = self._group_rows(actor, account["id"], row_ids)
        moved_ids: list[int] = []
        stale_ids: list[int] = []
        errors: list[str] = []
        for (folder, expected_validity), rows in grouped.items():
            if folder == target:
                continue
            connection = self.imap._connect(account)
            try:
                status, _ = connection.select(_encode_modified_utf7(folder), readonly=False)
                if status != "OK":
                    raise RuntimeError("IMAP folder could not be selected")
                actual_validity = self._uidvalidity(connection)
                if expected_validity and actual_validity and expected_validity != actual_validity:
                    stale_ids.extend(int(row["id"]) for row in rows)
                    continue
                capabilities = self._capabilities(connection)
                for chunk in self._chunks([str(row["uid"]) for row in rows]):
                    uid_set = ",".join(chunk)
                    if "MOVE" in capabilities:
                        status, _ = connection.uid("MOVE", uid_set, _encode_modified_utf7(target))
                    elif "UIDPLUS" in capabilities:
                        status, _ = connection.uid("COPY", uid_set, _encode_modified_utf7(target))
                        if status == "OK":
                            status, _ = connection.uid("STORE", uid_set, "+FLAGS.SILENT", "(\\Deleted)")
                        if status == "OK":
                            status, _ = connection.uid("EXPUNGE", uid_set)
                    else:
                        raise RuntimeError("Server unterstützt weder MOVE noch UIDPLUS; sichere Gruppenverschiebung nicht möglich")
                    if status != "OK":
                        raise RuntimeError("IMAP group move failed")
                moved_ids.extend(int(row["id"]) for row in rows)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{folder}: {type(exc).__name__}")
            finally:
                try:
                    connection.logout()
                except Exception:
                    pass
        if stale_ids:
            self.index.mark_missing_ids(actor, account["id"], stale_ids, "Ziel nicht gefunden (UIDVALIDITY geändert)")
        if moved_ids:
            self.index.mark_missing_ids(actor, account["id"], moved_ids, f"Ziel nicht gefunden (verschoben nach {target[:200]})")
        self.store.history.record(
            "imap_duplicate_group_moved", actor, "mail-accounts", account["id"],
            {"target": target, "moved": len(moved_ids), "stale": len(stale_ids), "errors": errors, "at": utc_now()},
        )
        return {"moved": len(moved_ids), "stale": len(stale_ids), "errors": errors}

    def delete(self, actor: str, account: dict[str, Any], row_ids: Iterable[int]) -> dict[str, Any]:
        self.policy.require_writable(actor, account["id"])
        grouped = self._group_rows(actor, account["id"], row_ids)
        deleted_ids: list[int] = []
        stale_ids: list[int] = []
        errors: list[str] = []
        for (folder, expected_validity), rows in grouped.items():
            connection = self.imap._connect(account)
            try:
                status, _ = connection.select(_encode_modified_utf7(folder), readonly=False)
                if status != "OK":
                    raise RuntimeError("IMAP folder could not be selected")
                actual_validity = self._uidvalidity(connection)
                if expected_validity and actual_validity and expected_validity != actual_validity:
                    stale_ids.extend(int(row["id"]) for row in rows)
                    continue
                if "UIDPLUS" not in self._capabilities(connection):
                    raise RuntimeError("Server unterstützt UIDPLUS nicht; gezieltes Löschen ohne globales EXPUNGE wird verweigert")
                for chunk in self._chunks([str(row["uid"]) for row in rows]):
                    uid_set = ",".join(chunk)
                    status, _ = connection.uid("STORE", uid_set, "+FLAGS.SILENT", "(\\Deleted)")
                    if status == "OK":
                        status, _ = connection.uid("EXPUNGE", uid_set)
                    if status != "OK":
                        raise RuntimeError("IMAP group delete failed")
                deleted_ids.extend(int(row["id"]) for row in rows)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{folder}: {type(exc).__name__}")
            finally:
                try:
                    connection.logout()
                except Exception:
                    pass
        if stale_ids:
            self.index.mark_missing_ids(actor, account["id"], stale_ids, "Ziel nicht gefunden (UIDVALIDITY geändert)")
        if deleted_ids:
            self.index.mark_missing_ids(actor, account["id"], deleted_ids, "Ziel nicht gefunden (auf Mailserver gelöscht)")
        self.store.history.record(
            "imap_duplicate_group_deleted", actor, "mail-accounts", account["id"],
            {"deleted": len(deleted_ids), "stale": len(stale_ids), "errors": errors, "at": utc_now()},
        )
        return {"deleted": len(deleted_ids), "stale": len(stale_ids), "errors": errors}
