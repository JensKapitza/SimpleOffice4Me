"""Relationship-backed CRM workspace for SimpleOffice 3.0."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Iterable

from .contact_extensions import ContactCRMStore
from .contact_store import ContactStore
from .document_store import DocumentStore, utc_now
from .project_store import ProjectStore
from .todo_store import TodoStore
from .calendar_store import CalendarStore
from .v3_relations import EntityRef, EntityRegistry, RelationStore, RelationType


CRM_RELATION_TYPE = "crm_link"
CRM_ROLE_TYPES = {
    "works_at",
    "contact_for",
    "customer",
    "supplier",
    "employee",
    "parent_company",
    "subsidiary",
    "custom",
}
CRM_ROLE_STATUSES = {"planned", "active", "former"}


def _ref_dict(ref: EntityRef) -> dict[str, str]:
    return {"type": ref.type, "id": ref.id, "instance": ref.instance}


def _same_ref(value: dict[str, Any], ref: EntityRef) -> bool:
    return (
        str(value.get("type", "")) == ref.type
        and str(value.get("id", "")) == ref.id
        and str(value.get("instance", "")) == ref.instance
    )


def _clean_date(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    return date.fromisoformat(value).isoformat()


def _relation_db(root: Path) -> Path:
    return root / ".simpleoffice-meta" / "v3-relations.sqlite3"


def _contact_resolver(root: Path):
    store = ContactStore(root)

    def resolve(ref: EntityRef):
        try:
            return store.get(ref.id)
        except ValueError:
            return None

    return resolve


def _contact_authorizer(root: Path):
    store = ContactStore(root)

    def authorize(principal: str, action: str, ref: EntityRef) -> bool:
        if action == "read":
            try:
                store.get(ref.id, principal)
                return True
            except ValueError:
                return False
        if action == "link":
            return store.can_manage(ref.id, principal)
        return False

    return authorize


def _project_resolver(root: Path):
    store = ProjectStore(root)

    def resolve(ref: EntityRef):
        try:
            return store.project(ref.id)
        except ValueError:
            return None

    return resolve


def _project_authorizer(principal: str, action: str, ref: EntityRef) -> bool:
    # ProjectStore currently has no object-level ACL. Route-level feature
    # permission remains authoritative until the project domain gains one.
    return bool(principal and action in {"read", "link"})


def crm_relation_store(root: str | Path) -> RelationStore:
    root = Path(root).expanduser().resolve()
    registry = EntityRegistry()
    contact_resolver = _contact_resolver(root)
    contact_authorizer = _contact_authorizer(root)
    registry.register("contact", contact_resolver, contact_authorizer)
    registry.register("company", contact_resolver, contact_authorizer)
    registry.register("project", _project_resolver(root), _project_authorizer)
    store = RelationStore(_relation_db(root), registry)
    store.register_type(RelationType(CRM_RELATION_TYPE, directed=False))
    return store


@dataclass(frozen=True)
class CRMRole:
    relation_id: str
    role_id: str
    role: str
    label: str
    subject: EntityRef
    object: EntityRef
    status: str
    valid_from: str
    valid_to: str
    title: str
    note: str
    created_at: str
    created_by: str
    updated_at: str
    updated_by: str


class CRMRelationshipService:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.store = crm_relation_store(self.root)

    def _between(self, principal: str, left: EntityRef, right: EntityRef):
        for relation in self.store.list_for(
            principal,
            left,
            relation_type=CRM_RELATION_TYPE,
            limit=500,
        ):
            other = relation.target if relation.source == left else relation.source
            if other == right:
                return relation
        return None

    @staticmethod
    def _role_from(relation_id: str, value: dict[str, Any]) -> CRMRole:
        subject = value.get("subject", {}) if isinstance(value.get("subject"), dict) else {}
        obj = value.get("object", {}) if isinstance(value.get("object"), dict) else {}
        return CRMRole(
            relation_id=relation_id,
            role_id=str(value.get("role_id", "")),
            role=str(value.get("role", "")),
            label=str(value.get("label", "")),
            subject=EntityRef(
                str(subject.get("type", "")),
                str(subject.get("id", "")),
                str(subject.get("instance", "")),
            ),
            object=EntityRef(
                str(obj.get("type", "")),
                str(obj.get("id", "")),
                str(obj.get("instance", "")),
            ),
            status=str(value.get("status", "")),
            valid_from=str(value.get("valid_from", "")),
            valid_to=str(value.get("valid_to", "")),
            title=str(value.get("title", "")),
            note=str(value.get("note", "")),
            created_at=str(value.get("created_at", "")),
            created_by=str(value.get("created_by", "")),
            updated_at=str(value.get("updated_at", "")),
            updated_by=str(value.get("updated_by", "")),
        )

    @staticmethod
    def _validate_role(
        role: str,
        label: str,
        status: str,
        valid_from: str,
        valid_to: str,
    ) -> tuple[str, str, str, str, str]:
        role = str(role).strip().casefold()
        if role not in CRM_ROLE_TYPES:
            raise ValueError("unknown CRM relationship role")
        label = " ".join(str(label).split())[:160]
        if role == "custom" and not label:
            raise ValueError("custom relationship requires a label")
        status = str(status or "active").strip().casefold()
        if status not in CRM_ROLE_STATUSES:
            raise ValueError("unknown CRM relationship status")
        start = _clean_date(valid_from)
        end = _clean_date(valid_to)
        if start and end and end < start:
            raise ValueError("relationship end must not precede start")
        return role, label, status, start, end

    def add_role(
        self,
        principal: str,
        subject: EntityRef,
        obj: EntityRef,
        *,
        role: str,
        label: str = "",
        status: str = "active",
        valid_from: str = "",
        valid_to: str = "",
        title: str = "",
        note: str = "",
    ) -> CRMRole:
        if subject == obj:
            raise ValueError("CRM relationship endpoints must differ")
        if subject.type not in {"contact", "company"}:
            raise ValueError("CRM relationship subject must be a contact or company")
        if obj.type not in {"contact", "company", "project"}:
            raise ValueError("unsupported CRM relationship target")
        role, label, status, valid_from, valid_to = self._validate_role(
            role,
            label,
            status,
            valid_from,
            valid_to,
        )
        now = utc_now()
        role_row = {
            "role_id": uuid.uuid4().hex,
            "role": role,
            "label": label,
            "subject": _ref_dict(subject),
            "object": _ref_dict(obj),
            "status": status,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "title": " ".join(str(title).split())[:200],
            "note": str(note).strip()[:4000],
            "created_at": now,
            "created_by": principal,
            "updated_at": now,
            "updated_by": principal,
        }
        relation = self._between(principal, subject, obj)
        if relation is None:
            relation = self.store.add(
                principal,
                CRM_RELATION_TYPE,
                subject,
                obj,
                metadata={"roles": [role_row]},
            )
        else:
            metadata = dict(relation.metadata or {})
            roles = [
                dict(item)
                for item in metadata.get("roles", [])
                if isinstance(item, dict)
            ]
            duplicate = next(
                (
                    item
                    for item in roles
                    if item.get("role") == role
                    and item.get("status") == "active"
                    and _same_ref(item.get("subject", {}), subject)
                    and _same_ref(item.get("object", {}), obj)
                    and not item.get("valid_to")
                    and not valid_to
                ),
                None,
            )
            if duplicate:
                raise ValueError("equivalent active CRM relationship already exists")
            roles.append(role_row)
            metadata["roles"] = roles[-100:]
            relation = self.store.update_metadata(
                principal,
                relation.relation_id,
                metadata,
            )
        return self._role_from(relation.relation_id, role_row)

    def roles_for(
        self,
        principal: str,
        ref: EntityRef,
        *,
        include_former: bool = True,
    ) -> list[CRMRole]:
        rows: list[CRMRole] = []
        seen: set[str] = set()
        for relation in self.store.list_for(
            principal,
            ref,
            relation_type=CRM_RELATION_TYPE,
            limit=500,
        ):
            for item in relation.metadata.get("roles", []):
                if not isinstance(item, dict):
                    continue
                try:
                    role = self._role_from(relation.relation_id, item)
                except ValueError:
                    continue
                if role.role_id in seen:
                    continue
                if not (_same_ref(item.get("subject", {}), ref) or _same_ref(item.get("object", {}), ref)):
                    continue
                if not include_former and role.status == "former":
                    continue
                seen.add(role.role_id)
                rows.append(role)
        return sorted(
            rows,
            key=lambda item: (
                item.status != "active",
                item.valid_to or "9999-12-31",
                item.valid_from,
                item.role,
                item.role_id,
            ),
        )

    def end_role(
        self,
        principal: str,
        relation_id: str,
        role_id: str,
        *,
        valid_to: str = "",
    ) -> CRMRole:
        relation = self.store.get(principal, relation_id)
        if relation is None or relation.relation_type != CRM_RELATION_TYPE:
            raise LookupError("unknown CRM relationship")
        end = _clean_date(valid_to) or date.today().isoformat()
        metadata = dict(relation.metadata or {})
        roles = []
        updated_row = None
        now = utc_now()
        for item in metadata.get("roles", []):
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if str(row.get("role_id", "")) == role_id:
                start = _clean_date(str(row.get("valid_from", "")))
                if start and end < start:
                    raise ValueError("relationship end must not precede start")
                row["status"] = "former"
                row["valid_to"] = end
                row["updated_at"] = now
                row["updated_by"] = principal
                updated_row = row
            roles.append(row)
        if updated_row is None:
            raise LookupError("unknown CRM role")
        metadata["roles"] = roles
        relation = self.store.update_metadata(principal, relation_id, metadata)
        return self._role_from(relation.relation_id, updated_row)

    def _copy_merge_roles(
        self,
        principal: str,
        source: EntityRef,
        target: EntityRef,
    ) -> None:
        relations = self.store.list_for(
            principal,
            source,
            relation_type=CRM_RELATION_TYPE,
            limit=500,
        )
        for relation in relations:
            other = relation.target if relation.source == source else relation.source
            if other == target:
                continue
            copied_roles = []
            for item in relation.metadata.get("roles", []):
                if not isinstance(item, dict):
                    continue
                row = dict(item)
                subject = dict(row.get("subject", {}))
                obj = dict(row.get("object", {}))
                if _same_ref(subject, source):
                    row["subject"] = _ref_dict(target)
                if _same_ref(obj, source):
                    row["object"] = _ref_dict(target)
                if row.get("subject") == row.get("object"):
                    continue
                row["updated_at"] = utc_now()
                row["updated_by"] = principal
                copied_roles.append(row)
            if not copied_roles:
                continue
            existing = self._between(principal, target, other)
            if existing is None:
                self.store.add(
                    principal,
                    CRM_RELATION_TYPE,
                    target,
                    other,
                    metadata={"roles": copied_roles},
                )
                continue
            metadata = dict(existing.metadata or {})
            combined = [
                dict(item)
                for item in metadata.get("roles", [])
                if isinstance(item, dict)
            ]
            known = {str(item.get("role_id", "")) for item in combined}
            combined.extend(
                item
                for item in copied_roles
                if str(item.get("role_id", "")) not in known
            )
            metadata["roles"] = combined[-100:]
            self.store.update_metadata(
                principal,
                existing.relation_id,
                metadata,
            )

    def prepare_contact_merge(
        self,
        principal: str,
        target_id: str,
        source_ids: Iterable[str],
    ) -> None:
        source_ids = [
            str(value).strip()
            for value in source_ids
            if str(value).strip() and str(value).strip() != target_id
        ]
        for entity_type in ("contact", "company"):
            target = EntityRef(entity_type, target_id)
            for source_id in source_ids:
                self._copy_merge_roles(
                    principal,
                    EntityRef(entity_type, source_id),
                    target,
                )

    def finalize_contact_merge(self, source_ids: Iterable[str]) -> None:
        """Remove obsolete CRM links after roles have been copied to the merge target.

        Cleanup is deliberately restricted to crm_link rows. If this cleanup
        fails, the copied target relation still preserves the user's data and
        the stale source row remains repairable.
        """
        ids = [str(value).strip() for value in source_ids if str(value).strip()]
        if not ids:
            return
        path = _relation_db(self.root)
        if not path.exists():
            return
        with sqlite3.connect(path) as db:
            for source_id in ids:
                db.execute(
                    """DELETE FROM v3_relation
                       WHERE relation_type=?
                         AND (
                           (source_type IN ('contact','company') AND source_id=?)
                           OR
                           (target_type IN ('contact','company') AND target_id=?)
                         )""",
                    (CRM_RELATION_TYPE, source_id, source_id),
                )


def prepare_contact_merge(
    root: str | Path,
    target_id: str,
    source_ids: Iterable[str],
    actor: str,
) -> None:
    path = _relation_db(Path(root).expanduser().resolve())
    if not path.exists():
        return
    CRMRelationshipService(root).prepare_contact_merge(
        actor,
        target_id,
        source_ids,
    )


def finalize_contact_merge(
    root: str | Path,
    source_ids: Iterable[str],
) -> None:
    path = _relation_db(Path(root).expanduser().resolve())
    if not path.exists():
        return
    try:
        CRMRelationshipService(root).finalize_contact_merge(source_ids)
    except (OSError, sqlite3.Error, ValueError):
        # Data was already copied before the contact write. A stale source link
        # is preferable to reporting a failed merge after the contact committed.
        return


def _unique_documents_for_contact(
    root: Path,
    contact_id: str,
    actor: str,
    *,
    is_admin: bool,
) -> list[dict[str, Any]]:
    from .business_documents import contact_links, invoices
    from .chat_access import document_visible

    store = DocumentStore(root)
    ids = {
        str(row.get("document_id", ""))
        for row in contact_links(root, contact_id)
        if row.get("document_id")
    }
    ids.update(
        str(row.get("document_id", ""))
        for row in invoices(root)
        if row.get("contact_id") == contact_id and row.get("document_id")
    )
    rows = []
    for document_id in sorted(ids):
        try:
            document = store.get_document(document_id)
        except (OSError, ValueError):
            continue
        if not document_visible(document, actor, is_admin):
            continue
        rows.append(document)
    return rows


def crm_workspace(
    root: str | Path,
    contact_id: str,
    actor: str,
    *,
    is_admin: bool = False,
    features: Iterable[str] = (),
) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    feature_set = frozenset(str(value) for value in features)
    contacts = ContactStore(root)
    contact = contacts.get(contact_id, actor)
    crm = ContactCRMStore(root).record(contact_id)
    relationships = CRMRelationshipService(root)
    roles: list[CRMRole] = []
    for ref_type in ("contact", "company"):
        try:
            roles.extend(
                relationships.roles_for(
                    actor,
                    EntityRef(ref_type, contact_id),
                )
            )
        except (OSError, ValueError):
            continue

    people = contacts.company_people(contact, actor)
    legacy_company = None
    company_id = str(contact.get("fields", {}).get("company_contact_id", ""))
    if company_id:
        try:
            legacy_company = contacts.get(company_id, actor)
        except ValueError:
            legacy_company = None

    projects = []
    if "projects" in feature_set:
        project_store = ProjectStore(root)
        project_ids = set()
        for role in roles:
            for ref in (role.subject, role.object):
                if ref.type == "project":
                    project_ids.add(ref.id)
        for project_id in sorted(project_ids):
            try:
                projects.append(project_store.project(project_id))
            except ValueError:
                continue

    tasks = []
    if "projects" in feature_set:
        tasks = TodoStore(root).items(actor, contact_id=contact_id)[:200]

    events = []
    if "calendar" in feature_set:
        events = [
            item
            for item in CalendarStore(root).events(actor)
            if str(item.get("contact_id", "")) == contact_id
        ][:200]

    documents = []
    invoices_rows = []
    if "documents" in feature_set:
        documents = _unique_documents_for_contact(
            root,
            contact_id,
            actor,
            is_admin=is_admin,
        )
        if contacts.can_manage(contact_id, actor):
            from .business_documents import invoices

            invoices_rows = [
                row
                for row in invoices(root)
                if str(row.get("contact_id", "")) == contact_id
            ][:200]

    return {
        "contact": contact,
        "crm": crm,
        "roles": roles,
        "company_people": people,
        "legacy_company": legacy_company,
        "projects": projects,
        "tasks": tasks,
        "events": events,
        "documents": documents,
        "invoices": invoices_rows,
        "timeline": ContactCRMStore(root).timeline(contact, crm)[:200],
    }
