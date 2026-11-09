"""Bounded, GET-only GitHub access for the public repository workspace.

This deliberately does not reuse the error reporter's write-capable credentials.
Neither hosts, repositories, HTTP methods nor URLs can be supplied by a visitor.
"""
from __future__ import annotations

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = "JensKapitza/SimpleOffice4Me"
REPOSITORY_URL = "https://github.com/" + REPOSITORY
API_BASE = "https://api.github.com/repos/" + REPOSITORY
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUESTS = 24
MAX_SECONDS = 45
_RESOURCE = re.compile(
    r"(?:|pulls|issues|actions/runs|branches/[A-Za-z0-9_.-]+|"
    r"pulls/[1-9][0-9]*/reviews|commits/[0-9a-f]{40}/(?:check-runs|status)|"
    r"code-scanning/alerts|dependabot/alerts)\Z"
)
_QUERY_KEYS = frozenset({"state", "sort", "direction", "per_page", "page", "filter", "branch"})


class WorkspaceError(Exception):
    """Only sanitized messages may cross this error boundary."""

    def __init__(self, message: str, *, retry_after: int = 0, status: int = 502):
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status


class WorkspaceUnauthorized(WorkspaceError):
    pass


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _integer(value, default: int = 0) -> int:
    try:
        return max(0, min(int(value), 2**53))
    except (TypeError, ValueError, OverflowError):
        return default


class GitHubReadClient:
    """One bounded collection, with no write or arbitrary-proxy interface."""

    def __init__(self, token: str = ""):
        if not isinstance(token, str) or len(token) > 4096 or any(c.isspace() for c in token):
            raise WorkspaceError("Ungültige GitHub-Sitzung.", status=401)
        self._token = token
        self._opener = build_opener(_NoRedirects())
        self._deadline = time.monotonic() + MAX_SECONDS
        self.requests = 0
        self.headers = {}
        self.retry_after = 0
        self.partial_message = ""

    def get(self, resource: str, query: dict | None = None) -> dict | list:
        if resource != "user" and not _RESOURCE.fullmatch(resource):
            raise WorkspaceError("Dieser GitHub-Lesezugriff ist nicht freigegeben.", status=400)
        if query and (set(query) - _QUERY_KEYS or any(
            not isinstance(v, (int, str)) or len(str(v)) > 128 for v in query.values()
        )):
            raise WorkspaceError("Ungültige GitHub-Abfrage.", status=400)
        if self.retry_after:
            raise WorkspaceError("GitHub-Abruflimit erreicht. Später erneut abrufen.",
                                 retry_after=self.retry_after, status=429)
        remaining = self._deadline - time.monotonic()
        if self.requests >= MAX_REQUESTS or remaining <= 0:
            raise WorkspaceError("Abrufumfang begrenzt; weitere Daten bleiben ungeprüft.", status=503)
        url = "https://api.github.com/user" if resource == "user" else API_BASE + ("/" + resource if resource else "")
        if query:
            url += "?" + urlencode(query)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "SimpleOffice4Me-ReadOnlyWorkspace"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        self.requests += 1
        try:
            with self._opener.open(Request(url, headers=headers, method="GET"),
                                   timeout=min(8, remaining)) as response:
                self.headers = {k.lower(): v for k, v in response.headers.items()}
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise WorkspaceError("GitHub-Antwort zu groß; Quelle wurde nicht übernommen.")
            data = json.loads(raw)
            if not isinstance(data, (dict, list)):
                raise WorkspaceError("Unerwartetes GitHub-Antwortformat.")
            if self.headers.get("x-ratelimit-remaining") == "0":
                self.retry_after = self._wait(self.headers)
            return data
        except HTTPError as exc:
            status = exc.code
            headers = {k.lower(): v for k, v in exc.headers.items()}
            rate_limited = False
            if status == 403:
                try:
                    error = json.loads(exc.read(8192))
                    rate_limited = isinstance(error, dict) and "rate limit" in str(error.get("message", "")).casefold()
                except (ValueError, UnicodeError, OSError, RecursionError):
                    rate_limited = False
            exc.close()
            if status == 401 and self._token:
                raise WorkspaceUnauthorized("GitHub-Anmeldung abgelaufen. Bitte erneut verbinden.", status=401) from None
            if status == 429 or status == 403 and (
                headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers or rate_limited
            ):
                self.retry_after = self._wait(headers)
                raise WorkspaceError("GitHub-Abruflimit erreicht. Später erneut abrufen.",
                                     retry_after=self.retry_after, status=429) from None
            message = {
                403: "Quelle nicht zugänglich: Berechtigung oder GitHub-Limit prüfen.",
                404: "Quelle nicht zugänglich oder nicht vorhanden; nicht als leer bewertet.",
            }.get(status, "GitHub-Quelle momentan nicht verfügbar.")
            raise WorkspaceError(message, status=status if status in (403, 404) else 502) from None
        except (URLError, TimeoutError, OSError):
            raise WorkspaceError("GitHub nicht erreichbar oder Zeitlimit überschritten.") from None
        except (ValueError, UnicodeError, RecursionError):
            raise WorkspaceError("GitHub-Antwort konnte nicht sicher gelesen werden.") from None

    @staticmethod
    def _wait(headers: dict) -> int:
        retry = _integer(headers.get("retry-after"), 60)
        reset = _integer(headers.get("x-ratelimit-reset")) - int(time.time())
        return max(60, min(max(retry, reset), 86400))

    def collection(self, resource: str, query: dict | None = None,
                   *, key: str | None = None, max_pages: int = 2) -> tuple[list, bool]:
        """Return records and completeness; never silently truncate a collection."""
        records = []
        self.partial_message = ""
        for page in range(1, max_pages + 1):
            try:
                payload = self.get(resource, {**(query or {}), "per_page": 100, "page": page})
            except WorkspaceUnauthorized:
                raise
            except WorkspaceError as exc:
                if not records:
                    raise
                self.partial_message = "Teilweise abgerufen. " + str(exc)
                return records, False
            items = payload.get(key) if key and isinstance(payload, dict) else payload
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise WorkspaceError("Unerwartetes GitHub-Listenformat.")
            records.extend(items)
            has_next = 'rel="next"' in self.headers.get("link", "")
            total = _integer(payload.get("total_count")) if isinstance(payload, dict) else 0
            if not has_next and len(records) >= total:
                return records, True
        return records, False
