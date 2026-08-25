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
from .revision_history import RevisionHistory

MAX_MESSAGE_BYTES = 100 * 1024 * 1024
MAX_MESSAGES_PER_RUN = 1000
MAX_SCRIPT_BYTES = 1024 * 1024
MAX_OUTBOUND_BYTES = 25 * 1024 * 1024
MAX_RECIPIENTS = 100


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


class SecretBox:
    """Encrypt saved mail secrets with an installation-specific master key."""

    def __init__(self, master_key: bytes):
        if len(master_key) < 16:
            raise ValueError("mail secret master key is too short")
        self.key = hashlib.sha256(b"simpleoffice-mail-v1\0" + master_key).digest()

    def encrypt(self, value: str) -> str:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key).encrypt(nonce, value.encode("utf-8"), b"simpleoffice-mail-v1")
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str) -> str:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
        return AESGCM(self.key).decrypt(raw[:12], raw[12:], b"simpleoffice-mail-v1").decode("utf-8")


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
            result.append(safe)
        return result

    def _owned_row(self, actor: str, account_id: str) -> dict[str, Any]:
        account_id = _safe_id(account_id)
        row = next((x for x in self._read(self.accounts_path, {"accounts": []}).get("accounts", []) if x.get("id") == account_id and x.get("owner") == actor), None)
        if row is None:
            raise KeyError("mail account does not exist")
        return dict(row)

    def account(self, actor: str, account_id: str, password: str = "") -> dict[str, Any]:
        row = self._owned_row(actor, account_id)
        result = dict(row)
        if password:
            result["plain_password"] = password
        elif row.get("password"):
            result["plain_password"] = self.secrets.decrypt(str(row["password"]))
        else:
            env_name = str(row.get("password_env", ""))
            result["plain_password"] = os.environ.get(env_name, "") if env_name else ""
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
            row["smtp_plain_password"] = self.secrets.decrypt(str(row["smtp_password"]))
        else:
            env_name = str(row.get("smtp_password_env", ""))
            row["smtp_plain_password"] = os.environ.get(env_name, "") if env_name else ""
            if not row["smtp_plain_password"]:
                # Explicitly configured reuse is convenient for common combined mail accounts.
                if row.get("password"):
                    row["smtp_plain_password"] = self.secrets.decrypt(str(row["password"]))
                else:
                    imap_env = str(row.get("password_env", ""))
                    row["smtp_plain_password"] = os.environ.get(imap_env, "") if imap_env else ""
        if not row["smtp_plain_password"]:
            raise ValueError("SMTP password is required for this operation")
        return row

    def save_account(self, actor: str, data: dict[str, Any], password: str, remember: bool) -> dict[str, Any]:
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
        stored_password = (previous or {}).get("password", "")
        if password:
            stored_password = self.secrets.encrypt(password) if remember else ""
        stored_smtp_password = (previous or {}).get("smtp_password", "")
        if data.get("smtp_password"):
            stored_smtp_password = self.secrets.encrypt(str(data["smtp_password"])) if remember else ""
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
            "smtp_username": str(data.get("smtp_username", username)).strip()[:320] or username,
            "smtp_from": smtp_from,
            "smtp_password_env": str(data.get("smtp_password_env", "")).strip()[:120],
            "smtp_password": stored_smtp_password,
            "updated_at": utc_now(),
        }
        payload["accounts"] = [x for x in payload["accounts"] if not (x.get("id") == account_id and x.get("owner") == actor)] + [row]
        self.control.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.accounts_path, payload)
        safe = {k: v for k, v in row.items() if k not in {"password", "smtp_password"}}
        safe["password_saved"] = bool(row["password"])
        safe["smtp_password_saved"] = bool(row["smtp_password"])
        self.history.record("mail_account_updated" if previous else "mail_account_created", actor, "mail-accounts", account_id, safe)
        return safe

    def save_script(self, actor: str, account_id: str, name: str, content: str) -> dict[str, Any]:
        _safe_id(account_id)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) or name in {".", ".."}:
            raise ValueError("invalid Sieve script name")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_SCRIPT_BYTES or b"\0" in encoded:
            raise ValueError("Sieve script is too large or contains NUL")
        # Ownership check; never let another application user write below an account.
        self.account(actor, account_id, password="ownership-check")
        path = self.scripts / _owner_key(actor) / account_id / f"{name}.sieve"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        temporary.replace(path)
        digest = hashlib.sha512(encoded).hexdigest()
        snapshot = {"account_id": account_id, "name": name, "sha512": digest, "size": len(encoded), "updated_at": utc_now()}
        commit = self.history.record("sieve_script_saved", actor, "sieve", hashlib.sha256(f"{actor}:{account_id}:{name}".encode()).hexdigest(), snapshot)
        return {**snapshot, "revision": commit}

    def script(self, actor: str, account_id: str, name: str) -> str:
        self.account(actor, account_id, password="ownership-check")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
            raise ValueError("invalid Sieve script name")
        return (self.scripts / _owner_key(actor) / _safe_id(account_id) / f"{name}.sieve").read_text(encoding="utf-8")

    def scripts_for(self, actor: str, account_id: str) -> list[dict[str, Any]]:
        self.account(actor, account_id, password="ownership-check")
        folder = self.scripts / _owner_key(actor) / _safe_id(account_id)
        if not folder.is_dir():
            return []
        return [{"name": p.stem, "size": p.stat().st_size, "sha512": hashlib.sha512(p.read_bytes()).hexdigest()} for p in sorted(folder.glob("*.sieve")) if p.is_file() and not p.is_symlink()]

    def archive_state(self, actor: str, account_id: str) -> dict[str, Any]:
        return self._read(self.index_path, {"version": 1, "accounts": {}}).get("accounts", {}).get(f"{actor}:{_safe_id(account_id)}", {})

    def update_archive_state(self, actor: str, account_id: str, state: dict[str, Any]) -> None:
        with exclusive_file_lock(self.index_path.with_suffix(".lock")):
            payload = self._read(self.index_path, {"version": 1, "accounts": {}})
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
            document = documents.create_document_at(path, raw, actor, max_bytes=MAX_OUTBOUND_BYTES)
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


class SmtpSubmission:
    """Authenticated RFC 6409 submission with mandatory local EML archiving."""

    def __init__(self, store: MailStore, timeout: int = 30):
        self.store, self.timeout = store, timeout

    @staticmethod
    def compose(account: dict[str, Any], recipients: str, subject: str, body: str, calendar_data: str = "") -> tuple[bytes, list[str], str]:
        targets = _mailboxes(recipients)
        sender = _mailboxes(str(account.get("smtp_from", account.get("username", ""))))
        if len(sender) != 1:
            raise ValueError("exactly one sender address is required")
        if not subject.strip() or len(subject.encode("utf-8")) > 998 or "\r" in subject or "\n" in subject:
            raise ValueError("a safe subject is required")
        if len(body.encode("utf-8")) > 1024 * 1024:
            raise ValueError("message body exceeds 1 MiB")
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = sender[0]
        message["To"] = ", ".join(targets)
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

    def send(self, actor: str, account: dict[str, Any], recipients: str, subject: str, body: str, calendar_data: str = "") -> dict[str, Any]:
        raw, targets, message_id = self.compose(account, recipients, subject, body, calendar_data)
        detail = {"message_id": message_id, "from": account["smtp_from"], "recipients": targets, "subject": subject.strip()[:500], "calendar": bool(calendar_data), "at": utc_now()}
        archived = self.store.archive_outbound(actor, account, raw, "pending", detail)
        try:
            client = self._connect(account)
            try:
                refused = client.sendmail(account["smtp_from"], targets, raw)
                if refused:
                    raise RuntimeError(f"SMTP refused {len(refused)} recipient(s)")
            finally:
                try: client.quit()
                except Exception: client.close()
        except Exception as exc:
            self.store.archive_outbound(actor, account, raw, "failed", {**detail, "error": str(exc)[:500]})
            raise
        self.store.archive_outbound(actor, account, raw, "sent", detail)
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

    def _connect(self, account: dict[str, Any]):
        context = ssl.create_default_context()
        timeout = 30
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
                    "PLAIN", lambda _challenge: f"\0{account['username']}\0{account['plain_password']}".encode("utf-8")
                )
            else:
                status, _ = connection.login(account["username"], account["plain_password"])
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

    def archive(self, actor: str, account: dict[str, Any], *, limit: int = 250, extract_attachments: bool = False) -> dict[str, Any]:
        limit = max(1, min(int(limit), MAX_MESSAGES_PER_RUN))
        connection = self._connect(account)
        result = {"examined": 0, "archived": 0, "duplicates": 0, "attachments": 0, "errors": []}
        self.store.ensure_private_archive(actor, account["id"])
        state = self.store.archive_state(actor, account["id"])
        known = set(state.get("sha512", []))
        last_uid = int(state.get("last_uid", 0) or 0)
        try:
            status, _ = connection.select(account["folder"], readonly=True)  # EXAMINE: no flags, moves or deletes.
            if status != "OK":
                raise RuntimeError("IMAP EXAMINE failed")
            raw_validity = connection.untagged_responses.get("UIDVALIDITY", [b""])[0]
            uidvalidity = raw_validity.decode() if isinstance(raw_validity, bytes) else str(raw_validity)
            if state.get("uidvalidity") and state.get("uidvalidity") != uidvalidity:
                last_uid = 0
            criterion = f"UID {last_uid + 1}:*" if last_uid else "ALL"
            status, data = connection.uid("search", None, criterion)
            if status != "OK":
                raise RuntimeError("IMAP UID SEARCH failed")
            uids = (data[0].split() if data and data[0] else [])[:limit]
            for uid in uids:
                try:
                    status, fetched = connection.uid("fetch", uid, f"(UID RFC822.SIZE BODY.PEEK[])")
                    if status != "OK":
                        raise RuntimeError("IMAP UID FETCH failed")
                    raw = self._literal(fetched)
                    if len(raw) > MAX_MESSAGE_BYTES:
                        raise ValueError("message exceeds 100 MiB archive limit")
                    digest = hashlib.sha512(raw).hexdigest()
                    result["examined"] += 1
                    last_uid = max(last_uid, int(uid))
                    if digest in known:
                        result["duplicates"] += 1
                        continue
                    message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
                    year = datetime.now(timezone.utc).strftime("%Y")
                    path = f"email/{_owner_key(actor)}/{account['id']}/{year}/{digest}.eml"
                    doc_store = DocumentStore(self.store.root)
                    doc_store.ensure_folder_policy(self.store.root / Path(path).parent, actor)
                    target = self.store.root / path
                    if target.is_file() and not target.is_symlink() and hashlib.sha512(target.read_bytes()).hexdigest() == digest:
                        known.add(digest)
                        result["duplicates"] += 1
                        continue
                    document = doc_store.create_document_at(path, raw, actor, max_bytes=MAX_MESSAGE_BYTES)
                    known.add(digest)
                    doc_store.set_tags(document["document_id"], ["email", "source:imap", f"imap-account:{account['id']}"], actor)
                    doc_store.set_attribute(document["document_id"], "email_origin", {"account_id": account["id"], "folder": account["folder"], "uidvalidity": uidvalidity, "uid": uid.decode(), "sha512": digest, "message_id": str(message.get("Message-ID", ""))[:500], "subject": str(message.get("Subject", ""))[:500], "from": str(message.get("From", ""))[:500]}, actor)
                    result["archived"] += 1
                    if extract_attachments:
                        security = AttachmentSecurity(self.store.root)
                        manifest = security.preview_eml(document["document_id"], actor)
                        selected = [int(row["part"]) for row in manifest["attachments"]]
                        if selected:
                            extracted = security.extract(manifest["manifest_id"], selected, actor)
                            result["attachments"] += sum(1 for row in extracted if row.get("document_id"))
                except Exception as exc:
                    result["errors"].append({"uid": uid.decode("ascii", "replace"), "error": str(exc)[:500]})
            self.store.update_archive_state(actor, account["id"], {"uidvalidity": uidvalidity, "last_uid": last_uid, "sha512": sorted(known)[-100000:], "updated_at": utc_now()})
            self.store.history.record("imap_archive_completed", actor, "mail-archive", account["id"], {k: v for k, v in result.items() if k != "errors"} | {"errors": len(result["errors"]), "folder": account["folder"], "last_uid": last_uid})
            return result
        finally:
            try:
                connection.logout()
            except Exception:
                pass


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
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)
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
