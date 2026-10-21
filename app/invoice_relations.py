"""Audited, sidecar-only invoice relationships and internal annotation revisions."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .document_store import CONTROL_DIR, utc_now
from .file_lock import exclusive_file_lock


INVOICE_RELATION_TYPES = {
    "follow_up": "Folgerechnung",
    "supplement": "Ergänzungsrechnung / Nachberechnung",
    "advance_final": "Abschlag / Schlussrechnung",
    "correction_reference": "Korrektur- / Stornoreferenz",
    "project_related": "Projektbezogene Rechnung",
}
INVOICE_RELATION_TYPES_EN = {
    "follow_up": "Follow-up invoice",
    "supplement": "Supplementary invoice / additional charges",
    "advance_final": "Advance / final invoice",
    "correction_reference": "Correction / cancellation reference",
    "project_related": "Project-related invoice",
    "reference_invoice": "Reference printed in invoice document",
}
MAX_RELATION_HISTORY_PER_INVOICE = 500
MAX_ANNOTATIONS_PER_INVOICE = 200
MAX_ANNOTATION_VERSIONS = 50
MAX_ANNOTATION_LENGTH = 5000
_INVOICE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_ANNOTATION_ID = re.compile(r"^[a-f0-9]{32}$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def invoice_project_ids(row: dict[str, Any]) -> set[str]:
    lines = row.get("lines", [])
    if not isinstance(lines, list):
        return set()
    return {
        str(line.get("project_id", "")).strip()
        for line in lines
        if isinstance(line, dict) and str(line.get("project_id", "")).strip()
    }


def _invoice_summary(row: dict[str, Any]) -> dict[str, str]:
    totals = row.get("totals")
    if not isinstance(totals, dict):
        totals = {}
    return {
        "invoice_id": str(row.get("invoice_id", "")),
        "invoice_number": str(row.get("invoice_number", "")),
        "issue_date": str(row.get("issue_date", "")),
        "status": str(row.get("status", "")),
        "gross": str(totals.get("gross", "")),
        "currency": str(row.get("currency", "EUR")),
    }


def invoice_reference_candidates(
    rows: list[dict[str, Any]], contact_id: str, exclude_invoice_id: str = "",
) -> list[dict[str, Any]]:
    eligible = sorted(
        (item for item in rows
         if item.get("contact_id") == contact_id
         and item.get("invoice_id") != exclude_invoice_id
         and item.get("status") not in {"draft", "finalizing"}
         and not str(item.get("invoice_number", "")).startswith("DRAFT-")),
        key=lambda item: (str(item.get("issue_date", "")), str(item.get("invoice_number", ""))),
        reverse=True,
    )[:200]
    candidates = []
    for item in eligible:
        totals = item.get("totals")
        if not isinstance(totals, dict):
            totals = {}
        candidates.append({
            "invoice_id": str(item.get("invoice_id", "")),
            "invoice_number": str(item.get("invoice_number", "")),
            "issue_date": str(item.get("issue_date", "")),
            "totals": {"gross": str(totals.get("gross", ""))},
            "currency": str(item.get("currency", "EUR")),
        })
    return candidates


def filter_invoice_overview(
    rows: list[dict[str, Any]], contact_map: dict[str, dict[str, Any]],
    project_titles: dict[str, str], related_invoice_ids: dict[str, set[str]],
    query: str, selected_status: str, selected_project_id: str,
    selected_related_invoice_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    visible = [
        {**row, "contact": contact_map[row.get("contact_id", "")]}
        for row in rows if row.get("contact_id") in contact_map
    ]
    visible_by_id = {str(row.get("invoice_id", "")): row for row in visible}
    link_index = {
        invoice_id: set(related_invoice_ids.get(invoice_id, set()))
        for invoice_id in visible_by_id
    }
    for invoice_id, row in visible_by_id.items():
        reference_id = str(row.get("reference_invoice_id", ""))
        reference = visible_by_id.get(reference_id)
        if reference and reference.get("contact_id") == row.get("contact_id"):
            link_index[invoice_id].add(reference_id)
            link_index[reference_id].add(invoice_id)
    project_ids = {
        project_id for row in visible for project_id in invoice_project_ids(row)
    }
    projects = [
        {"project_id": project_id, "title": project_titles.get(project_id, project_id)}
        for project_id in sorted(project_ids, key=lambda value: project_titles.get(value, value).casefold())
    ]
    related_ids = {
        related_id
        for invoice_id, linked_ids in link_index.items()
        if invoice_id in visible_by_id
        for related_id in linked_ids
        if related_id in visible_by_id
        and visible_by_id[related_id].get("contact_id") == visible_by_id[invoice_id].get("contact_id")
    }
    related = [
        {"invoice_id": invoice_id, "invoice_number": visible_by_id[invoice_id].get("invoice_number", "")}
        for invoice_id in sorted(
            related_ids,
            key=lambda value: str(visible_by_id[value].get("invoice_number", "")).casefold(),
        )
    ]
    selected_query = query.casefold()
    filtered = []
    for row in visible:
        project_ids_for_row = invoice_project_ids(row)
        payment_state = row.get("payment_state")
        payment_status = payment_state.get("status") if isinstance(payment_state, dict) else None
        if selected_status and payment_status != selected_status:
            continue
        if selected_project_id and selected_project_id not in project_ids_for_row:
            continue
        linked_ids = link_index.get(str(row.get("invoice_id", "")), set())
        accessible_links = {
            linked_id for linked_id in linked_ids
            if linked_id in visible_by_id
            and visible_by_id[linked_id].get("contact_id") == row.get("contact_id")
        }
        if selected_related_invoice_id and selected_related_invoice_id not in accessible_links:
            continue
        related_numbers = " ".join(
            str(visible_by_id[linked_id].get("invoice_number", ""))
            for linked_id in accessible_links
        )
        project_names = " ".join(
            f"{project_id} {project_titles.get(project_id, '')}"
            for project_id in project_ids_for_row
        )
        buyer = row.get("buyer")
        buyer_name = buyer.get("name", "") if isinstance(buyer, dict) else ""
        contact = row.get("contact")
        contact_fields = contact.get("fields") if isinstance(contact, dict) else None
        contact_name = contact_fields.get("display_name", "") if isinstance(contact_fields, dict) else ""
        searchable = " ".join((
            str(row.get("invoice_number", "")),
            str(buyer_name), str(contact_name),
            project_names, related_numbers,
        ))
        if selected_query and selected_query not in searchable.casefold():
            continue
        filtered.append(row)
    return filtered, projects, related


def invoice_overview_data(
    root: str | Path, actor: str, query: str, selected_status: str,
    selected_project_id: str, selected_related_invoice_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]], bool]:
    from .business_document_generation import invoices
    from .contact_store import ContactStore
    from .project_store import ProjectStore

    contacts = ContactStore(root)
    contact_map = {
        item["contact_id"]: item for item in contacts.contacts(actor)
        if contacts.can_manage_contact(item, actor)
    }
    invoice_rows = invoices(Path(root))
    visible_ids = [
        str(row["invoice_id"]) for row in invoice_rows
        if row.get("contact_id") in contact_map
    ]
    relation_error = False
    try:
        relation_index = InvoiceRelationStore(root).related_invoice_ids(visible_ids)
    except (OSError, sqlite3.Error, ValueError) as exc:
        logging.getLogger(__name__).warning(
            "Invoice relationship filter unavailable (%s)", type(exc).__name__
        )
        relation_index = {}
        relation_error = True
    try:
        project_titles = {
            str(project.get("project_id", "")): str(project.get("title", ""))
            for project in ProjectStore(root).projects()
        }
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning(
            "Invoice project filter labels unavailable (%s)", type(exc).__name__
        )
        project_titles = {}
    rows, projects, related = filter_invoice_overview(
        invoice_rows, contact_map, project_titles, relation_index, query,
        selected_status, selected_project_id, selected_related_invoice_id,
    )
    return rows, projects, related, relation_error


class InvoiceRelationStore:
    """Store links/notes separately so they never rewrite invoice JSON or PDFs."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.database = self.control / "invoice-relations.sqlite"
        self.lock_path = self.control / ".invoice-relations.lock"
        self.control.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            try:
                descriptor = os.open(
                    self.database, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600,
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            self._initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS invoice_relation(
                    relation_id TEXT PRIMARY KEY,
                    source_invoice_id TEXT NOT NULL,
                    target_invoice_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    removed_by TEXT NOT NULL DEFAULT '',
                    removed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ux_invoice_relation_active
                    ON invoice_relation(source_invoice_id,target_invoice_id)
                    WHERE removed_at='';
                CREATE INDEX IF NOT EXISTS ix_invoice_relation_source
                    ON invoice_relation(source_invoice_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS ix_invoice_relation_target
                    ON invoice_relation(target_invoice_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS invoice_annotation_version(
                    annotation_id TEXT NOT NULL,
                    invoice_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    author TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(annotation_id,version)
                );
                CREATE INDEX IF NOT EXISTS ix_invoice_annotation_invoice
                    ON invoice_annotation_version(invoice_id,annotation_id,version);
                CREATE TABLE IF NOT EXISTS invoice_relation_audit(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    invoice_id TEXT NOT NULL,
                    related_id TEXT NOT NULL DEFAULT '',
                    relation_type TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 0,
                    body_sha256 TEXT NOT NULL DEFAULT '',
                    occurred_at TEXT NOT NULL
                );
                """
            )
        try:
            os.chmod(self.database, 0o600)
        except OSError:
            # The file lives in the application's private control directory;
            # chmod may be unsupported on some platforms.
            pass

    @staticmethod
    def _invoice_id(value: str) -> str:
        result = str(value or "").strip()
        if not _INVOICE_ID.fullmatch(result):
            raise ValueError("invalid invoice identifier")
        return result

    @staticmethod
    def _actor(value: str) -> str:
        actor = str(value or "").strip()
        if not actor or len(actor) > 200:
            raise ValueError("a named actor is required")
        return actor

    @staticmethod
    def _body(value: object) -> str:
        body = str(value or "").strip()
        if not body:
            raise ValueError("an internal annotation is required")
        if len(body) > MAX_ANNOTATION_LENGTH or _CONTROL_CHARACTERS.search(body):
            raise ValueError("annotation is too long or contains unsupported control characters")
        return body

    @staticmethod
    def _audit(
        db: sqlite3.Connection,
        event_type: str,
        actor: str,
        invoice_id: str,
        *,
        related_id: str = "",
        relation_type: str = "",
        version: int = 0,
        body: str = "",
    ) -> None:
        db.execute(
            """INSERT INTO invoice_relation_audit(
                event_type,actor,invoice_id,related_id,relation_type,version,body_sha256,occurred_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                event_type, actor, invoice_id, related_id, relation_type, version,
                hashlib.sha256(body.encode("utf-8")).hexdigest() if body else "",
                utc_now(),
            ),
        )

    def _would_cycle(self, db: sqlite3.Connection, source_id: str, target_id: str) -> bool:
        frontier = [target_id]
        visited: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current == source_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            if len(visited) > 10000:
                raise RuntimeError("invoice relation cycle check exceeded its safety bound")
            rows = db.execute(
                """SELECT target_invoice_id FROM invoice_relation
                   WHERE source_invoice_id=? AND removed_at='' LIMIT 1000""",
                (current,),
            ).fetchall()
            frontier.extend(str(row["target_invoice_id"]) for row in rows)
        return False

    def add_relationship(
        self, source_invoice_id: str, target_invoice_id: str, relation_type: str, actor: str,
    ) -> dict[str, Any]:
        source_id = self._invoice_id(source_invoice_id)
        target_id = self._invoice_id(target_invoice_id)
        actor = self._actor(actor)
        kind = str(relation_type or "").strip()
        if kind not in INVOICE_RELATION_TYPES:
            raise ValueError("invoice relationship type is invalid")
        if source_id == target_id:
            raise ValueError("an invoice cannot be linked to itself")
        relation_id = uuid.uuid4().hex
        created_at = utc_now()
        with exclusive_file_lock(self.lock_path):
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                counts = db.execute(
                    """SELECT
                         SUM(CASE WHEN source_invoice_id=? OR target_invoice_id=?
                                  THEN 1 ELSE 0 END),
                         SUM(CASE WHEN source_invoice_id=? OR target_invoice_id=?
                                  THEN 1 ELSE 0 END)
                       FROM invoice_relation
                       WHERE source_invoice_id IN (?, ?) OR target_invoice_id IN (?, ?)""",
                    (source_id, source_id, target_id, target_id,
                     source_id, target_id, source_id, target_id),
                ).fetchone()
                if any(int(count or 0) >= MAX_RELATION_HISTORY_PER_INVOICE for count in counts):
                    raise ValueError("invoice relationship history limit reached")
                if self._would_cycle(db, source_id, target_id):
                    raise ValueError("invoice relationship would create a cycle")
                try:
                    db.execute(
                        """INSERT INTO invoice_relation(
                            relation_id,source_invoice_id,target_invoice_id,relation_type,created_by,created_at
                        ) VALUES(?,?,?,?,?,?)""",
                        (relation_id, source_id, target_id, kind, actor, created_at),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("invoice relationship already exists") from exc
                self._audit(db, "created", actor, source_id, related_id=target_id, relation_type=kind)
                self._audit(db, "created", actor, target_id, related_id=source_id, relation_type=kind)
        return {
            "relation_id": relation_id, "source_invoice_id": source_id,
            "target_invoice_id": target_id, "relation_type": kind,
            "created_by": actor, "created_at": created_at,
        }

    def relationships_for(self, invoice_id: str, *, include_removed: bool = False) -> list[dict[str, Any]]:
        invoice_id = self._invoice_id(invoice_id)
        removed_filter = "" if include_removed else " AND removed_at=''"
        limit = MAX_RELATION_HISTORY_PER_INVOICE
        with self._db() as db:
            rows = db.execute(
                f"""SELECT * FROM invoice_relation
                   WHERE (source_invoice_id=? OR target_invoice_id=?){removed_filter}
                   ORDER BY created_at DESC,relation_id DESC LIMIT ?""",
                (invoice_id, invoice_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def related_invoice_ids(self, invoice_ids: list[str]) -> dict[str, set[str]]:
        selected = sorted({self._invoice_id(value) for value in invoice_ids})
        related: dict[str, set[str]] = {invoice_id: set() for invoice_id in selected}
        for start in range(0, len(selected), 400):
            batch = selected[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            with self._db() as db:
                rows = db.execute(
                    f"""SELECT source_invoice_id,target_invoice_id FROM invoice_relation
                        WHERE removed_at='' AND (source_invoice_id IN ({placeholders})
                        OR target_invoice_id IN ({placeholders}))""",
                    (*batch, *batch),
                ).fetchall()
            for row in rows:
                source_id = str(row["source_invoice_id"])
                target_id = str(row["target_invoice_id"])
                if source_id in related:
                    related[source_id].add(target_id)
                if target_id in related:
                    related[target_id].add(source_id)
        return related

    def audit_events_for(self, invoice_id: str, limit: int = 200) -> list[dict[str, Any]]:
        invoice_id = self._invoice_id(invoice_id)
        size = max(1, min(500, int(limit)))
        with self._db() as db:
            rows = db.execute(
                """SELECT event_type,actor,related_id,relation_type,version,body_sha256,occurred_at
                   FROM invoice_relation_audit WHERE invoice_id=?
                   ORDER BY event_id DESC LIMIT ?""",
                (invoice_id, size),
            ).fetchall()
        return [dict(row) for row in rows]

    def relationship_for(self, invoice_id: str, relation_id: str) -> dict[str, Any] | None:
        invoice_id = self._invoice_id(invoice_id)
        if not _ANNOTATION_ID.fullmatch(str(relation_id or "")):
            return None
        with self._db() as db:
            row = db.execute(
                """SELECT * FROM invoice_relation WHERE relation_id=? AND removed_at=''
                   AND (source_invoice_id=? OR target_invoice_id=?)""",
                (relation_id, invoice_id, invoice_id),
            ).fetchone()
        return dict(row) if row else None

    def remove_relationship(self, invoice_id: str, relation_id: str, actor: str) -> dict[str, Any]:
        invoice_id = self._invoice_id(invoice_id)
        actor = self._actor(actor)
        if not _ANNOTATION_ID.fullmatch(str(relation_id or "")):
            raise ValueError("invalid invoice relationship identifier")
        removed_at = utc_now()
        with exclusive_file_lock(self.lock_path):
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    """SELECT * FROM invoice_relation WHERE relation_id=? AND removed_at=''
                       AND (source_invoice_id=? OR target_invoice_id=?)""",
                    (relation_id, invoice_id, invoice_id),
                ).fetchone()
                if row is None:
                    raise KeyError("invoice relationship does not exist")
                db.execute(
                    "UPDATE invoice_relation SET removed_by=?,removed_at=? WHERE relation_id=?",
                    (actor, removed_at, relation_id),
                )
                source_id = str(row["source_invoice_id"])
                target_id = str(row["target_invoice_id"])
                kind = str(row["relation_type"])
                self._audit(db, "removed", actor, source_id, related_id=target_id, relation_type=kind)
                self._audit(db, "removed", actor, target_id, related_id=source_id, relation_type=kind)
        return {"relation_id": relation_id, "source_invoice_id": source_id,
                "target_invoice_id": target_id, "relation_type": kind,
                "removed_by": actor, "removed_at": removed_at}

    def add_annotation(self, invoice_id: str, body: object, actor: str) -> dict[str, Any]:
        invoice_id = self._invoice_id(invoice_id)
        body = self._body(body)
        actor = self._actor(actor)
        note_id = uuid.uuid4().hex
        created_at = utc_now()
        with exclusive_file_lock(self.lock_path):
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                count = db.execute(
                    "SELECT COUNT(DISTINCT annotation_id) FROM invoice_annotation_version WHERE invoice_id=?",
                    (invoice_id,),
                ).fetchone()[0]
                if int(count) >= MAX_ANNOTATIONS_PER_INVOICE:
                    raise ValueError("invoice annotation limit reached")
                db.execute(
                    """INSERT INTO invoice_annotation_version(
                        annotation_id,invoice_id,version,body,author,created_at,deleted
                    ) VALUES(?,?,1,?,?,?,0)""",
                    (note_id, invoice_id, body, actor, created_at),
                )
                self._audit(db, "annotation_added", actor, invoice_id,
                            related_id=note_id, version=1, body=body)
        return {"annotation_id": note_id, "invoice_id": invoice_id,
                "version": 1, "body": body, "author": actor,
                "created_at": created_at, "deleted": False}

    def revise_annotation(
        self, invoice_id: str, annotation_id: str, body: object, actor: str,
        expected_version: int, *, deleted: bool = False,
    ) -> dict[str, Any]:
        invoice_id = self._invoice_id(invoice_id)
        actor = self._actor(actor)
        if not _ANNOTATION_ID.fullmatch(str(annotation_id or "")):
            raise ValueError("invalid invoice annotation identifier")
        new_body = "" if deleted else self._body(body)
        try:
            expected_version = int(expected_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("annotation version is invalid") from exc
        now = utc_now()
        with exclusive_file_lock(self.lock_path):
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    """SELECT version,deleted FROM invoice_annotation_version
                       WHERE annotation_id=? AND invoice_id=? ORDER BY version DESC LIMIT 1""",
                    (annotation_id, invoice_id),
                ).fetchone()
                if row is None:
                    raise KeyError("invoice annotation does not exist")
                version = int(row["version"])
                if bool(row["deleted"]):
                    raise ValueError("invoice annotation is deleted")
                if version != expected_version:
                    raise ValueError("invoice annotation changed; reload before editing")
                if version >= MAX_ANNOTATION_VERSIONS:
                    raise ValueError("invoice annotation history limit reached")
                next_version = version + 1
                db.execute(
                    """INSERT INTO invoice_annotation_version(
                        annotation_id,invoice_id,version,body,author,created_at,deleted
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (annotation_id, invoice_id, next_version, new_body, actor, now, int(deleted)),
                )
                self._audit(
                    db, "annotation_deleted" if deleted else "annotation_revised",
                    actor, invoice_id, related_id=annotation_id,
                    version=next_version, body=new_body,
                )
        return {"annotation_id": annotation_id, "invoice_id": invoice_id,
                "version": next_version, "body": new_body, "author": actor,
                "created_at": now, "deleted": deleted}

    def annotations_for(self, invoice_id: str) -> list[dict[str, Any]]:
        invoice_id = self._invoice_id(invoice_id)
        with self._db() as db:
            rows = db.execute(
                """SELECT annotation_id,version,body,author,created_at,deleted
                   FROM invoice_annotation_version WHERE invoice_id=?
                   ORDER BY annotation_id,version""",
                (invoice_id,),
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["annotation_id"]), []).append(dict(row))
        result = []
        for annotation_id, versions in grouped.items():
            current = versions[-1]
            result.append({**current, "annotation_id": annotation_id, "history": versions})
        return sorted(result, key=lambda row: (row["created_at"], row["annotation_id"]), reverse=True)


def invoice_relationship_context(
    root: str | Path, row: dict[str, Any], actor: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve only same-customer invoices the request principal can manage."""
    from .business_document_generation import invoice, invoices
    from .contact_store import ContactStore

    base = Path(root).expanduser().resolve()
    service = InvoiceRelationStore(base)
    contacts = ContactStore(base)
    invoice_rows = invoices(base)
    invoice_by_id = {str(item.get("invoice_id", "")): item for item in invoice_rows}
    related = []
    relation_rows = service.relationships_for(row["invoice_id"], include_removed=True)
    active_pairs = {
        frozenset((item["source_invoice_id"], item["target_invoice_id"]))
        for item in relation_rows if not item.get("removed_at")
    }
    for relation in relation_rows:
        outgoing = relation["source_invoice_id"] == row["invoice_id"]
        other_id = relation["target_invoice_id"] if outgoing else relation["source_invoice_id"]
        other = invoice_by_id.get(other_id)
        visible = bool(other and other.get("contact_id") == row.get("contact_id")
                       and contacts.can_manage(str(other.get("contact_id", "")), actor))
        view = {
            "relation_id": relation["relation_id"],
            "relation_type": relation["relation_type"],
            "label": INVOICE_RELATION_TYPES.get(relation["relation_type"], "Rechnungsbezug"),
            "label_en": INVOICE_RELATION_TYPES_EN.get(relation["relation_type"], "Invoice reference"),
            "direction": "outgoing" if outgoing else "incoming",
            "removed_at": relation.get("removed_at", ""),
            "removed_by": relation.get("removed_by", ""),
            "available": bool(visible),
        }
        if visible and other is not None:
            view["invoice"] = _invoice_summary(other)
        related.append(view)
    for other in invoice_rows:
        other_id = str(other.get("invoice_id", ""))
        if other_id == row["invoice_id"]:
            referenced_id = str(row.get("reference_invoice_id", ""))
            outgoing = bool(referenced_id)
            other_id = referenced_id
        else:
            outgoing = False
            if str(other.get("reference_invoice_id", "")) != row["invoice_id"]:
                continue
        if not other_id or frozenset((row["invoice_id"], other_id)) in active_pairs:
            continue
        related_invoice = invoice_by_id.get(other_id)
        visible = bool(related_invoice and related_invoice.get("contact_id") == row.get("contact_id")
                       and contacts.can_manage(str(related_invoice.get("contact_id", "")), actor))
        view = {
            "relation_id": "",
            "relation_type": "reference_invoice",
            "label": "Bezug im Rechnungsdokument",
            "label_en": INVOICE_RELATION_TYPES_EN["reference_invoice"],
            "direction": "outgoing" if outgoing else "incoming",
            "removed_at": "",
            "removed_by": "",
            "available": visible,
            "virtual": True,
        }
        if visible and related_invoice is not None:
            view["invoice"] = _invoice_summary(related_invoice)
        related.append(view)
    candidates = invoice_reference_candidates(invoice_rows, str(row.get("contact_id", "")), row["invoice_id"])
    return related, candidates, service.annotations_for(row["invoice_id"])
