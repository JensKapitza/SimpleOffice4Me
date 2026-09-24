"""Stable V2 service layer for password-vault search and mail references.

The service deliberately performs credential search in memory after vault
unlock. It creates no plaintext search index and never includes password, TOTP
or note contents in searchable fields.

Mail integration is reference-only: the mail index remains the source of truth
and only whitelisted message locator/header fields are returned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit

from ..password_vault import PasswordVault


MAX_QUERY_CHARS = 2_000
MAX_RESULTS = 1_000
MAX_TAGS = 64
MAX_URLS = 64
MAX_MAIL_TERMS = 16
MAX_MAIL_REFERENCES = 500


class MailAccountPort(Protocol):
    def accounts(self, actor: str) -> list[dict[str, Any]]:
        ...


class MailSearchPort(Protocol):
    def search(
        self,
        actor: str,
        account_id: str,
        query: str,
        *,
        include_missing: bool = True,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class VaultSearchFilters:
    text: str = ""
    username: str = ""
    email: str = ""
    domain: str = ""
    tags: tuple[str, ...] = ()
    folder: str = ""
    favorites_only: bool = False
    limit: int = 250


def _bounded_text(value: Any, label: str, maximum: int = MAX_QUERY_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise ValueError(f"{label} is too long")
    return text


def _normalize_email(value: Any) -> str:
    text = _bounded_text(value, "email", 320).casefold()
    if not text:
        return ""
    if text.count("@") != 1:
        raise ValueError("invalid email address")
    local, domain = text.rsplit("@", 1)
    if not local or not domain:
        raise ValueError("invalid email address")
    normalized_domain = _normalize_domain(domain)
    return f"{local}@{normalized_domain}"


def _normalize_domain(value: Any) -> str:
    text = _bounded_text(value, "domain", 253).casefold().rstrip(".")
    if not text:
        return ""
    if "://" in text:
        parsed = urlsplit(text)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("invalid credential domain")
        text = parsed.hostname.casefold().rstrip(".")
    if "/" in text or "\\" in text or "@" in text or not text:
        raise ValueError("invalid credential domain")
    try:
        ascii_domain = text.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ValueError("invalid credential domain") from exc
    if len(ascii_domain) > 253 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        for label in ascii_domain.split(".")
    ):
        raise ValueError("invalid credential domain")
    return ascii_domain


def _safe_service_url(value: Any) -> str:
    """Return a display/search URL without userinfo, query or fragment."""

    text = _bounded_text(value, "credential URL", 10_000)
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return ""
        domain = _normalize_domain(parsed.hostname)
        port = parsed.port
    except (ValueError, UnicodeError):
        return ""
    host = domain
    if port is not None:
        default = (parsed.scheme == "http" and port == 80) or (
            parsed.scheme == "https" and port == 443
        )
        if not default:
            host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "", "", ""))


def _domain_from_url(value: Any) -> str:
    text = _bounded_text(value, "credential URL", 10_000)
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    try:
        return _normalize_domain(parsed.hostname)
    except ValueError:
        return ""


def _tags(data: dict[str, Any]) -> tuple[str, ...]:
    raw = data.get("tags")
    values: Sequence[Any]
    if isinstance(raw, str):
        values = [part for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = []
    result: list[str] = []
    for value in values[:MAX_TAGS]:
        text = _bounded_text(value, "tag", 200).casefold()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _urls(data: dict[str, Any]) -> tuple[str, ...]:
    candidates: list[Any] = []
    if data.get("url"):
        candidates.append(data.get("url"))
    raw = data.get("urls")
    if isinstance(raw, (list, tuple)):
        for item in raw[:MAX_URLS]:
            if isinstance(item, dict):
                candidates.append(item.get("url") or item.get("value"))
            else:
                candidates.append(item)
    result: list[str] = []
    for value in candidates:
        text = _safe_service_url(value)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _domains(data: dict[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for value in _urls(data):
        domain = _domain_from_url(value)
        if domain and domain not in result:
            result.append(domain)
    return tuple(result)


def _emails(data: dict[str, Any]) -> tuple[str, ...]:
    candidates = [data.get("email")]
    username = str(data.get("username") or "").strip()
    if "@" in username:
        candidates.append(username)
    raw = data.get("emails")
    if isinstance(raw, (list, tuple)):
        candidates.extend(raw[:MAX_TAGS])
    result: list[str] = []
    for value in candidates:
        if not value:
            continue
        try:
            email = _normalize_email(value)
        except ValueError:
            continue
        if email and email not in result:
            result.append(email)
    return tuple(result)


def _searchable_values(data: dict[str, Any]) -> tuple[str, ...]:
    """Return non-secret fields eligible for in-memory credential search."""

    values: list[str] = []
    for key in ("name", "title", "username", "email", "folder"):
        text = _bounded_text(data.get(key), key, 10_000).casefold()
        if text:
            values.append(text)
    values.extend(value.casefold() for value in _urls(data))
    values.extend(_domains(data))
    values.extend(_emails(data))
    values.extend(_tags(data))
    return tuple(dict.fromkeys(values))


def _domain_matches(candidate: str, requested: str) -> bool:
    return candidate == requested or candidate.endswith("." + requested)


def _favorite(data: dict[str, Any]) -> bool:
    value = data.get("favorite")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _matches(data: dict[str, Any], filters: VaultSearchFilters) -> bool:
    username = _bounded_text(filters.username, "username", 320).casefold()
    email = _normalize_email(filters.email) if filters.email else ""
    domain = _normalize_domain(filters.domain) if filters.domain else ""
    folder = _bounded_text(filters.folder, "folder", 500).casefold()
    text = _bounded_text(filters.text, "search text").casefold()
    requested_tags = tuple(
        dict.fromkeys(
            _bounded_text(tag, "tag", 200).casefold()
            for tag in filters.tags[:MAX_TAGS]
            if str(tag or "").strip()
        )
    )

    if username and str(data.get("username") or "").strip().casefold() != username:
        return False
    if email and email not in _emails(data):
        return False
    if domain and not any(_domain_matches(value, domain) for value in _domains(data)):
        return False
    if folder and str(data.get("folder") or "").strip().casefold() != folder:
        return False
    tags = _tags(data)
    if requested_tags and any(tag not in tags for tag in requested_tags):
        return False
    if filters.favorites_only and not _favorite(data):
        return False
    if text and not any(text in value for value in _searchable_values(data)):
        return False
    return True


def _service_summary(wrapper: dict[str, Any]) -> dict[str, Any]:
    data = wrapper.get("data")
    if not isinstance(data, dict):
        data = {}
    return {
        "entry_id": str(wrapper.get("entry_id") or ""),
        "revision": int(wrapper.get("revision") or 0),
        "updated_at": wrapper.get("updated_at"),
        "type": str(data.get("type") or "login"),
        "name": str(data.get("name") or data.get("title") or ""),
        "username": str(data.get("username") or ""),
        "emails": list(_emails(data)),
        "domains": list(_domains(data)),
        "urls": list(_urls(data)),
        "tags": list(_tags(data)),
        "folder": str(data.get("folder") or ""),
        "favorite": _favorite(data),
    }


def _mail_reference(row: dict[str, Any]) -> dict[str, Any]:
    """Whitelist reference fields; never return indexed message body/search text."""

    return {
        "id": int(row.get("id") or 0),
        "account_id": str(row.get("account_id") or ""),
        "folder": str(row.get("folder") or ""),
        "uidvalidity": str(row.get("uidvalidity") or ""),
        "uid": str(row.get("uid") or ""),
        "source_kind": str(row.get("source_kind") or ""),
        "source_peer": str(row.get("source_peer") or ""),
        "resource_uri": str(row.get("resource_uri") or ""),
        "message_id": str(row.get("message_id") or ""),
        "subject": str(row.get("subject") or ""),
        "sender": str(row.get("sender") or ""),
        "recipients": str(row.get("recipients") or ""),
        "message_date": str(row.get("message_date") or ""),
        "present": bool(int(row.get("present") or 0)),
        "presence_status": str(row.get("presence_status") or ""),
        "last_seen_at": str(row.get("last_seen_at") or ""),
    }


class VaultService:
    """Unlocked-vault service boundary intended for UI/API consumers."""

    def __init__(
        self,
        vault: PasswordVault,
        *,
        mail_accounts: MailAccountPort | None = None,
        mail_search: MailSearchPort | None = None,
    ):
        self.vault = vault
        self.mail_accounts = mail_accounts
        self.mail_search = mail_search

    def _matching_entries(
        self,
        user_id: str,
        vault_key: bytes,
        filters: VaultSearchFilters | None = None,
    ) -> list[dict[str, Any]]:
        query = filters or VaultSearchFilters()
        if isinstance(query.limit, bool):
            raise ValueError("search limit must be an integer")
        limit = max(1, min(int(query.limit), MAX_RESULTS))
        rows = self.vault.entries(str(user_id), vault_key)
        result = [
            row
            for row in rows
            if isinstance(row.get("data"), dict) and _matches(row["data"], query)
        ]
        return result[:limit]

    def search(
        self,
        user_id: str,
        vault_key: bytes,
        filters: VaultSearchFilters | None = None,
    ) -> list[dict[str, Any]]:
        """Return a non-secret search projection.

        Search/list endpoints must not become a bulk credential export. Full
        decrypted entry data stays behind an explicit per-entry read.
        """

        return [
            _service_summary(row)
            for row in self._matching_entries(user_id, vault_key, filters)
        ]

    def summaries(
        self,
        user_id: str,
        vault_key: bytes,
        filters: VaultSearchFilters | None = None,
    ) -> list[dict[str, Any]]:
        return self.search(user_id, vault_key, filters)

    def credential(
        self,
        user_id: str,
        vault_key: bytes,
        entry_id: str,
    ) -> dict[str, Any]:
        """Return one explicitly requested decrypted credential entry."""

        requested = str(entry_id or "").strip()
        if not requested:
            raise ValueError("vault credential id is required")
        for row in self.vault.entries(str(user_id), vault_key):
            if str(row.get("entry_id") or "") == requested:
                return row
        raise ValueError("vault credential does not exist")

    def identity_usage(
        self,
        user_id: str,
        vault_key: bytes,
        *,
        username: str = "",
        email: str = "",
    ) -> list[dict[str, Any]]:
        if not username and not email:
            raise ValueError("username or email is required")
        filters = VaultSearchFilters(username=username, email=email, limit=MAX_RESULTS)
        return self.search(user_id, vault_key, filters)

    def credential_mail_references(
        self,
        user_id: str,
        vault_key: bytes,
        entry_id: str,
        *,
        include_missing: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if self.mail_accounts is None or self.mail_search is None:
            return []
        if isinstance(limit, bool):
            raise ValueError("mail reference limit must be an integer")
        requested_limit = max(1, min(int(limit), MAX_MAIL_REFERENCES))
        entry = self.credential(str(user_id), vault_key, str(entry_id))
        if not isinstance(entry.get("data"), dict):
            raise ValueError("vault credential does not exist")
        data = entry["data"]
        terms = list(_emails(data))
        terms.extend(domain for domain in _domains(data) if domain not in terms)
        terms = terms[:MAX_MAIL_TERMS]
        if not terms:
            return []

        result: dict[tuple[str, int], dict[str, Any]] = {}
        for account in self.mail_accounts.accounts(str(user_id)):
            account_id = str(account.get("id") or "").strip()
            if not account_id:
                continue
            per_term_limit = max(1, min(requested_limit, 250))
            for term in terms:
                rows = self.mail_search.search(
                    str(user_id),
                    account_id,
                    term,
                    include_missing=bool(include_missing),
                    limit=per_term_limit,
                )
                for row in rows:
                    row_id = int(row.get("id") or 0)
                    if row_id <= 0:
                        continue
                    key = (account_id, row_id)
                    if key not in result:
                        reference = _mail_reference(row)
                        reference["matched_by"] = [term]
                        result[key] = reference
                    elif term not in result[key]["matched_by"]:
                        result[key]["matched_by"].append(term)
                    if len(result) >= requested_limit:
                        break
                if len(result) >= requested_limit:
                    break
            if len(result) >= requested_limit:
                break

        rows = list(result.values())
        rows.sort(
            key=lambda value: (
                bool(value["present"]),
                str(value["last_seen_at"]),
                int(value["id"]),
            ),
            reverse=True,
        )
        return rows[:requested_limit]
