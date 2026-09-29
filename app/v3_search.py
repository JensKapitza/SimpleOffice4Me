"""Provider-based global search and safe navigation commands."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class SearchContext:
    root: Path
    actor: str
    is_admin: bool
    features: frozenset[str]


@dataclass(frozen=True)
class SearchHit:
    provider: str
    kind: str
    ref_type: str
    ref_id: str
    title: str
    subtitle: str
    url: str
    score: int = 0


Provider = Callable[[str, SearchContext, int], Iterable[SearchHit]]


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    feature: str
    search: Provider


class SearchRegistry:
    def __init__(self):
        self._providers: dict[str, ProviderDefinition] = {}

    def register(self, name: str, provider: Provider, *, feature: str = "") -> None:
        name = str(name).strip()
        if not name or name in self._providers:
            raise ValueError("provider name must be unique and non-empty")
        self._providers[name] = ProviderDefinition(name, str(feature).strip(), provider)

    def search(
        self,
        query: str,
        context: SearchContext,
        *,
        limit: int = 30,
        timeout_seconds: float = 0.45,
    ) -> tuple[list[SearchHit], list[str]]:
        query = " ".join(str(query).split())[:200]
        if not query:
            return [], []
        size = max(1, min(100, int(limit)))
        selected = [
            provider for provider in self._providers.values()
            if not provider.feature or provider.feature in context.features
        ]
        if not selected:
            return [], []

        executor = ThreadPoolExecutor(
            max_workers=min(6, len(selected)),
            thread_name_prefix="v3-search",
        )
        futures = {
            executor.submit(provider.search, query, context, size): provider.name
            for provider in selected
        }
        done, pending = wait(
            futures,
            timeout=max(0.05, min(2.0, float(timeout_seconds))),
        )
        hits: list[SearchHit] = []
        errors: list[str] = []
        for future in done:
            provider_name = futures[future]
            try:
                rows = list(future.result())[:size]
            except Exception:
                errors.append(provider_name)
                continue
            hits.extend(row for row in rows if isinstance(row, SearchHit))
        for future in pending:
            errors.append(futures[future])
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        hits.sort(
            key=lambda row: (
                -row.score,
                row.kind.casefold(),
                row.title.casefold(),
                row.ref_id,
            )
        )
        return hits[:size], sorted(set(errors))


def contact_provider(query: str, context: SearchContext, limit: int):
    from .contact_store import ContactStore

    rows = ContactStore(context.root).search(query, context.actor)[:limit]
    for row in rows:
        fields = row.get("fields", {})
        title = str(fields.get("display_name") or row.get("contact_id") or "Kontakt")
        detail = " · ".join(
            value
            for value in (
                str(fields.get("company") or ""),
                str(fields.get("email") or ""),
                str(fields.get("phone") or ""),
            )
            if value
        )
        contact_id = str(row["contact_id"])
        yield SearchHit(
            "contacts",
            "Kontakt",
            "contact",
            contact_id,
            title,
            detail,
            f"/documents/contacts/{contact_id}",
            100,
        )


def project_provider(query: str, context: SearchContext, limit: int):
    from .project_store import ProjectStore

    needle = query.casefold()
    count = 0
    for row in ProjectStore(context.root).projects():
        haystack = " ".join(
            str(row.get(key, ""))
            for key in ("title", "description", "location", "status")
        ).casefold()
        if needle not in haystack:
            continue
        project_id = str(row["project_id"])
        yield SearchHit(
            "projects",
            "Projekt",
            "project",
            project_id,
            str(row.get("title") or "Projekt"),
            str(row.get("location") or row.get("status") or ""),
            f"/documents/projects/{project_id}",
            80,
        )
        count += 1
        if count >= limit:
            break


def document_provider(query: str, context: SearchContext, limit: int):
    from .chat_access import document_visible
    from .document_store import DocumentStore

    store = DocumentStore(context.root)
    literal = json.dumps(query + "*", ensure_ascii=False)
    rows = store.search(
        f"name: {literal} ODER tag: {literal}",
        limit=max(limit * 3, 25),
    )
    count = 0
    for row in rows:
        try:
            metadata = store.get_document(str(row.get("document_id", "")))
        except (OSError, ValueError):
            continue
        if not document_visible(metadata, context.actor, context.is_admin):
            continue
        document_id = str(metadata["document_id"])
        path = str(row.get("path") or metadata.get("last_path") or "")
        yield SearchHit(
            "documents",
            "Dokument",
            "document",
            document_id,
            Path(path).name or document_id,
            path,
            f"/documents/{document_id}",
            90,
        )
        count += 1
        if count >= limit:
            break


def navigation_provider(query: str, context: SearchContext, limit: int):
    commands = (
        ("Kontakt anlegen", "Kontakte öffnen", "contacts", "/documents/contacts"),
        ("Dokument importieren", "Dokument-Inbox öffnen", "documents", "/documents/inbox"),
        ("Neue Aufgabe", "Aufgaben öffnen", "projects", "/tasks"),
        ("Neues Projekt", "Projekte öffnen", "projects", "/documents/projects"),
    )
    needle = query.casefold()
    count = 0
    for title, subtitle, feature, url in commands:
        if feature not in context.features:
            continue
        if needle not in (title + " " + subtitle).casefold():
            continue
        yield SearchHit(
            "commands",
            "Befehl",
            "command",
            url,
            title,
            subtitle,
            url,
            120,
        )
        count += 1
        if count >= limit:
            break


def default_registry() -> SearchRegistry:
    registry = SearchRegistry()
    registry.register("commands", navigation_provider)
    registry.register("contacts", contact_provider, feature="contacts")
    registry.register("projects", project_provider, feature="projects")
    registry.register("documents", document_provider, feature="documents")
    return registry
