"""Privacy-preserving GitHub issue reporting for application errors.

Only explicitly allow-listed technical metadata is sent. Request bodies,
query parameters, cookies, headers, user identities, exception messages,
environment variables, database content and log files are never uploaded.
"""
from __future__ import annotations

import json
import os
import re
import stat
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization|cookie|token|secret|password|passwd|api[_-]?key)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?i)\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{8,}\b"),
)


@dataclass(frozen=True)
class GitHubReporterConfig:
    enabled: bool
    repository: str
    token: str
    label: str = ""
    api_base: str = "https://api.github.com"


def _read_token() -> str:
    """Load the GitHub token without ever requiring it in repository files."""
    token_file = os.environ.get("SIMPLEOFFICE_GITHUB_ERROR_TOKEN_FILE", "").strip()
    if token_file:
        path = Path(token_file)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("GitHub token file must be a regular file")
        if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise RuntimeError("GitHub token file must not be accessible by group or others")
        return path.read_text(encoding="utf-8").strip()
    return os.environ.get("SIMPLEOFFICE_GITHUB_ERROR_TOKEN", "").strip()


def load_config() -> GitHubReporterConfig:
    """Load reporter configuration from environment or a protected secret file."""
    enabled = os.environ.get("SIMPLEOFFICE_GITHUB_ERROR_REPORTING", "0").strip().casefold() in {
        "1", "true", "yes", "on",
    }
    return GitHubReporterConfig(
        enabled=enabled,
        repository=os.environ.get("SIMPLEOFFICE_GITHUB_ERROR_REPOSITORY", "").strip(),
        token=_read_token(),
        label=os.environ.get("SIMPLEOFFICE_GITHUB_ERROR_LABEL", "").strip(),
        api_base=os.environ.get("SIMPLEOFFICE_GITHUB_API_BASE", "https://api.github.com").rstrip("/"),
    )


def sanitize_text(value: object, limit: int = 500) -> str:
    """Return a bounded, best-effort redacted string for local admin display."""
    text = str(value or "").replace("\x00", "")
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
            safe.append({
                "file": sanitize_text(frame.get("file"), 160),
                "line": int(frame.get("line") or 0),
                "function": sanitize_text(frame.get("function"), 160),
            })
    return safe[-12:]


def build_report(*, exception_type: str, exception_message: str, endpoint: str,
                 method: str, request_id: str, fingerprint: str,
                 frames: Sequence[Mapping[str, object]], app_version: str = "") -> tuple[str, str]:
    """Build an issue using a strict outbound allow-list."""
    del exception_message
    exception_type = sanitize_text(exception_type, 120) or "ApplicationError"
    endpoint = sanitize_text(endpoint, 160) or "unknown"
    fingerprint = sanitize_text(fingerprint, 128)
    title = f"[auto] {exception_type} in {endpoint}"[:240]
    payload = {
        "request_id": sanitize_text(request_id, 64),
        "fingerprint": fingerprint,
        "exception_type": exception_type,
        "endpoint": endpoint,
        "method": sanitize_text(method, 12),
        "app_version": sanitize_text(app_version, 120),
        "frames": safe_frames(frames),
    }
    body = (
        "Automatisch von SimpleOffice gemeldet. Es werden ausschließlich "
        "freigegebene technische Metadaten übertragen; keine Logs, Anhänge, "
        "Request-Daten oder Exception-Nachrichten.\n\n```json\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n```\n\n"
        + f"<!-- simpleoffice-error:{fingerprint} -->"
    )
    return title, body


def _validated_api_base(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("SIMPLEOFFICE_GITHUB_API_BASE must be an HTTPS URL without credentials")
    return value.rstrip("/")


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
    # nosec B310: _validated_api_base() enforces HTTPS and rejects URL credentials.
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def find_existing_issue(config: GitHubReporterConfig, fingerprint: str) -> int | None:
    if not fingerprint:
        return None
    marker = f"simpleoffice-error:{sanitize_text(fingerprint, 128)}"
    query = urllib.parse.quote(f'"{marker}" repo:{config.repository} is:issue is:open')
    result = _request_json(config, "GET", f"/search/issues?q={query}")
    if isinstance(result, dict):
        items = result.get("items") or []
        if items:
            return int(items[0]["number"])
    return None


def create_issue(config: GitHubReporterConfig, title: str, body: str) -> int:
    payload: dict[str, object] = {"title": title, "body": body}
    if config.label:
        payload["labels"] = [config.label]
    result = _request_json(config, "POST", f"/repos/{config.repository}/issues", payload)
    if not isinstance(result, dict) or "number" not in result:
        raise RuntimeError("GitHub returned no issue number")
    return int(result["number"])


def issue_url(config: GitHubReporterConfig, issue_number: int) -> str:
    return f"https://github.com/{config.repository}/issues/{int(issue_number)}"


def report_error(*, exception_type: str, exception_message: str, endpoint: str,
                 method: str, request_id: str, fingerprint: str,
                 frames: Sequence[Mapping[str, object]], app_version: str = "") -> int | None:
    config = load_config()
    if not config.enabled or not config.repository or not config.token:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.repository):
        raise ValueError("Invalid SIMPLEOFFICE_GITHUB_ERROR_REPOSITORY")
    existing = find_existing_issue(config, fingerprint)
    if existing is not None:
        return existing
    title, body = build_report(
        exception_type=exception_type,
        exception_message=exception_message,
        endpoint=endpoint,
        method=method,
        request_id=request_id,
        fingerprint=fingerprint,
        frames=frames,
        app_version=app_version,
    )
    return create_issue(config, title, body)
