"""Persistent SIP extension profiles for the Mini Services telephony assistant."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .telephony_numbering import clean_number

ALLOWED_TRANSPORTS = {"udp", "tcp", "tls"}
_HOST_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_DIGEST_RE = re.compile(r"^[0-9a-f]{32}$")


def _now() -> int:
    return int(time.time())


def _clean_host(value: str, *, allow_empty: bool = False) -> str:
    host = str(value or "").strip()
    if not host:
        if allow_empty:
            return ""
        raise ValueError("SIP-Server ist ungueltig")
    if len(host) > 253 or any(ch.isspace() for ch in host):
        raise ValueError("SIP-Server ist ungueltig")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.rstrip(".").split(".")
    if not labels or any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError("SIP-Server ist ungueltig")
    return host.rstrip(".")


def _clean_transport(value: str) -> str:
    transport = str(value or "udp").strip().lower()
    if transport not in ALLOWED_TRANSPORTS:
        raise ValueError("SIP-Transport muss udp, tcp oder tls sein")
    return transport


def sip_digest_ha1(username: str, realm: str, password: str) -> str:
    """Return RFC-compatible MD5 HA1 without persisting the clear-text password."""
    user = clean_number(username, "Nebenstelle")
    clean_realm = _clean_host(realm)
    secret = str(password or "")
    if not secret:
        raise ValueError("SIP-Passwort fehlt")
    material = f"{user}:{clean_realm}:{secret}".encode("utf-8")
    try:
        return hashlib.md5(material, usedforsecurity=False).hexdigest()
    except TypeError:  # pragma: no cover - older compatible Python builds
        return hashlib.md5(material).hexdigest()  # nosec B324 - SIP Digest compatibility


class TelephonyProfileStore:
    """Store device profiles while keeping SIP secrets encrypted at rest."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "telephony-profiles.sqlite3"
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS telephony_setting(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telephony_profile(
                    extension TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    auth_user TEXT NOT NULL,
                    secret_enc TEXT NOT NULL,
                    digest_ha1 TEXT NOT NULL DEFAULT '',
                    device_kind TEXT NOT NULL DEFAULT 'softphone',
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(telephony_profile)").fetchall()}
            if "digest_ha1" not in columns:
                db.execute("ALTER TABLE telephony_profile ADD COLUMN digest_ha1 TEXT NOT NULL DEFAULT ''")

    def settings(self) -> dict:
        defaults = {
            "registrar_host": "",
            "registrar_port": "5060",
            "transport": "udp",
            "realm": "simpleoffice.local",
            "stun_server": "",
        }
        with self._db() as db:
            rows = db.execute("SELECT key,value FROM telephony_setting").fetchall()
        defaults.update({row["key"]: row["value"] for row in rows})
        return defaults

    def save_settings(self, *, registrar_host: str, registrar_port: int, transport: str, realm: str, stun_server: str = "") -> dict:
        host = _clean_host(registrar_host, allow_empty=True)
        port = int(registrar_port)
        if not 1 <= port <= 65535:
            raise ValueError("SIP-Port muss zwischen 1 und 65535 liegen")
        clean_transport = _clean_transport(transport)
        clean_realm = _clean_host(realm)
        clean_stun = _clean_host(stun_server, allow_empty=True)
        previous = self.settings()
        if clean_realm != previous["realm"] and self.profiles():
            raise ValueError("SIP-Realm kann mit vorhandenen Nebenstellen nicht geändert werden; zuerst Nebenstellen-Passwörter erneuern")
        values = {
            "registrar_host": host,
            "registrar_port": str(port),
            "transport": clean_transport,
            "realm": clean_realm,
            "stun_server": clean_stun,
        }
        with self._db() as db:
            db.executemany(
                """INSERT INTO telephony_setting(key,value) VALUES(?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                values.items(),
            )
        return self.settings()

    def create_profile(self, extension: str, display_name: str, secret_enc: str, *, device_kind: str = "softphone", digest_ha1: str = "") -> dict:
        number = clean_number(extension, "Nebenstelle")
        label = " ".join(str(display_name or number).split())[:200]
        kind = str(device_kind or "softphone").strip().lower()
        if kind not in {"softphone", "deskphone", "doorphone", "other"}:
            raise ValueError("Unbekannter Geraetetyp")
        if not str(secret_enc).startswith("enc:v1:"):
            raise ValueError("SIP-Zugang muss verschluesselt gespeichert werden")
        digest = str(digest_ha1 or "").strip().casefold()
        if digest and not _DIGEST_RE.fullmatch(digest):
            raise ValueError("SIP-Digest-Verifier ist ungueltig")
        timestamp = _now()
        with self._db() as db:
            db.execute(
                """INSERT INTO telephony_profile(
                       extension,display_name,auth_user,secret_enc,digest_ha1,device_kind,enabled,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,1,?,?)""",
                (number, label, number, secret_enc, digest, kind, timestamp, timestamp),
            )
        return self.profile(number)

    def profile(self, extension: str) -> dict:
        number = clean_number(extension, "Nebenstelle")
        with self._db() as db:
            row = db.execute(
                "SELECT extension,display_name,auth_user,device_kind,enabled,created_at,updated_at,CASE WHEN digest_ha1<>'' THEN 1 ELSE 0 END AS registrar_ready FROM telephony_profile WHERE extension=?",
                (number,),
            ).fetchone()
        if row is None:
            raise KeyError("Nebenstelle nicht gefunden")
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        result["registrar_ready"] = bool(result["registrar_ready"])
        return result

    def profiles(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT extension,display_name,auth_user,device_kind,enabled,created_at,updated_at,CASE WHEN digest_ha1<>'' THEN 1 ELSE 0 END AS registrar_ready FROM telephony_profile ORDER BY length(extension),extension"
            ).fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"]), "registrar_ready": bool(row["registrar_ready"])} for row in rows]

    def encrypted_secret(self, extension: str) -> str:
        number = clean_number(extension, "Nebenstelle")
        with self._db() as db:
            row = db.execute("SELECT secret_enc FROM telephony_profile WHERE extension=?", (number,)).fetchone()
        if row is None:
            raise KeyError("Nebenstelle nicht gefunden")
        return str(row["secret_enc"])

    def rotate_secret(self, extension: str, secret_enc: str, *, digest_ha1: str = "") -> dict:
        number = clean_number(extension, "Nebenstelle")
        if not str(secret_enc).startswith("enc:v1:"):
            raise ValueError("SIP-Zugang muss verschluesselt gespeichert werden")
        digest = str(digest_ha1 or "").strip().casefold()
        if digest and not _DIGEST_RE.fullmatch(digest):
            raise ValueError("SIP-Digest-Verifier ist ungueltig")
        with self._db() as db:
            cursor = db.execute(
                "UPDATE telephony_profile SET secret_enc=?,digest_ha1=?,updated_at=? WHERE extension=?",
                (secret_enc, digest, _now(), number),
            )
            if cursor.rowcount != 1:
                raise KeyError("Nebenstelle nicht gefunden")
        return self.profile(number)

    def registrar_profile(self, extension: str) -> dict:
        """Return only the verifier fields required by the local SIP runtime."""
        number = clean_number(extension, "Nebenstelle")
        with self._db() as db:
            row = db.execute(
                "SELECT extension,auth_user,digest_ha1,enabled FROM telephony_profile WHERE extension=?",
                (number,),
            ).fetchone()
        if row is None:
            raise KeyError("Nebenstelle nicht gefunden")
        return {**dict(row), "enabled": bool(row["enabled"])}

    def delete_profile(self, extension: str) -> None:
        number = clean_number(extension, "Nebenstelle")
        with self._db() as db:
            cursor = db.execute("DELETE FROM telephony_profile WHERE extension=?", (number,))
            if cursor.rowcount != 1:
                raise KeyError("Nebenstelle nicht gefunden")

    def setup_values(self, extension: str) -> dict:
        profile = self.profile(extension)
        settings = self.settings()
        host = settings["registrar_host"]
        if not host:
            try:
                from simpleoffice_sip_runtime import effective_sip_settings
                effective = effective_sip_settings(self.root.parent / "mini-services.json")
                host = str(effective.get("advertised_host") or "")
                settings = {**settings, "registrar_port": str(effective["registrar_port"]), "transport": effective["transport"]}
            except (OSError, ValueError):
                host = ""
        uri_host = f"[{host}]" if ":" in host else host
        return {
            **profile,
            **settings,
            "registrar_host": host,
            "sip_uri": f"sip:{profile['extension']}@{uri_host}" if host else "",
            "runtime_ready": False,
        }