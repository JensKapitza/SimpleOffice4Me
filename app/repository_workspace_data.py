"""Shared public cache and isolated encrypted account snapshots for read-only use."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag

from .db import get_db
from .repository_workspace_context import CHECKLIST, documentation_gaps
from .repository_workspace_github import (
    GitHubReadClient, REPOSITORY, REPOSITORY_URL, WorkspaceError, WorkspaceUnauthorized,
)
from .repository_workspace_model import normalise
from .security_controls import protect_value, unprotect_value

PUBLIC_TTL = 1800  # A shared anonymous GitHub IP budget is only 60 requests/hour.
ACCOUNT_TTL = 300
ACCOUNT_COOLDOWN = 60
MAX_CACHES = 200


def _iso(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if timestamp is None else timestamp, timezone.utc).isoformat()


def _cache_id(cache_key: str) -> str:
    return hashlib.sha256(cache_key.encode()).hexdigest()


def _database():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS repository_workspace_cache (
        cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL DEFAULT '',
        fetched_at REAL NOT NULL DEFAULT 0, refresh_after REAL NOT NULL DEFAULT 0,
        busy_until REAL NOT NULL DEFAULT 0, lease TEXT NOT NULL DEFAULT '',
        expires_at REAL NOT NULL DEFAULT 0)""")
    db.commit()
    return db


def invalidate_cache(cache_key: str) -> None:
    """Deleting the lease also prevents a late request from re-saving logged-out data."""
    db = _database()
    db.execute("DELETE FROM repository_workspace_cache WHERE cache_key=?", (_cache_id(cache_key),))
    db.commit()


def _decode(row, private: bool) -> dict | None:
    if not row or not row["payload"]:
        return None
    try:
        encoded = row["payload"]
        if private:
            if not encoded.startswith("enc:v1:"):
                return None
            encoded = unprotect_value(encoded, "repository-workspace-cache-" + row["cache_key"])
        value = json.loads(encoded)
        return value if isinstance(value, dict) else None
    except (ValueError, InvalidTag, TypeError, UnicodeError, RecursionError):
        return None


def _empty(private: bool) -> dict:
    return {"repository": {"full_name": REPOSITORY, "url": REPOSITORY_URL, "default_branch": "main"},
            "mode": "account" if private else "public", "fetched_at": None, "cached": False,
            "next_refresh_at": None, "items": [], "sources": [], "checklist": CHECKLIST,
            "documentation_gaps": documentation_gaps()}


class _Collector:
    def __init__(self, token: str):
        self.client = GitHubReadClient(token)
        self.sources = []

    def fetch(self, ident: str, label: str, resource: str, query: dict | None = None,
              *, collection: bool = True, key: str | None = None, max_pages: int = 2):
        try:
            if collection:
                value, complete = self.client.collection(resource, query, key=key, max_pages=max_pages)
            else:
                value, complete = self.client.get(resource, query), True
                if not isinstance(value, dict):
                    raise WorkspaceError("Unerwartetes GitHub-Objektformat.")
            self.sources.append({"id": ident, "label": label, "state": "ok" if complete else "partial",
                                 "message": "Abgerufen." if complete else "Seitenlimit erreicht; weitere Einträge ungeprüft.",
                                 "checked_at": _iso()})
            partial_message = getattr(self.client, "partial_message", "")
            if collection and not complete and isinstance(partial_message, str) and partial_message:
                self.sources[-1]["message"] = partial_message
            return value, complete
        except WorkspaceUnauthorized:
            raise
        except WorkspaceError as exc:
            self.sources.append({"id": ident, "label": label,
                                 "state": "unavailable" if exc.status in (403, 404) else "error",
                                 "message": str(exc), "checked_at": _iso()})
            return ([] if collection else {}), False


def _collect(token: str) -> tuple[dict, int]:
    collector = _Collector(token)
    fetch = collector.fetch
    repo, _ = fetch("repository", "Repository-Metadaten", "", collection=False)
    pulls, _ = fetch("pulls", "Offene Pull Requests", "pulls", {"state": "open", "sort": "updated", "direction": "desc"})
    issues, _ = fetch("issues", "Offene Issues", "issues", {"state": "open", "sort": "updated", "direction": "desc"})
    runs, _ = fetch("runs", "Jüngste CI-Läufe (maximal 100)", "actions/runs", key="workflow_runs", max_pages=1)
    closed, _ = fetch("closed", "Zuletzt geschlossene Einträge (maximal 100)", "issues",
                      {"state": "closed", "sort": "updated", "direction": "desc"}, max_pages=1)
    branch = repo.get("default_branch", "main")
    branch = branch if isinstance(branch, str) and len(branch) <= 100 else "main"
    head, _ = fetch("head", "Aktueller Hauptbranch-Commit", "branches/" + branch, collection=False)
    payload = {"pulls": pulls, "issues": issues, "runs": runs, "closed": closed,
               "main_sha": (head.get("commit") or {}).get("sha", ""), "details": {}}
    if token:
        for kind, label in (("code-scanning", "Code-Scanning-Funde"), ("dependabot", "Dependabot-Funde")):
            payload[kind], _ = fetch(kind, label, kind + "/alerts", {"state": "open"}, max_pages=1)
    else:
        for kind, label in (("code-scanning", "Code-Scanning-Funde"), ("dependabot", "Dependabot-Funde")):
            collector.sources.append({"id": kind, "label": label, "state": "unavailable", "checked_at": None,
                                      "message": "Im Public-Modus nicht geprüft. GitHub-Konto und passende App-Rechte erforderlich."})
    # Bound per-PR enrichment as well as total HTTP calls. Unenriched PRs stay unknown.
    for pull in pulls[:8]:
        number = pull.get("number")
        sha = (pull.get("head") or {}).get("sha", "")
        if not isinstance(number, int) or not isinstance(sha, str):
            continue
        reviews, reviews_ok = fetch(f"reviews-{number}", f"Reviews PR #{number}", f"pulls/{number}/reviews", max_pages=1)
        checks, checks_ok = fetch(f"checks-{number}", f"Checks PR #{number}", f"commits/{sha}/check-runs",
                                  {"filter": "latest"}, key="check_runs", max_pages=1)
        statuses, status_ok = fetch(f"status-{number}", f"Commit-Status PR #{number}", f"commits/{sha}/status",
                                    {"per_page": 100}, collection=False)
        # The combined-status endpoint can paginate; do not imply complete checks.
        status_list = statuses.get("statuses", [])
        status_valid = (status_ok and isinstance(status_list, list) and
                        all(isinstance(status, dict) for status in status_list) and statuses.get("sha") == sha)
        if status_ok and not status_valid:
            collector.sources[-1].update(state="error", message="Commit-Status hat ein ungültiges Format oder einen anderen SHA.")
        status_ok = status_valid
        if status_valid and statuses.get("total_count", 0) > len(status_list):
            status_ok = False
            collector.sources[-1].update(state="partial", message="Weitere Commit-Statusmeldungen ungeprüft.")
        payload["details"][number] = {"reviews": reviews, "checks": checks,
                                      "statuses": status_list if status_valid else [],
                                      "complete": reviews_ok and checks_ok and status_ok}
    if len(pulls) > 8:
        collector.sources.append({"id": "pr-limit", "label": "PR-Detailabdeckung", "state": "partial",
                                  "checked_at": _iso(), "message": "Details auf acht PRs begrenzt; weitere PRs ungeprüft."})
    collector.sources.extend([
        {"id": "merge-rules", "label": "Merge-Freigabe", "state": "unavailable", "checked_at": None,
         "message": "Branch-Regeln und offene Review-Threads nicht vollständig geprüft; keine automatische Merge-Bereitschaft."},
        {"id": "secret-scanning", "label": "Secret Scanning", "state": "unavailable", "checked_at": None,
         "message": "Nicht abgerufen; diese Ansicht liefert keine vollständige Security-Inventur."},
    ])
    result = _empty(bool(token))
    result.update(items=normalise(payload), sources=collector.sources, fetched_at=_iso())
    result["repository"]["default_branch"] = branch
    return result, collector.client.retry_after


def snapshot(token: str = "", *, refresh: bool = False, cache_key: str = "public") -> dict:
    private = bool(token)
    if private == (cache_key == "public"):
        raise WorkspaceError("Ungültiger Sitzungskontext.", status=400)
    ident, now = _cache_id(cache_key), time.time()
    ttl = ACCOUNT_TTL if private else PUBLIC_TTL
    cooldown = ACCOUNT_COOLDOWN if private else PUBLIC_TTL
    db = _database()
    db.execute("DELETE FROM repository_workspace_cache WHERE expires_at < ? AND busy_until < ?", (now, now))
    row = db.execute("SELECT * FROM repository_workspace_cache WHERE cache_key=?", (ident,)).fetchone()
    if row is None:
        count = db.execute("SELECT COUNT(*) FROM repository_workspace_cache").fetchone()[0]
        if count >= MAX_CACHES:
            db.commit()
            raise WorkspaceError("Zu viele aktive Abrufsitzungen. Später erneut versuchen.", status=503)
        db.execute("INSERT OR IGNORE INTO repository_workspace_cache(cache_key, expires_at) VALUES (?, ?)", (ident, now + 3600))
        row = db.execute("SELECT * FROM repository_workspace_cache WHERE cache_key=?", (ident,)).fetchone()
    cached = _decode(row, private)
    if cached and (row["refresh_after"] > now or (not refresh and row["fetched_at"] + ttl > now)):
        db.commit()
        cached.update(cached=True, next_refresh_at=_iso(row["refresh_after"]))
        return cached
    lease = secrets.token_hex(16)
    claimed = db.execute("""UPDATE repository_workspace_cache SET busy_until=?, lease=?
                            WHERE cache_key=? AND busy_until<=? AND refresh_after<=?""",
                         (now + 90, lease, ident, now, now)).rowcount
    db.commit()
    if not claimed:
        result = cached or _empty(private)
        result.update(cached=bool(cached), next_refresh_at=_iso(max(now + 5, row["busy_until"], row["refresh_after"])))
        result["sources"].append({"id": "refresh-pending", "label": "Datenabruf", "state": "partial",
                                  "checked_at": _iso(), "message": "Abruf läuft bereits oder Wartezeit aktiv; später aktualisieren."})
        return result
    try:
        result, retry_after = _collect(token)
        next_refresh = time.time() + max(cooldown, retry_after)
        result["next_refresh_at"] = _iso(next_refresh)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if private:
            encoded = protect_value(encoded, "repository-workspace-cache-" + ident)
        # UPDATE-only: disconnect deletes the row/lease; late requests cannot resurrect it.
        db.execute("""UPDATE repository_workspace_cache SET payload=?, fetched_at=?, refresh_after=?,
                      busy_until=0, lease='', expires_at=? WHERE cache_key=? AND lease=?""",
                   (encoded, time.time(), next_refresh, time.time() + max(3600, retry_after + 60), ident, lease))
        db.commit()
        return result
    finally:
        db.execute("UPDATE repository_workspace_cache SET busy_until=0, lease='' WHERE cache_key=? AND lease=?", (ident, lease))
        db.commit()
