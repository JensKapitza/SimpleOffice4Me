"""Shared entity-detail context without replacing existing domain pages."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class EntityContext:
    entity_type: str
    entity_id: str
    title: str
    subtitle: str
    canonical_url: str
    status: str = ""
    overview: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EntityRequest:
    root: Path
    actor: str
    is_admin: bool
    features: frozenset[str]


Resolver = Callable[[str, EntityRequest], EntityContext | None]
SectionProvider = Callable[[EntityContext, EntityRequest], dict | None]


class EntityContextRegistry:
    def __init__(self):
        self._resolvers: dict[str, tuple[str, Resolver]] = {}
        self._sections: list[tuple[str, SectionProvider]] = []

    def register_entity(
        self,
        entity_type: str,
        resolver: Resolver,
        *,
        feature: str = "",
    ) -> None:
        entity_type = str(entity_type).strip()
        if not entity_type or entity_type in self._resolvers:
            raise ValueError("entity type must be unique and non-empty")
        self._resolvers[entity_type] = (str(feature).strip(), resolver)

    def register_section(self, name: str, provider: SectionProvider) -> None:
        name = str(name).strip()
        if not name or any(existing == name for existing, _ in self._sections):
            raise ValueError("section name must be unique and non-empty")
        self._sections.append((name, provider))

    def resolve(
        self,
        entity_type: str,
        entity_id: str,
        request: EntityRequest,
    ) -> EntityContext | None:
        registration = self._resolvers.get(str(entity_type))
        if registration is None:
            return None
        feature, resolver = registration
        if feature and feature not in request.features:
            return None
        try:
            return resolver(str(entity_id), request)
        except (OSError, ValueError, LookupError, PermissionError):
            return None

    def sections(
        self,
        entity: EntityContext,
        request: EntityRequest,
    ) -> list[dict]:
        result = []
        for name, provider in self._sections:
            try:
                section = provider(entity, request)
            except Exception:
                section = None
            if not section:
                continue
            row = dict(section)
            row.setdefault("name", name)
            result.append(row)
        return result


def contact_resolver(entity_id: str, request: EntityRequest) -> EntityContext | None:
    from .contact_store import ContactStore

    item = ContactStore(request.root).get(entity_id, request.actor)
    fields = item.get("fields", {})
    overview = tuple(
        (label, str(value))
        for label, value in (
            ("Firma", fields.get("company")),
            ("E-Mail", fields.get("email")),
            ("Telefon", fields.get("phone")),
            ("Position", fields.get("title")),
        )
        if value
    )
    return EntityContext(
        "contact",
        entity_id,
        str(fields.get("display_name") or entity_id),
        str(fields.get("company") or ""),
        f"/documents/contacts/{entity_id}",
        str(fields.get("status") or item.get("status") or ""),
        overview,
    )


def project_resolver(entity_id: str, request: EntityRequest) -> EntityContext | None:
    from .project_store import ProjectStore

    item = ProjectStore(request.root).project(entity_id)
    overview = tuple(
        (label, str(value))
        for label, value in (
            ("Status", item.get("status")),
            ("Ort", item.get("location")),
            ("Start", item.get("planned_start")),
            ("Ende", item.get("planned_end")),
        )
        if value
    )
    return EntityContext(
        "project",
        entity_id,
        str(item.get("title") or entity_id),
        str(item.get("description") or ""),
        f"/documents/projects/{entity_id}",
        str(item.get("status") or ""),
        overview,
    )


def document_resolver(entity_id: str, request: EntityRequest) -> EntityContext | None:
    from .chat_access import document_visible
    from .document_store import DocumentStore

    item = DocumentStore(request.root).get_document(entity_id)
    if not document_visible(item, request.actor, request.is_admin):
        return None
    path = str(item.get("last_path") or "")
    overview = tuple(
        (label, str(value))
        for label, value in (
            ("Pfad", path),
            ("Status", item.get("state")),
            ("Größe", item.get("size")),
            ("Version", item.get("version_number")),
        )
        if value not in (None, "")
    )
    return EntityContext(
        "document",
        entity_id,
        Path(path).name or entity_id,
        path,
        f"/documents/{entity_id}",
        str(item.get("state") or ""),
        overview,
    )


def activity_section(entity: EntityContext, request: EntityRequest) -> dict | None:
    """Optional adapter: absence of the activity subsystem hides the section."""
    from .v3_capabilities import enabled

    if not enabled("v3.activity"):
        return None
    try:
        from .v3_activity import ActivityStore
    except ImportError:
        return None
    rows = ActivityStore(request.root).list(
        entity_type=entity.entity_type,
        entity_id=entity.entity_id,
        limit=30,
    )
    if not rows:
        return None
    return {
        "title": "Aktivitäten",
        "kind": "activity",
        "items": [
            {
                "title": row.event_type,
                "detail": row.actor,
                "time": row.occurred_at,
            }
            for row in rows
        ],
    }


def default_registry() -> EntityContextRegistry:
    registry = EntityContextRegistry()
    registry.register_entity("contact", contact_resolver, feature="contacts")
    registry.register_entity("project", project_resolver, feature="projects")
    registry.register_entity("document", document_resolver, feature="documents")
    registry.register_section("activity", activity_section)
    return registry
