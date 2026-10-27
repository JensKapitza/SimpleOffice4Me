"""Read-only IMAP archive and versioned ManageSieve integration."""

from __future__ import annotations

import base64
import hashlib
import imaplib
import json
import os
import re
import smtplib
import socket
import ssl
import tempfile
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email import policy
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .attachment_security import AttachmentSecurity
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .mail_env_credentials import environment_mail_password
from .revision_history import RevisionHistory
from .v2.storage_runtime import create_document

MAX_MESSAGE_BYTES = 100 * 1024 * 1024
MAX_MESSAGES_PER_RUN = 1000
MAX_SCRIPT_BYTES = 1024 * 1024
MAX_OUTBOUND_BYTES = 25 * 1024 * 1024
MAX_RECIPIENTS = 100
MAX_OUTBOUND_ATTACHMENTS = 20

MAIL_UI_FEATURES = (
    "inbox", "archive", "duplicates", "compose", "cases",
    "participants", "comments", "drafts", "delegation",
    "contacts", "calendar", "sieve",
)


def _mail_ui_features(value: object | None = None) -> dict[str, bool]:
    defaults = {name: True for name in MAIL_UI_FEATURES}
    if not isinstance(value, dict):
        return defaults
    return {name: bool(value.get(name, True)) for name in MAIL_UI_FEATURES}


def _form_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "on", "yes"}



class ImapAuthenticationError(RuntimeError):
    """Safe, actionable IMAP authentication failure without credentials."""

    def __init__(self, diagnostic: dict[str, Any]):
        self.diagnostic = diagnostic
        mechanisms = ", ".join(diagnostic["advertised_authentication"]) or "nicht angekündigt"
        hints = " ".join(diagnostic["hints"])
        super().__init__(
            f"Anmeldeverfahren {diagnostic['attempted']} abgewiesen. "
            f"Vom Server angekündigte AUTH-Verfahren: {mechanisms}. {hints}"
        )


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("invalid account identifier")
    return value


def _owner_key(actor: str) -> str:
    if not actor.strip():
        raise ValueError("a named user is required")
    return hashlib.sha256(actor.encode("utf-8")).hexdigest()[:32]


class SmtpDeliveryStateUnknown(RuntimeError):
    """SMTP may already have accepted at least one recipient; automatic retry is unsafe."""

    def __init__(self, delivery_status: str, result: dict[str, Any]):
        super().__init__("SMTP delivery state is not safely retryable")
        self.delivery_status = delivery_status
        self.result = dict(result)

    @property
    def user_message(self) -> str:
        if self.delivery_status == "accepted_unarchived":
            return (
                "Der SMTP-Server hat die Nachricht angenommen, aber der Archivstatus konnte "
                "nicht bestätigt werden. Nicht erneut senden; Archiv und Serverprotokoll prüfen."
            )
        if self.delivery_status == "partial":
            return (
                "Der SMTP-Server hat die Nachricht nur für einen Teil der Empfänger angenommen. "
                "Nicht erneut an alle senden; Empfängerstatus und Serverprotokoll prüfen."
            )
        return (
            "Der SMTP-Zustand ist unklar: Die Nachricht könnte bereits angenommen worden sein. "
            "Nicht erneut senden; Versand/Archiv und Serverprotokoll prüfen."
        )


class SecretBox:
    """Encrypt saved mail secrets with an installation-specific master key."""

    def __init__(self, master_key: bytes):
        if len(master_key) < 16:
            raise ValueError("mail secret master key is too short")
        self.key = hashlib.sha256(b"simpleoffice-mail-v1\0" + master_key).digest()

    @staticmethod
    def _aad(context: str) -> bytes:
        return b"simpleoffice-mail-v2\0" + context.encode("utf-8")

    def encrypt(self, value: str, *, username: str = "") -> str:
        nonce = os.urandom(12)
        if username:
            ciphertext = AESGCM(self.key).encrypt(nonce, value.encode("utf-8"), self._aad(username))
            prefix = "enc:v2:"
        else:
            ciphertext = AESGCM(self.key).encrypt(nonce, value.encode("utf-8"), b"simpleoffice-mail-v1")
            prefix = "enc:v1:"
        return prefix + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str, *, username: str = "") -> str:
        raw = base64.urlsafe_b64decode(value.removeprefix("enc:v1:").removeprefix("enc:v2:").encode("ascii"))
        if value.startswith("enc:v2:"):
            if not username:
                raise ValueError("mail secret username context is required")
            aad = self._aad(username)
        else:
            aad = b"simpleoffice-mail-v1"
        return AESGCM(self.key).decrypt(raw[:12], raw[12:], aad).decode("utf-8")


class MailStore:
    def __init__(self, root: str | Path, master_key: bytes):
        self.root = Path(root).resolve()
        self.control = self.root / CONTROL_DIR / "mail"
        self.accounts_path = self.control / "accounts.json"
        self.index_path = self.control / "archive-index.json"
        self.scripts = self.control / "sieve"
        self.secrets = SecretBox(master_key)
        self.history = RevisionHistory(self.root)

    def _read(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else default
        except (OSError, json.JSONDecodeError):
            return default

    def accounts(self, actor: str) -> list[dict[str, Any]]:
        rows = self._read(self.accounts_path, {"accounts": []}).get("accounts", [])
        result = []
        for row in rows:
            if row.get("owner") != actor:
                continue
            safe = {k: v for k, v in row.items() if k not in {"password", "smtp_password"}}
            safe["password_saved"] = bool(row.get("password"))
            safe["smtp_password_saved"] = bool(row.get("smtp_password"))
            stored_password = str(row.get("password", "") or "")
            safe["password_state"] = (
                "stored" if stored_password.startswith("enc:") else
                "legacy" if stored_password else
                "environment" if row.get("password_env") else "missing"
            )
            safe["auto_fetch_enabled"] = _form_bool(row.get("auto_fetch_enabled", True))
            safe["ui_features"] = _mail_ui_features(row.get("ui_features"))
            result.append(safe)
        return result

    def _owned_row(self, actor: str, account_id: str) -> dict[str, Any]:
        account_id = _safe_id(account_id)
        row = next((x for x in self._read(self.accounts_path, {"accounts": []}).get("accounts", []) if x.get("id") == account_id and x.get("owner") == actor), None)
        if row is None:
            raise KeyError("mail account does not exist")
        return dict(row)

    def _secret(self, row: dict[str, Any], field: str, username: str) -> tuple[str, bool]:
        """Resolve encrypted and legacy plaintext secrets without blocking service use."""
        value = str(row.get(field, "") or "")
        if not value:
            return "", False
        if value.startswith("enc:"):
            return self.secrets.decrypt(value, username=username), False
        return value, True

    def _migrate_secret(self, actor: str, account_id: str, field: str, value: str, username: str) -> None:
        if not value:
            return
        payload = self._read(self.accounts_path, {"version": 1, "accounts": []})
        for row in payload.get("accounts", []):
            if row.get("id") == account_id and row.get("owner") == actor and row.get(field) == value:
                row[field] = self.secrets.encrypt(value, username=username)
                atomic_json_write(self.accounts_path, payload)
                return

    def ui_features(self, owner: str, account_id: str) -> dict[str, bool]:
        """Return non-secret UI visibility preferences for one owned mail account."""
        row = self._owned_row(owner, account_id)
        return _mail_ui_features(row.get("ui_features"))

    def account(self, actor: str, account_id: str, password: str = "", *, protocol: str = "imap") -> dict[str, Any]:
        row = self._owned_row(actor, account_id)
        result = dict(row)
        if password:
            result["plain_password"] = password
        elif row.get("password"):
            result["plain_password"], legacy = self._secret(row, "password", str(row.get("username", "")))
            if legacy:
                self._migrate_secret(actor, account_id, "password", str(row["password"]), str(row.get("username", "")))
        else:
            env_name = str(row.get("password_env", ""))
            result["plain_password"] = environment_mail_password(actor, row, protocol, env_name)
        if not result["plain_password"]:
            raise ValueError("password is required for this operation")
        return result

    def smtp_account(self, actor: str, account_id: str, password: str = "") -> dict[str, Any]:
        """Return an owned account with a separately resolved SMTP secret."""
        row = self._owned_row(actor, account_id)
        # Older stored accounts remain usable without a migration.
        row.setdefault("smtp_host", row["host"])
        row.setdefault("smtp_port", 587)
        row.setdefault("smtp_security", "starttls")
        row.setdefault("smtp_username", row["username"])
        row.setdefault("smtp_from", row["username"])
        if password:
            row["smtp_plain_password"] = password
        elif row.get("smtp_password"):
            smtp_username = str(row.get("smtp_username") or row["username"])
            row["smtp_plain_password"], legacy = self._secret(row, "smtp_password", smtp_username)
            if legacy:
                self._migrate_secret(actor, account_id, "smtp_password", str(row["smtp_password"]), smtp_username)
        else:
            env_name = str(row.get("smtp_password_env", ""))
            row["smtp_plain_password"] = environment_mail_password(actor, row, "smtp", env_name)
            if not row["smtp_plain_password"]:
                # Explicitly configured reuse is convenient for common combined mail accounts.
                if row.get("password"):
                    imap_username = str(row.get("username", ""))
                    row["smtp_plain_password"], legacy = self._secret(
                        row, "password", imap_username
                    )
                    if legacy:
                        self._migrate_secret(
                            actor, account_id, "password", str(row["password"]), imap_username
                        )
                else:
                    imap_env = str(row.get("password_env", ""))
                    row["smtp_plain_password"] = environment_mail_password(actor, row, "smtp", imap_env)
        if not row["smtp_plain_password"]:
            raise ValueError("SMTP password is required for this operation")
        return row

    def save_account(self, actor: str, data: dict[str, Any], password: str, remember: bool) -> dict[str, Any]:
        with exclusive_file_lock(self.accounts_path.with_suffix(".lock")):
            return self._save_account_locked(actor, data, password, remember)

    def _save_account_locked(
        self, actor: str, data: dict[str, Any], password: str, remember: bool,
    ) -> dict[str, Any]:
        host = str(data.get("host", "")).strip()
        username = str(data.get("username", "")).strip()
        if not host or len(host) > 253 or not username or len(username) > 320:
            raise ValueError("server and username are required")
        if any(char in host for char in "/\\\x00"):
            raise ValueError("invalid server name")
        mode = str(data.get("security", "tls"))
        if mode not in {"tls", "starttls"}:
            raise ValueError("only TLS or STARTTLS is supported")
        auth_method = str(data.get("auth_method", "auto")).lower()
        if auth_method not in {"auto", "login", "plain"}:
            raise ValueError("IMAP authentication method must be auto, login or plain")
        port = int(data.get("port", 993 if mode == "tls" else 143))
        sieve_port = int(data.get("sieve_port", 4190))
        smtp_mode = str(data.get("smtp_security", "starttls"))
        if smtp_mode not in {"tls", "starttls"}:
            raise ValueError("SMTP supports only TLS or STARTTLS")
        smtp_port = int(data.get("smtp_port", 465 if smtp_mode == "tls" else 587))
        if not 1 <= port <= 65535 or not 1 <= sieve_port <= 65535 or not 1 <= smtp_port <= 65535:
            raise ValueError("invalid port")
        account_id = str(data.get("id", "")).strip() or uuid.uuid4().hex
        _safe_id(account_id)
        payload = self._read(self.accounts_path, {"version": 1, "accounts": []})
        previous = next((x for x in payload["accounts"] if x.get("id") == account_id and x.get("owner") == actor), None)
        smtp_host = str(data.get("smtp_host", host)).strip()[:253] or host
        if any(char in smtp_host for char in "/\\\x00"):
            raise ValueError("invalid SMTP server name")
        smtp_from = str(data.get("smtp_from", username)).strip()[:320] or username
        if len(_mailboxes(smtp_from)) != 1:
            raise ValueError("exactly one SMTP sender address is required")
        smtp_username = str(data.get("smtp_username", username)).strip()[:320] or username
        stored_password = (previous or {}).get("password", "")
        if password:
            stored_password = self.secrets.encrypt(password, username=username) if remember else ""
        elif _form_bool(data.get("clear_saved_password")):
            stored_password = ""
        elif stored_password:
            if str(stored_password).startswith("enc:v2:"):
                old_username = str((previous or {}).get("username", ""))
                if old_username != username:
                    old_password = self.secrets.decrypt(str(stored_password), username=old_username)
                    stored_password = self.secrets.encrypt(old_password, username=username)
            elif not str(stored_password).startswith("enc:"):
                stored_password = self.secrets.encrypt(str(stored_password), username=username)
        stored_smtp_password = (previous or {}).get("smtp_password", "")
        if data.get("smtp_password"):
            stored_smtp_password = self.secrets.encrypt(str(data["smtp_password"]), username=smtp_username) if remember else ""
        elif _form_bool(data.get("clear_saved_smtp_password")):
            stored_smtp_password = ""
        elif stored_smtp_password:
            if str(stored_smtp_password).startswith("enc:v2:"):
                old_smtp_username = str((previous or {}).get("smtp_username") or (previous or {}).get("username", ""))
                if old_smtp_username != smtp_username:
                    old_password = self.secrets.decrypt(str(stored_smtp_password), username=old_smtp_username)
                    stored_smtp_password = self.secrets.encrypt(old_password, username=smtp_username)
            elif not str(stored_smtp_password).startswith("enc:"):
                stored_smtp_password = self.secrets.encrypt(str(stored_smtp_password), username=smtp_username)
        row = {
            "id": account_id, "owner": actor, "label": str(data.get("label", host)).strip()[:120] or host,
            "host": host, "port": port, "security": mode, "username": username,
            "auth_method": auth_method,
            "folder": str(data.get("folder", "INBOX")).strip()[:500] or "INBOX",
            "sieve_host": str(data.get("sieve_host", host)).strip()[:253] or host,
            "sieve_port": sieve_port, "sieve_security": "starttls",
            "password_env": str(data.get("password_env", "")).strip()[:120],
            "password": stored_password,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port, "smtp_security": smtp_mode,
            "smtp_username": smtp_username,
            "smtp_from": smtp_from,
            "smtp_password_env": str(data.get("smtp_password_env", "")).strip()[:120],
            "smtp_password": stored_smtp_password,
            "auto_fetch_enabled": (
                _form_bool(data.get("auto_fetch_enabled"))
                if "auto_fetch_enabled" in data
                else _form_bool((previous or {}).get("auto_fetch_enabled", True))
            ),
            "ui_features": (
                {
                    name: str(data.get(f"ui_{name}", "")) == "1"
                    for name in MAIL_UI_FEATURES
                }
                if any(f"ui_{name}" in data for name in MAIL_UI_FEATURES)
                else _mail_ui_features((previous or {}).get("ui_features"))
            ),
            "updated_at": utc_now(),
        }
        payload["accounts"] = [x for x in payload["accounts"] if not (x.get("id") == account_id and x.get("owner") == actor)] + [row]
        self.control.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.accounts_path, payload)
        safe = {k: v for k, v in row.items() if k not in {"password", "smtp_password"}}
        safe["password_saved"] = bool(row["password"])
        safe["smtp_password_saved"] = bool(row["smtp_password"])
        safe["password_state"] = (
            "stored" if str(row["password"]).startswith("enc:") else
            "legacy" if row["password"] else
            "environment" if row["password_env"] else "missing"
        )
        safe["auto_fetch_enabled"] = row["auto_fetch_enabled"]
        safe["ui_features"] = _mail_ui_features(row.get("ui_features"))
        self.history.record("mail_account_updated" if previous else "mail_account_created", actor, "mail-accounts", account_id, safe)
        return safe

    def set_auto_fetch_enabled(self, actor: str, account_id: str, enabled: bool) -> bool:
        account_id = _safe_id(account_id)
        with exclusive_file_lock(self.accounts_path.with_suffix(".lock")):
            payload = self._read(self.accounts_path, {"version": 1, "accounts": []})
            row = next((
                item for item in payload.get("accounts", [])
                if item.get("id") == account_id and item.get("owner") == actor
            ), None)
            if row is None:
                raise KeyError("mail account does not exist")
            row["auto_fetch_enabled"] = bool(enabled)
            self.control.mkdir(parents=True, exist_ok=True)
            atomic_json_write(self.accounts_path, payload)
        self.history.record(
            "mail_auto_fetch_updated", actor, "mail-accounts", account_id,
            {"auto_fetch_enabled": bool(enabled)},
        )
        return bool(enabled)

    def _sieve_directory(self, actor: str, account_id: str, *, create: bool) -> Path | None:
        """Resolve a non-symlink account directory beneath the private Sieve root."""
        account_id = _safe_id(account_id)
        directories = (
            self.root / CONTROL_DIR,
            self.control,
            self.scripts,
            self.scripts / _owner_key(actor),
            self.scripts / _owner_key(actor) / account_id,
        )
        for directory in directories:
            if directory.is_symlink():
                raise ValueError("mail Sieve storage contains an unsafe symbolic link")
            if create:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            elif not directory.exists():
                return None
            if not directory.is_dir():
                raise ValueError("mail Sieve storage path is not a directory")
            if not directory.resolve(strict=True).is_relative_to(self.root):
                raise ValueError("mail Sieve storage resolved outside the document root")
        return directories[-1].resolve(strict=True)

    @staticmethod
    def _safe_sieve_name(name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) or name in {".", ".."}:
            raise ValueError("invalid Sieve script name")
        return name

    def _sieve_script_path(
        self, actor: str, account_id: str, name: str, *, create_directory: bool,
    ) -> Path:
        safe_name = self._safe_sieve_name(name)
        directory = self._sieve_directory(actor, account_id, create=create_directory)
        if directory is None:
            raise FileNotFoundError("mail Sieve script directory does not exist")
        candidate = directory / f"{safe_name}.sieve"
        if candidate.is_symlink():
            raise ValueError("mail Sieve script path is a symbolic link")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(directory)
        except ValueError:
            raise ValueError("mail Sieve script resolved outside its account directory") from None
        return resolved

    def save_script(self, actor: str, account_id: str, name: str, content: str) -> dict[str, Any]:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_SCRIPT_BYTES or b"\0" in encoded:
            raise ValueError("Sieve script is too large or contains NUL")
        # Ownership check; never let another application user write below an account.
        self.account(actor, account_id, password="ownership-check")
        path = self._sieve_script_path(actor, account_id, name, create_directory=True)
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".sieve-", suffix=".tmp", delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
        digest = hashlib.sha512(encoded).hexdigest()
        snapshot = {"account_id": account_id, "name": name, "sha512": digest, "size": len(encoded), "updated_at": utc_now()}
        commit = self.history.record("sieve_script_saved", actor, "sieve", hashlib.sha256(f"{actor}:{account_id}:{name}".encode()).hexdigest(), snapshot)
        return {**snapshot, "revision": commit}

    def script(self, actor: str, account_id: str, name: str) -> str:
        self.account(actor, account_id, password="ownership-check")
        return self._sieve_script_path(
            actor, account_id, name, create_directory=False,
        ).read_text(encoding="utf-8")

    def scripts_for(self, actor: str, account_id: str) -> list[dict[str, Any]]:
        self.account(actor, account_id, password="ownership-check")
        folder = self._sieve_directory(actor, account_id, create=False)
        if folder is None:
            return []
        return [{"name": p.stem, "size": p.stat().st_size, "sha512": hashlib.sha512(p.read_bytes()).hexdigest()} for p in sorted(folder.glob("*.sieve")) if p.is_file() and not p.is_symlink()]

    def _archive_index(self) -> dict[str, Any]:
        """Only a genuinely absent checkpoint is empty; never erase unreadable state."""
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "accounts": {}}
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Archivzustand ist beschädigt; Sicherung prüfen.") from None
        if (not isinstance(payload, dict) or not isinstance(payload.get("accounts"), dict)
                or any(not isinstance(row, dict) for row in payload["accounts"].values())):
            raise ValueError("Archivzustand ist beschädigt; Sicherung prüfen.")
        return payload

    def archive_state(self, actor: str, account_id: str) -> dict[str, Any]:
        return self._archive_index()["accounts"].get(f"{actor}:{_safe_id(account_id)}", {})

    def update_archive_state(self, actor: str, account_id: str, state: dict[str, Any]) -> None:
        with exclusive_file_lock(self.index_path.with_suffix(".lock")):
            payload = self._archive_index()
            payload.setdefault("accounts", {})[f"{actor}:{_safe_id(account_id)}"] = state
            self.control.mkdir(parents=True, exist_ok=True)
            atomic_json_write(self.index_path, payload)

    def ensure_private_archive(self, actor: str, account_id: str) -> Path:
        """Create an ACL boundary so DAV/SFTP users only see their own mail tree."""
        user_folder = self.root / "email" / _owner_key(actor)
        folder = user_folder / _safe_id(account_id)
        changed = False
        for boundary in (user_folder, folder):
            policy_path = DocumentStore(self.root).ensure_folder_policy(boundary, actor)
            with exclusive_file_lock(policy_path.with_suffix(".lock")):
                policy = self._read(policy_path, {})
                expected = [{"principal": actor, "role": "manage"}]
                if not (policy.get("access_enabled") is True and policy.get("grants") == expected and policy.get("inherit") is True):
                    policy.update({"version": max(3, int(policy.get("version", 0) or 0)), "access_enabled": True, "inherit": True, "grants": expected, "access_updated_at": utc_now(), "access_updated_by": actor})
                    atomic_json_write(policy_path, policy)
                    changed = True
        if changed:
            self.history.record("mail_archive_access_initialized", actor, "policies", hashlib.sha256(f"mail:{actor}:{account_id}".encode()).hexdigest(), {"account_id": account_id, "owner": actor, "role": "manage", "inherit": True})
        return folder

    def archive_outbound(self, actor: str, account: dict[str, Any], raw: bytes, state: str, detail: dict[str, Any]) -> dict[str, Any]:
        """Persist the exact submitted EML before transport and audit its state."""
        if len(raw) > MAX_OUTBOUND_BYTES:
            raise ValueError("outbound message exceeds 25 MiB")
        digest = hashlib.sha512(raw).hexdigest()
        self.ensure_private_archive(actor, account["id"])
        year = datetime.now(timezone.utc).strftime("%Y")
        path = f"email/{_owner_key(actor)}/{account['id']}/sent/{year}/{digest}.eml"
        documents = DocumentStore(self.root)
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        documents.ensure_folder_policy(target.parent, actor)
        if target.is_file() and not target.is_symlink() and hashlib.sha512(target.read_bytes()).hexdigest() == digest:
            document = documents.get_document(path)
        else:
            document = create_document(documents.root, actor, path, raw, max_bytes=MAX_OUTBOUND_BYTES)
            documents.set_tags(document["document_id"], ["email", "source:smtp", "direction:outbound", f"imap-account:{account['id']}"], actor)
        origin = {"account_id": account["id"], "sha512": digest, "state": state, **detail}
        documents.set_attribute(document["document_id"], "email_origin", origin, actor)
        self.history.record(f"smtp_message_{state}", actor, "mail-outbound", document["document_id"], origin)
        return {"document_id": document["document_id"], "path": path, "sha512": digest}


def _mailboxes(value: str) -> list[str]:
    if "\r" in value or "\n" in value:
        raise ValueError("mail addresses must not contain line breaks")
    parsed = [address.strip() for _, address in getaddresses([value]) if address.strip()]
    if not parsed or len(parsed) > MAX_RECIPIENTS:
        raise ValueError("one to 100 recipients are required")
    for address in parsed:
        local, separator, domain = address.rpartition("@")
        if not separator or not local or "." not in domain or len(address) > 254 or any(char.isspace() for char in address):
            raise ValueError(f"invalid recipient address: {address[:80]}")
    if len({value.casefold() for value in parsed}) != len(parsed):
        raise ValueError("duplicate recipients are not allowed")
    return parsed


def _optional_mailboxes(value: str) -> list[str]:
    return _mailboxes(value) if value.strip() else []


def _outbound_attachment(value: dict[str, Any]) -> tuple[str, str, str, bytes]:
    if not isinstance(value, dict):
        raise ValueError("attachment must be an object")
    filename = Path(str(value.get("filename", "")).replace("\\", "/")).name.strip(" .")
    content_type = str(value.get("content_type", "application/octet-stream")).strip().casefold()
    payload = value.get("payload")
    if not filename or len(filename) > 180 or any(ord(ch) < 32 or ord(ch) == 127 for ch in filename):
        raise ValueError("invalid attachment filename")
    if not isinstance(payload, (bytes, bytearray)):
        raise ValueError("attachment payload must be bytes")
    raw = bytes(payload)
    if len(raw) > MAX_OUTBOUND_BYTES:
        raise ValueError("attachment exceeds outbound message limit")
    maintype, separator, subtype = content_type.partition("/")
    token = re.compile(r"^[a-z0-9!#$&^_.+-]{1,100}$")
    if not separator or not token.fullmatch(maintype) or not token.fullmatch(subtype):
        raise ValueError("invalid attachment content type")
    return filename, maintype, subtype, raw


class SmtpSubmission:
    """Authenticated RFC 6409 submission with mandatory local EML archiving."""

    def __init__(self, store: MailStore, timeout: int = 30):
        self.store, self.timeout = store, timeout

    @staticmethod
    def _safe_transport_error(exc: BaseException) -> dict[str, Any]:
        """Describe a transport failure without persisting remote response text."""
        detail: dict[str, Any] = {"error_type": type(exc).__name__}
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            detail["error_code"] = "authentication_failed"
        elif isinstance(exc, smtplib.SMTPRecipientsRefused):
            detail["error_code"] = "recipients_refused"
        elif isinstance(exc, smtplib.SMTPSenderRefused):
            detail["error_code"] = "sender_refused"
        elif isinstance(exc, smtplib.SMTPDataError):
            detail["error_code"] = "message_rejected"
        elif isinstance(exc, (TimeoutError, socket.timeout)):
            detail["error_code"] = "timeout"
        elif isinstance(exc, (ConnectionError, OSError)):
            detail["error_code"] = "connection_failed"
        else:
            detail["error_code"] = "transport_failed"
        if isinstance(exc, smtplib.SMTPResponseException):
            # The numeric status is useful and non-secret. smtp_error is an
            # untrusted remote string and must never enter metadata/history.
            detail["smtp_code"] = int(getattr(exc, "smtp_code", 0) or 0)
        return detail

    @staticmethod
    def compose(
        account: dict[str, Any], recipients: str, subject: str, body: str,
        calendar_data: str = "", *, cc: str = "", bcc: str = "",
        attachments: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    ) -> tuple[bytes, list[str], str]:
        to_targets = _mailboxes(recipients)
        cc_targets = _optional_mailboxes(cc)
        bcc_targets = _optional_mailboxes(bcc)
        targets = [*to_targets, *cc_targets, *bcc_targets]
        if len(targets) > MAX_RECIPIENTS:
            raise ValueError("one to 100 recipients are required")
        if len({address.casefold() for address in targets}) != len(targets):
            raise ValueError("duplicate recipients are not allowed")
        sender = _mailboxes(str(account.get("smtp_from", account.get("username", ""))))
        if len(sender) != 1:
            raise ValueError("exactly one sender address is required")
        if not subject.strip() or len(subject.encode("utf-8")) > 998 or "\r" in subject or "\n" in subject:
            raise ValueError("a safe subject is required")
        if len(body.encode("utf-8")) > 1024 * 1024:
            raise ValueError("message body exceeds 1 MiB")
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = sender[0]
        message["To"] = ", ".join(to_targets)
        if cc_targets:
            message["Cc"] = ", ".join(cc_targets)
        message["Subject"] = subject.strip()
        message["Date"] = formatdate(localtime=True)
        message_id = make_msgid(domain=sender[0].rsplit("@", 1)[1])
        message["Message-ID"] = message_id
        message.set_content(body or "")
        if calendar_data:
            encoded = calendar_data.encode("utf-8")
            if len(encoded) > 1024 * 1024 or "BEGIN:VCALENDAR" not in calendar_data.upper() or "END:VCALENDAR" not in calendar_data.upper():
                raise ValueError("calendar data must be one VCALENDAR up to 1 MiB")
            method_match = re.search(r"(?im)^METHOD\s*:\s*([A-Z-]+)\s*$", calendar_data)
            if method_match is None:
                raise ValueError("iTIP calendar data requires METHOD")
            method = method_match.group(1).upper()
            if method not in {"REQUEST", "REPLY", "CANCEL", "COUNTER", "DECLINECOUNTER", "PUBLISH"}:
                raise ValueError("unsupported iTIP method")
            message.add_attachment(encoded, maintype="text", subtype="calendar", params={"method": method, "charset": "UTF-8"}, filename="termin.ics")
        attachment_rows = list(attachments)
        if len(attachment_rows) > MAX_OUTBOUND_ATTACHMENTS:
            raise ValueError("too many outbound attachments")
        for attachment in attachment_rows:
            filename, maintype, subtype, payload = _outbound_attachment(attachment)
            message.add_attachment(
                payload, maintype=maintype, subtype=subtype, filename=filename
            )
        raw = message.as_bytes(policy=policy.SMTP)
        if len(raw) > MAX_OUTBOUND_BYTES:
            raise ValueError("outbound message exceeds 25 MiB")
        return raw, targets, message_id

    def _connect(self, account: dict[str, Any]):
        context = ssl.create_default_context()
        if account["smtp_security"] == "tls":
            client = smtplib.SMTP_SSL(account["smtp_host"], account["smtp_port"], timeout=self.timeout, context=context)
            client.ehlo()
        else:
            client = smtplib.SMTP(account["smtp_host"], account["smtp_port"], timeout=self.timeout)
            client.ehlo()
            if not client.has_extn("starttls"):
                client.close()
                raise RuntimeError("SMTP server does not advertise STARTTLS")
            client.starttls(context=context)
            client.ehlo()
        client.login(account["smtp_username"], account["smtp_plain_password"])
        return client

    def test(self, account: dict[str, Any]) -> dict[str, Any]:
        client = self._connect(account)
        try:
            return {"ok": True, "features": sorted(client.esmtp_features)}
        finally:
            try: client.quit()
            except Exception: client.close()

    def send(
        self, actor: str, account: dict[str, Any], recipients: str, subject: str,
        body: str, calendar_data: str = "", *, cc: str = "", bcc: str = "",
        attachments: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        raw, targets, message_id = self.compose(
            account, recipients, subject, body, calendar_data,
            cc=cc, bcc=bcc, attachments=attachments,
        )
        detail = {
            "message_id": message_id,
            "from": account["smtp_from"],
            "recipients": targets,
            "subject": subject.strip()[:500],
            "calendar": bool(calendar_data),
            "attachment_count": len(attachments),
            "at": utc_now(),
        }
        archived = self.store.archive_outbound(actor, account, raw, "pending", detail)

        def archive_state(state: str, extra: dict[str, Any] | None = None) -> None:
            try:
                self.store.archive_outbound(
                    actor, account, raw, state, {**detail, **(extra or {})}
                )
            except Exception:
                # The pending EML already exists. Do not turn an audit/storage
                # follow-up failure into a reason to submit the message again.
                pass

        try:
            client = self._connect(account)
        except Exception as exc:
            archive_state("failed", self._safe_transport_error(exc))
            raise

        try:
            try:
                refused = client.sendmail(account["smtp_from"], targets, raw)
            except (
                smtplib.SMTPRecipientsRefused,
                smtplib.SMTPSenderRefused,
                smtplib.SMTPAuthenticationError,
                smtplib.SMTPDataError,
            ) as exc:
                archive_state("failed", self._safe_transport_error(exc))
                raise
            except Exception as exc:
                archive_state("unknown", self._safe_transport_error(exc))
                raise SmtpDeliveryStateUnknown(
                    "unknown", {**archived, "message_id": message_id, "recipients": len(targets)}
                ) from exc

            if refused:
                if len(refused) >= len(targets):
                    archive_state("failed", {"refused_recipients": len(refused)})
                    raise RuntimeError(f"SMTP refused {len(refused)} recipient(s)")
                archive_state("partial", {"refused_recipients": len(refused)})
                raise SmtpDeliveryStateUnknown(
                    "partial", {**archived, "message_id": message_id, "recipients": len(targets)}
                )
        finally:
            try:
                client.quit()
            except Exception:
                try:
                    client.close()
                except Exception:
                    pass

        try:
            self.store.archive_outbound(actor, account, raw, "sent", detail)
        except Exception as exc:
            raise SmtpDeliveryStateUnknown(
                "accepted_unarchived",
                {**archived, "message_id": message_id, "recipients": len(targets)},
            ) from exc
        return {**archived, "message_id": message_id, "recipients": len(targets)}



class ImapArchive:
    def __init__(self, store: MailStore):
        self.store = store

    @staticmethod
    def _literal(response: Any) -> bytes:
        for item in response or []:
            if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
                return item[1]
        raise RuntimeError("IMAP server returned no message body")

    def _connect(self, account: dict[str, Any], *, password: str | None = None):
        context = ssl.create_default_context()
        timeout = 30
        credential = account["plain_password"] if password is None else password
        if account["security"] == "tls":
            connection = imaplib.IMAP4_SSL(account["host"], account["port"], ssl_context=context, timeout=timeout)
        else:
            connection = imaplib.IMAP4(account["host"], account["port"], timeout=timeout)
            connection.starttls(ssl_context=context)
        capabilities = {
            (value.decode("ascii", "replace") if isinstance(value, bytes) else str(value)).upper()
            for value in connection.capabilities
        }
        advertised = {value[5:] for value in capabilities if value.startswith("AUTH=")}
        configured_method = str(account.get("auth_method", "auto")).lower()
        method = "plain" if configured_method == "plain" or (configured_method == "auto" and "LOGINDISABLED" in capabilities and "PLAIN" in advertised) else "login"
        attempted = "SASL PLAIN over TLS" if method == "plain" else "IMAP LOGIN over TLS"
        try:
            if method == "plain":
                status, _ = connection.authenticate(
                    "PLAIN", lambda _challenge: f"\0{account['username']}\0{credential}".encode("utf-8")
                )
            else:
                status, _ = connection.login(account["username"], credential)
        except imaplib.IMAP4.error as exc:
            diagnostic = self.authentication_diagnostic(account, capabilities, exc, attempted)
            try: connection.logout()
            except Exception: pass
            raise ImapAuthenticationError(diagnostic) from None
        if status != "OK":
            raise ImapAuthenticationError(self.authentication_diagnostic(account, capabilities, attempted=attempted))
        return connection

    @staticmethod
    def authentication_diagnostic(account: dict[str, Any], capabilities: set[str], error: BaseException | None = None, attempted: str = "IMAP LOGIN over TLS") -> dict[str, Any]:
        advertised = sorted(value[5:] for value in capabilities if value.startswith("AUTH=") and len(value) > 5)
        hints = []
        if "LOGINDISABLED" in capabilities:
            hints.append("Der Server verbietet IMAP LOGIN; verwenden Sie ein App-Passwort oder einen Anbieter mit freigeschaltetem IMAP.")
        if any(value in advertised for value in ("XOAUTH2", "OAUTHBEARER")):
            hints.append("Der Server bietet OAuth an; diese Installation unterstützt dafür noch keinen interaktiven OAuth-Login.")
        hints.append("Prüfen Sie vollständigen Benutzernamen, IMAP-Freigabe und ein gegebenenfalls erforderliches App-Passwort beim Anbieter.")
        reason = ""
        if error:
            # Authentication replies are untrusted and may contain identifiers.
            # Keep only a small classifiable status, never the supplied secret.
            raw = str(error).upper()
            reason = next((item for item in ("AUTHENTICATIONFAILED", "AUTHORIZATIONFAILED", "UNAVAILABLE") if item in raw), "rejected")
        return {
            "host": str(account.get("host", ""))[:253], "port": int(account.get("port", 0)),
            "transport": account.get("security", ""), "attempted": attempted,
            "advertised_authentication": advertised, "login_disabled": "LOGINDISABLED" in capabilities,
            "reason": reason, "hints": hints,
        }

    def test(self, account: dict[str, Any]) -> dict[str, Any]:
        connection = self._connect(account)
        try:
            status, folders = connection.list()
            if status != "OK":
                raise RuntimeError("IMAP LIST failed")
            return {"ok": True, "folders": len(folders or []), "capabilities": sorted(x.decode("ascii", "replace") if isinstance(x, bytes) else str(x) for x in connection.capabilities)}
        finally:
            connection.logout()

    def archive(
        self,
        actor: str,
        account_id: str,
        account_config: dict[str, Any],
        password: str,
        *,
        limit: int = 250,
        extract_attachments: bool = False,
    ) -> dict[str, Any]:
        from .imap_archive_recovery import ArchiveRecovery

        account_id = _safe_id(account_id)
        if not isinstance(account_config, dict) or account_config.get("id") != account_id:
            raise ValueError("IMAP archive account configuration does not match its ID")
        if not isinstance(password, str) or not password:
            raise ValueError("IMAP archive password is required")
        public_fields = ("host", "port", "security", "username", "auth_method", "folder")
        archive_config = {"id": account_id}
        archive_config.update({key: account_config[key] for key in public_fields if key in account_config})
        for key in ("host", "port", "security", "username", "folder"):
            if key not in archive_config:
                raise ValueError(f"IMAP archive account configuration is missing {key}")
        return ArchiveRecovery(self, actor, account_id, archive_config, password).run(
            max(1, min(int(limit), MAX_MESSAGES_PER_RUN)), extract_attachments,
        )



class ManageSieveClient:
    """Small RFC 5804 client for TLS + SASL PLAIN and script management."""

    def __init__(self, host: str, port: int = 4190, timeout: int = 30):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock: ssl.SSLSocket | None = None
        self.file = None

    def connect(self, username: str, password: str) -> None:
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.file = raw.makefile("rwb")
        self._response()
        self._command("STARTTLS")
        self.file.close()
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.sock = context.wrap_socket(raw, server_hostname=self.host)
        self.file = self.sock.makefile("rwb")
        # RFC 5804 requires capabilities to be refreshed after TLS negotiation.
        self._command("CAPABILITY")
        auth = base64.b64encode(b"\0" + username.encode() + b"\0" + password.encode()).decode("ascii")
        self._command(f'AUTHENTICATE "PLAIN" "{auth}"')

    def close(self) -> None:
        if self.file:
            try: self._command("LOGOUT")
            except Exception: pass
            self.file.close()
        if self.sock: self.sock.close()

    def _response(self) -> list[str]:
        if self.file is None:
            raise RuntimeError("ManageSieve is not connected")
        lines = []
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("ManageSieve connection closed")
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            lines.append(text)
            if text.upper().startswith(("OK", "NO", "BYE")):
                if not text.upper().startswith("OK"):
                    raise RuntimeError(f"ManageSieve rejected command: {text[:300]}")
                return lines

    def _command(self, command: str) -> list[str]:
        if self.file is None:
            raise RuntimeError("ManageSieve is not connected")
        self.file.write(command.encode("utf-8") + b"\r\n")
        self.file.flush()
        return self._response()

    def put_script(self, name: str, content: str, *, activate: bool = False) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
            raise ValueError("invalid Sieve script name")
        payload = content.encode("utf-8")
        if len(payload) > MAX_SCRIPT_BYTES:
            raise ValueError("Sieve script exceeds 1 MiB")
        if self.file is None:
            raise RuntimeError("ManageSieve is not connected")
        self.file.write(f'PUTSCRIPT "{name}" {{{len(payload)}}}\r\n'.encode("ascii"))
        self.file.flush()
        continuation = self.file.readline()
        if not continuation.startswith(b"+"):
            raise RuntimeError(f"ManageSieve did not accept script literal: {continuation.decode('utf-8', 'replace')[:300]}")
        self.file.write(payload + b"\r\n")
        self.file.flush()
        self._response()
        if activate:
            self._command(f'SETACTIVE "{name}"')
