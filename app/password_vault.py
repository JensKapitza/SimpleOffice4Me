"""Encrypted password vault with master-password key wrapping and portable backups.

The server never stores the master password or a reusable password verifier.
A random vault key encrypts every item with AES-GCM. The master password only
wraps that vault key through scrypt, so changing the master password does not
require re-encrypting every secret.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .document_store import CONTROL_DIR

SCHEMA_VERSION = 1
VAULT_FORMAT = "simpleoffice-password-vault"
KDF_N = 2**15
KDF_R = 8
KDF_P = 1
MAX_ITEM_BYTES = 512 * 1024
MAX_BACKUP_BYTES = 64 * 1024 * 1024
ENTRY_TYPES = {"login", "secure_note", "identity", "card", "ssh_key", "api_key"}


def _now() -> int:
    return int(time.time())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _kdf(master_password: str, salt: bytes, *, n: int = KDF_N, r: int = KDF_R, p: int = KDF_P) -> bytes:
    if not isinstance(master_password, str) or len(master_password) < 10 or len(master_password) > 4096:
        raise ValueError("Master-Passwort muss zwischen 10 und 4096 Zeichen lang sein")
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(master_password.encode("utf-8"))


def _profile_aad(user_id: str) -> bytes:
    return f"simpleoffice-password-vault:keywrap:v1:{user_id}".encode("utf-8")


def _entry_aad(user_id: str, entry_id: str, revision: int) -> bytes:
    return f"simpleoffice-password-vault:item:v1:{user_id}:{entry_id}:{revision}".encode("utf-8")


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Vault-Eintrag muss ein Objekt sein")
    kind = str(payload.get("type") or "login").strip()
    if kind not in ENTRY_TYPES:
        raise ValueError("Unbekannter Vault-Eintragstyp")
    normalized = dict(payload)
    normalized["type"] = kind
    for key in ("name", "username", "password", "url", "notes", "totp", "folder", "favorite"):
        if key not in normalized:
            continue
        value = normalized[key]
        if isinstance(value, str) and len(value) > 200_000:
            raise ValueError(f"Vault-Feld {key} ist zu groß")
    raw = _canonical(normalized)
    if len(raw) > MAX_ITEM_BYTES:
        raise ValueError("Vault-Eintrag ist zu groß")
    return normalized


class PasswordVault:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "password-vault.sqlite3"
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS vault_profile(
                    user_id TEXT PRIMARY KEY,
                    salt BLOB NOT NULL,
                    wrap_nonce BLOB NOT NULL,
                    wrapped_key BLOB NOT NULL,
                    kdf_n INTEGER NOT NULL,
                    kdf_r INTEGER NOT NULL,
                    kdf_p INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vault_entry(
                    user_id TEXT NOT NULL,
                    entry_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    deleted_at INTEGER,
                    PRIMARY KEY(user_id, entry_id),
                    FOREIGN KEY(user_id) REFERENCES vault_profile(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS vault_entry_updated_idx
                    ON vault_entry(user_id, updated_at DESC);
                """
            )
            db.execute("PRAGMA user_version=1")
        if os.name == "posix" and self.path.exists():
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def configured(self, user_id: str) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM vault_profile WHERE user_id=?", (str(user_id),)).fetchone() is not None

    def create(self, user_id: str, master_password: str) -> bytes:
        user_id = str(user_id).strip()
        if not user_id or len(user_id) > 160:
            raise ValueError("Ungültige Benutzer-ID")
        if self.configured(user_id):
            raise ValueError("Passwort-Vault existiert bereits")
        salt = secrets.token_bytes(16)
        wrapping_key = _kdf(master_password, salt)
        vault_key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        wrapped = AESGCM(wrapping_key).encrypt(nonce, vault_key, _profile_aad(user_id))
        timestamp = _now()
        with self._db() as db:
            db.execute(
                "INSERT INTO vault_profile(user_id,salt,wrap_nonce,wrapped_key,kdf_n,kdf_r,kdf_p,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (user_id, salt, nonce, wrapped, KDF_N, KDF_R, KDF_P, timestamp, timestamp),
            )
        return vault_key

    def unlock(self, user_id: str, master_password: str) -> bytes:
        user_id = str(user_id).strip()
        with self._db() as db:
            row = db.execute("SELECT * FROM vault_profile WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("Passwort-Vault ist nicht eingerichtet")
        try:
            wrapping_key = _kdf(
                master_password, bytes(row["salt"]),
                n=int(row["kdf_n"]), r=int(row["kdf_r"]), p=int(row["kdf_p"]),
            )
            return AESGCM(wrapping_key).decrypt(bytes(row["wrap_nonce"]), bytes(row["wrapped_key"]), _profile_aad(user_id))
        except (InvalidTag, ValueError) as exc:
            raise ValueError("Master-Passwort ist falsch oder der Vault wurde verändert") from exc

    def change_master_password(self, user_id: str, old_password: str, new_password: str) -> None:
        vault_key = self.unlock(user_id, old_password)
        user_id = str(user_id).strip()
        salt = secrets.token_bytes(16)
        wrapping_key = _kdf(new_password, salt)
        nonce = secrets.token_bytes(12)
        wrapped = AESGCM(wrapping_key).encrypt(nonce, vault_key, _profile_aad(user_id))
        with self._db() as db:
            db.execute(
                "UPDATE vault_profile SET salt=?,wrap_nonce=?,wrapped_key=?,kdf_n=?,kdf_r=?,kdf_p=?,updated_at=? WHERE user_id=?",
                (salt, nonce, wrapped, KDF_N, KDF_R, KDF_P, _now(), user_id),
            )

    def put(self, user_id: str, vault_key: bytes, payload: dict[str, Any], *, entry_id: str | None = None) -> dict[str, Any]:
        user_id = str(user_id).strip()
        if len(vault_key) != 32 or not self.configured(user_id):
            raise ValueError("Vault ist nicht entsperrt")
        entry_id = str(entry_id or uuid.uuid4())
        try:
            uuid.UUID(entry_id)
        except ValueError as exc:
            raise ValueError("Ungültige Vault-Eintrags-ID") from exc
        normalized = _normalize_payload(payload)
        with self._db() as db:
            current = db.execute("SELECT revision,created_at FROM vault_entry WHERE user_id=? AND entry_id=?", (user_id, entry_id)).fetchone()
            revision = int(current["revision"]) + 1 if current else 1
            created_at = int(current["created_at"]) if current else _now()
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(vault_key).encrypt(nonce, _canonical(normalized), _entry_aad(user_id, entry_id, revision))
            timestamp = _now()
            db.execute(
                """INSERT INTO vault_entry(user_id,entry_id,revision,nonce,ciphertext,created_at,updated_at,deleted_at)
                   VALUES(?,?,?,?,?,?,?,NULL)
                   ON CONFLICT(user_id,entry_id) DO UPDATE SET revision=excluded.revision,nonce=excluded.nonce,
                     ciphertext=excluded.ciphertext,updated_at=excluded.updated_at,deleted_at=NULL""",
                (user_id, entry_id, revision, nonce, ciphertext, created_at, timestamp),
            )
        return {"entry_id": entry_id, "revision": revision, "updated_at": timestamp}

    def delete(self, user_id: str, entry_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE vault_entry SET deleted_at=?,updated_at=? WHERE user_id=? AND entry_id=?", (_now(), _now(), str(user_id), str(entry_id)))

    def entries(self, user_id: str, vault_key: bytes, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        if len(vault_key) != 32:
            raise ValueError("Vault ist nicht entsperrt")
        with self._db() as db:
            if include_deleted:
                rows = db.execute(
                    "SELECT * FROM vault_entry WHERE user_id=? ORDER BY updated_at DESC",
                    (str(user_id),),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM vault_entry WHERE user_id=? AND deleted_at IS NULL ORDER BY updated_at DESC",
                    (str(user_id),),
                ).fetchall()
        result = []
        for row in rows:
            try:
                raw = AESGCM(vault_key).decrypt(
                    bytes(row["nonce"]), bytes(row["ciphertext"]),
                    _entry_aad(str(user_id), str(row["entry_id"]), int(row["revision"])),
                )
                payload = json.loads(raw)
            except (InvalidTag, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeError(f"Vault-Eintrag {row['entry_id']} ist beschädigt") from exc
            result.append({
                "entry_id": row["entry_id"], "revision": row["revision"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "deleted_at": row["deleted_at"], "data": payload,
            })
        return result

    def export_backup(self, user_id: str) -> bytes:
        """Export only already-encrypted material; no master password is required."""
        with self._db() as db:
            profile = db.execute("SELECT * FROM vault_profile WHERE user_id=?", (str(user_id),)).fetchone()
            rows = db.execute("SELECT * FROM vault_entry WHERE user_id=? ORDER BY entry_id", (str(user_id),)).fetchall()
        if profile is None:
            raise ValueError("Passwort-Vault ist nicht eingerichtet")
        payload = {
            "format": VAULT_FORMAT,
            "version": SCHEMA_VERSION,
            "exported_at": _now(),
            "user_id": str(user_id),
            "profile": {
                "salt": _b64(bytes(profile["salt"])), "wrap_nonce": _b64(bytes(profile["wrap_nonce"])),
                "wrapped_key": _b64(bytes(profile["wrapped_key"])), "kdf_n": profile["kdf_n"],
                "kdf_r": profile["kdf_r"], "kdf_p": profile["kdf_p"],
                "created_at": profile["created_at"], "updated_at": profile["updated_at"],
            },
            "entries": [
                {
                    "entry_id": row["entry_id"], "revision": row["revision"],
                    "nonce": _b64(bytes(row["nonce"])), "ciphertext": _b64(bytes(row["ciphertext"])),
                    "created_at": row["created_at"], "updated_at": row["updated_at"], "deleted_at": row["deleted_at"],
                }
                for row in rows
            ],
        }
        body = _canonical(payload)
        if len(body) > MAX_BACKUP_BYTES:
            raise ValueError("Vault-Backup ist zu groß")
        envelope = {"payload": payload, "sha256": hashlib.sha256(body).hexdigest()}
        return json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")

    def import_backup(self, data: bytes, *, target_user_id: str | None = None, replace: bool = False) -> dict[str, int]:
        if not data or len(data) > MAX_BACKUP_BYTES:
            raise ValueError("Ungültige Vault-Backup-Größe")
        try:
            envelope = json.loads(data)
            payload = envelope["payload"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("Ungültiges Vault-Backup") from exc
        if payload.get("format") != VAULT_FORMAT or payload.get("version") != SCHEMA_VERSION:
            raise ValueError("Nicht unterstütztes Vault-Backup")
        if hashlib.sha256(_canonical(payload)).hexdigest() != str(envelope.get("sha256", "")):
            raise ValueError("Vault-Backup-Prüfsumme stimmt nicht")
        source_user = str(payload.get("user_id") or "")
        user_id = str(target_user_id or source_user).strip()
        if not user_id or (target_user_id is not None and user_id != source_user):
            raise ValueError("Vault-Backups können nicht ohne Schlüsselrotation einem anderen Benutzer zugeordnet werden")
        profile = payload.get("profile")
        entries = payload.get("entries")
        if not isinstance(profile, dict) or not isinstance(entries, list):
            raise ValueError("Vault-Backup ist unvollständig")
        if self.configured(user_id) and not replace:
            raise ValueError("Ziel-Vault existiert bereits")
        decoded_profile = (
            _unb64(str(profile["salt"])), _unb64(str(profile["wrap_nonce"])), _unb64(str(profile["wrapped_key"])),
            int(profile["kdf_n"]), int(profile["kdf_r"]), int(profile["kdf_p"]),
            int(profile["created_at"]), int(profile["updated_at"]),
        )
        if len(decoded_profile[0]) != 16 or len(decoded_profile[1]) != 12:
            raise ValueError("Vault-Profil ist beschädigt")
        staged = []
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError("Ungültiger Vault-Eintrag")
            entry_id = str(item.get("entry_id") or "")
            try:
                uuid.UUID(entry_id)
            except ValueError as exc:
                raise ValueError("Ungültige Vault-Eintrags-ID") from exc
            nonce = _unb64(str(item.get("nonce") or ""))
            ciphertext = _unb64(str(item.get("ciphertext") or ""))
            if len(nonce) != 12 or len(ciphertext) > MAX_ITEM_BYTES + 64:
                raise ValueError("Ungültige Vault-Ciphertext-Größe")
            staged.append((
                user_id, entry_id, int(item["revision"]), nonce, ciphertext,
                int(item["created_at"]), int(item["updated_at"]), item.get("deleted_at"),
            ))
        with self._db() as db:
            if replace:
                db.execute("DELETE FROM vault_profile WHERE user_id=?", (user_id,))
            db.execute(
                "INSERT INTO vault_profile(user_id,salt,wrap_nonce,wrapped_key,kdf_n,kdf_r,kdf_p,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (user_id, *decoded_profile),
            )
            db.executemany(
                "INSERT INTO vault_entry(user_id,entry_id,revision,nonce,ciphertext,created_at,updated_at,deleted_at) VALUES(?,?,?,?,?,?,?,?)",
                staged,
            )
        return {"profiles": 1, "entries": len(staged)}


def generate_password(length: int = 24) -> str:
    length = max(12, min(int(length), 256))
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!#$%&()*+,-./:;<=>?@[]^_{|}~"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value) and any(not c.isalnum() for c in value):
            return value
