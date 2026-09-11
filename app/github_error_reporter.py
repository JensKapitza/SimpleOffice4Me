"""Privacy-preserving GitHub issue reporting for application errors.

Only explicitly allow-listed technical metadata is sent. Request bodies,
query parameters, cookies, headers, user identities, exception messages,
environment variables, database content and log files are never uploaded.

A normal SimpleOffice installation can send the allow-listed report to a
central SimpleOffice error relay. Only the relay needs GitHub credentials.
"""
from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


DEFAULT_REPOSITORY = "JensKapitza/SimpleOffice4Me"
REPORT_SCHEMA = 1
MAX_TOKEN_BYTES = 4096
MAX_HTTP_RESPONSE_BYTES = 256 * 1024
LOCAL_REPORT_WINDOW_SECONDS = 60
LOCAL_REPORT_LIMIT = 30
LOCAL_REPORT_INFLIGHT_LIMIT = 2
LOCAL_REPORT_CACHE_LIMIT = 1000
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization|cookie|token|secret|password|passwd|api[_-]?key)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?i)\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{8,}\b"),
)
_local_report_lock = threading.Lock()
_local_report_attempts: deque[float] = deque()
_local_report_inflight: set[str] = set()
_local_issue_cache: OrderedDict[str, int] = OrderedDict()


@dataclass(frozen=True)
class GitHubReporterConfig:
    enabled: bool
    repository: str
    token: str
    label: str = ""
    api_base: str = "https://api.github.com"
    relay_url: str = ""


def _validated_token(value: str) -> str:
    token = value.strip()
    if not token:
        return ""
    if len(token.encode("utf-8")) > MAX_TOKEN_BYTES or any(character.isspace() for character in token):
        raise RuntimeError("GitHub token has an invalid format")
    return token


def _read_token() -> str:
    """Load a bounded token from an ordinary, non-symlink secret file or env."""
    token_file = os.environ.get("SIMPLEOFFICE_GITHUB_ERROR_TOKEN_FILE", "").strip()
    if token_file:
        path = Path(token_file).expanduser()
        if path.is_symlink():
            raise RuntimeError("GitHub token file must not be a symlink")
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise RuntimeError("GitHub token file cannot be opened safely") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("GitHub token file must be a regular file")
            if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise RuntimeError("GitHub token file must not be accessible by group or others")
            if info.st_size > MAX_TOKEN_BYTES:
                raise RuntimeError("GitHub token file is too large")
            stream = os.fdopen(descriptor, "r", encoding="utf-8")
            descriptor = -1
            with stream:
                token = stream.read(MAX_TOKEN_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return _validated_token(token)
    return _validated_token(os.environ.get("SIMPLEOFFICE_GITHUB_ERROR_TOKEN", ""))


def _enabled(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _validated_repository(value: str) -> str:
    repository = value.strip()
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("Invalid SIMPLEOFFICE_GITHUB_ERROR_REPOSITORY")
    return repository


def load_config() -> GitHubReporterConfig:
    """Load reporter configuration from runtime settings and protected secrets."""
    relay_url = os.environ.get("SIMPLEOFFICE_ERROR_REPORT_URL", "").strip()
    direct_enabled = _enabled(os.environ.get("SIMPLEOFFICE_GITHUB_ERROR_REPORTING", "0"))
    enabled = direct_enabled or bool(relay_url)
    token = _read_token() if direct_enabled else ""
    repository = os.environ.get(
        "SIMPLEOFFICE_GITHUB_ERROR_REPOSITORY", DEFAULT_REPOSITORY
    ).strip() or DEFAULT_REPOSITORY
    return GitHubReporterConfig(
        enabled=enabled,
        repository=_validated_repository(repository),
        token=token,
        label=os.environ.get("SIMPLEOFFICE_GITHUB_ERROR_LABEL", "").strip(),
        # GitHub.com is intentional here. Allowing an arbitrary host while attaching
        # a bearer token would turn a modified environment into a token-exfiltration path.
        api_base="https://api.github.com",
        relay_url=relay_url,
    )


def sanitize_text(value: object, limit: int = 500) -> str:
    """Return a bounded, best-effort redacted string for local admin display."""
    # Bound regex work before redaction as exception text can itself be attacker-controlled.
    scan_limit = max(256, min(max(0, limit) * 4, 8192))
    text = str(value or "")[:scan_limit].replace("\x00", "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = re.sub(r"(?:[A-Za-z]:\\|/)(?:[^\s:/\\]+[/\\])+([^\s:/\\]+)", r"<path>/\1", text)
    return text[:limit]


def safe_frames(frames: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    """Keep only non-sensitive stack coordinates."""
    safe: list[dict[str, object]] = []
    for frame in frames:
        if "template" in frame:
            safe.append({
                "template": sanitize_text(frame.get("template"), 240),
                "line": int(frame.get("line") or 0),
                "variable": sanitize_text(frame.get("variable"), 120),
            })
        else:
            filename = Path(str(frame.get("file") or "")).name
            safe.append({
                "file": sanitize_text(filename, 160),
                "line": int(frame.get("line") or 0),
                "function": sanitize_text(frame.get("function"), 160),
            })
    return safe[-12:]


def build_report_payload(*, exception_type: str, exception_message: str, endpoint: str,
                         method: str, request_id: str, fingerprint: str,
                         frames: Sequence[Mapping[str, object]], app_version: str = "") -> dict[str, object]:
    """Build the strict transport allow-list. Arbitrary exception text is dropped."""
    del exception_message
    return {
        "schema": REPORT_SCHEMA,
        "request_id": sanitize_text(request_id, 64),
        "fingerprint": sanitize_text(fingerprint, 128),
        "exception_type": sanitize_text(exception_type, 120) or "ApplicationError",
        "endpoint": sanitize_text(endpoint, 160) or "unknown",
        "method": sanitize_text(method, 12),
        "app_version": sanitize_text(app_version, 120),
        "frames": safe_frames(frames),
    }


def build_report_from_payload(payload: Mapping[str, object]) -> tuple[str, str]:
    """Render an allow-listed payload as an automatic GitHub issue."""
    exception_type = sanitize_text(payload.get("exception_type"), 120) or "ApplicationError"
    endpoint = sanitize_text(payload.get("endpoint"), 160) or "unknown"
    fingerprint = sanitize_text(payload.get("fingerprint"), 128)
    safe_payload = {
        "request_id": sanitize_text(payload.get("request_id"), 64),
        "fingerprint": fingerprint,
        "exception_type": exception_type,
        "endpoint": endpoint,
        "method": sanitize_text(payload.get("method"), 12),
        "app_version": sanitize_text(payload.get("app_version"), 120),
        "frames": safe_frames(payload.get("frames") or []),
    }
    title = f"[auto] {exception_type} in {endpoint}"[:240]
    body = (
        "Automatisch von SimpleOffice gemeldet. Es werden ausschließlich "
        "freigegebene technische Metadaten übertragen; keine Logs, Anhänge, "
        "Request-Daten oder Exception-Nachrichten.\n\n```json\n"
        + json.dumps(safe_payload, ensure_ascii=False, indent=2)
        + "\n```\n\n"
        + f"<!-- simpleoffice-error:{fingerprint} -->"
    )
    return title, body


def build_report(*, exception_type: str, exception_message: str, endpoint: str,
                 method: str, request_id: str, fingerprint: str,
                 frames: Sequence[Mapping[str, object]], app_version: str = "") -> tuple[str, str]:
    """Build an issue using the same strict outbound allow-list as the relay."""
    payload = build_report_payload(
        exception_type=exception_type,
        exception_message=exception_message,
        endpoint=endpoint,
        method=method,
        request_id=request_id,
        fingerprint=fingerprint,
        frames=frames,
        app_version=app_version,
    )
    return build_report_from_payload(payload)


def manual_issue_url(
    request_id: str,
    repository: str = DEFAULT_REPOSITORY,
    *,
    exception_type: str = "ApplicationError",
    endpoint: str = "unknown",
    method: str = "",
    fingerprint: str = "",
    frames: Sequence[Mapping[str, object]] = (),
    app_version: str = "",
) -> str:
    """Build a useful, privacy-safe GitHub App/browser issue handoff URL."""
    repository = _validated_repository(repository)
    payload = build_report_payload(
        exception_type=exception_type,
        exception_message="",
        endpoint=endpoint,
        method=method,
        request_id=request_id,
        fingerprint=fingerprint,
        # Keep browser/app handoff URLs compact while retaining the nearest frames.
        frames=safe_frames(frames)[-6:],
        app_version=app_version,
    )
    title = f"[manual] {payload['exception_type']} in {payload['endpoint']}"[:240]
    body = (
        "Manuell aus SimpleOffice vorbereitet. Enthalten sind nur freigegebene "
        "technische Metadaten; keine Logs, Kundendaten, Request-Daten oder "
        "Exception-Nachrichten.\n\n```json\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n```\n\n"
        "Bitte beschreibe optional kurz, was unmittelbar vor dem Fehler gemacht wurde. "
        "Keine Passwörter, Tokens, Kundendaten oder Dokumentinhalte einfügen."
    )
    if payload["fingerprint"]:
        body += f"\n\n<!-- simpleoffice-error:{payload['fingerprint']} -->"
    query = urllib.parse.urlencode({"title": title, "body": body})
    return f"https://github.com/{repository}/issues/new?{query}"


def _validated_api_base(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("GitHub API must use https://api.github.com") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.github.com"
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GitHub API must use https://api.github.com")
    return "https://api.github.com"


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward report traffic or bearer credentials across redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        raise RuntimeError("HTTP redirects are not allowed for error reporting")


def _open_json(request: urllib.request.Request) -> object:
    opener = urllib.request.build_opener(_RejectRedirects())
    with opener.open(request, timeout=3) as response:
        raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
    if len(raw) > MAX_HTTP_RESPONSE_BYTES:
        raise RuntimeError("Error-report HTTP response exceeds size limit")
    return json.loads(raw.decode("utf-8"))


def _request_json(config: GitHubReporterConfig, method: str, path: str, payload: dict | None = None) -> object:
    api_base = _validated_api_base(config.api_base)
    if not path.startswith("/"):
        raise ValueError("GitHub API path must be absolute")
    url = f"{api_base}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {config.token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "SimpleOffice4Me-error-reporter")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    return _open_json(request)


def _validated_relay_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "SIMPLEOFFICE_ERROR_REPORT_URL must be an HTTPS URL without credentials or fragment"
        ) from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or port is not None and not 1 <= port <= 65535
    ):
        raise ValueError("SIMPLEOFFICE_ERROR_REPORT_URL must be an HTTPS URL without credentials or fragment")
    return value


def _post_relay(relay_url: str, payload: Mapping[str, object]) -> object:
    """Send only the allow-listed report to the central SimpleOffice relay."""
    url = _validated_relay_url(relay_url)
    data = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
    if len(data) > 16 * 1024:
        raise ValueError("Error report exceeds relay size limit")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "SimpleOffice4Me-error-reporter")
    return _open_json(request)


def find_existing_issue(config: GitHubReporterConfig, fingerprint: str) -> int | None:
    if not fingerprint:
        return None
    repository = _validated_repository(config.repository)
    marker = f"simpleoffice-error:{sanitize_text(fingerprint, 128)}"
    query = urllib.parse.quote(f'"{marker}" repo:{repository} is:issue is:open')
    result = _request_json(config, "GET", f"/search/issues?q={query}&per_page=1")
    if isinstance(result, dict):
        items = result.get("items") or []
        if items:
            return int(items[0]["number"])
    return None


def create_issue(config: GitHubReporterConfig, title: str, body: str) -> int:
    repository = _validated_repository(config.repository)
    payload: dict[str, object] = {"title": title, "body": body}
    if config.label:
        payload["labels"] = [config.label]
    result = _request_json(config, "POST", f"/repos/{repository}/issues", payload)
    if not isinstance(result, dict) or "number" not in result:
        raise RuntimeError("GitHub returned no issue number")
    return int(result["number"])


def issue_url(config: GitHubReporterConfig, issue_number: int) -> str:
    repository = _validated_repository(config.repository)
    return f"https://github.com/{repository}/issues/{int(issue_number)}"


def report_payload_to_github(config: GitHubReporterConfig, payload: Mapping[str, object]) -> int:
    """Create/deduplicate a GitHub issue. Intended for direct mode and the central relay."""
    if not config.token:
        raise RuntimeError("GitHub error reporter token is not configured")
    _validated_repository(config.repository)
    fingerprint = sanitize_text(payload.get("fingerprint"), 128)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", fingerprint):
        raise ValueError("Invalid error fingerprint")
    existing = find_existing_issue(config, fingerprint)
    if existing is not None:
        return existing
    title, body = build_report_from_payload(payload)
    return create_issue(config, title, body)


def _reserve_local_report(fingerprint: str) -> tuple[str, int | None]:
    """Bound synchronous outbound work caused by application error storms."""
    now = time.monotonic()
    with _local_report_lock:
        cached = _local_issue_cache.get(fingerprint)
        if cached is not None:
            _local_issue_cache.move_to_end(fingerprint)
            return "cached", cached
        if fingerprint in _local_report_inflight:
            return "duplicate", None
        if len(_local_report_inflight) >= LOCAL_REPORT_INFLIGHT_LIMIT:
            return "busy", None
        cutoff = now - LOCAL_REPORT_WINDOW_SECONDS
        while _local_report_attempts and _local_report_attempts[0] <= cutoff:
            _local_report_attempts.popleft()
        if len(_local_report_attempts) >= LOCAL_REPORT_LIMIT:
            return "limited", None
        _local_report_attempts.append(now)
        _local_report_inflight.add(fingerprint)
        return "reserved", None


def _release_local_report(fingerprint: str) -> None:
    with _local_report_lock:
        _local_report_inflight.discard(fingerprint)


def _remember_local_issue(fingerprint: str, issue_number: int) -> None:
    with _local_report_lock:
        _local_issue_cache[fingerprint] = issue_number
        _local_issue_cache.move_to_end(fingerprint)
        while len(_local_issue_cache) > LOCAL_REPORT_CACHE_LIMIT:
            _local_issue_cache.popitem(last=False)


def report_error(*, exception_type: str, exception_message: str, endpoint: str,
                 method: str, request_id: str, fingerprint: str,
                 frames: Sequence[Mapping[str, object]], app_version: str = "") -> int | None:
    config = load_config()
    if not config.enabled:
        return None
    payload = build_report_payload(
        exception_type=exception_type,
        exception_message=exception_message,
        endpoint=endpoint,
        method=method,
        request_id=request_id,
        fingerprint=fingerprint,
        frames=frames,
        app_version=app_version,
    )
    safe_fingerprint = sanitize_text(payload.get("fingerprint"), 128)
    reservation, cached_issue = _reserve_local_report(safe_fingerprint)
    if reservation == "cached":
        return cached_issue
    if reservation != "reserved":
        return None
    try:
        issue_number: int | None = None
        if config.relay_url:
            result = _post_relay(config.relay_url, payload)
            if isinstance(result, dict):
                candidate = result.get("issue_number")
                if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
                    issue_number = candidate
        elif config.token:
            issue_number = report_payload_to_github(config, payload)
        if issue_number is not None and issue_number > 0:
            _remember_local_issue(safe_fingerprint, issue_number)
        return issue_number
    finally:
        _release_local_report(safe_fingerprint)
