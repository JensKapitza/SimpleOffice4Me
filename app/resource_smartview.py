"""Virtual SmartView for the local `self` pane."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .document_store import DocumentStore
from .resource_provider import ProviderCapabilities, ProviderError, ResourceEntry

SMART_COLLECTIONS = {
    "documents": "Dokumente",
    "customers": "Kunden",
    "projects": "Projekte",
    "invoices": "Rechnungen",
    "receipts": "Belege",
    "mail": "E-Mail",
    "tasks": "Aufgaben",
    "inbox": "Eingang",
    "unassigned": "Ohne Zuordnung",
    "duplicates": "Duplikate",
    "archive": "Archiv",
    "recent": "Zuletzt geändert",
}

_COLLECTION_TERMS = {
    "customers": ("kunde", "customer", "contact"),
    "projects": ("projekt", "project"),
    "invoices": ("rechnung", "invoice"),
    "receipts": ("beleg", "receipt", "expense"),
    "mail": ("email/", ".eml", "mail"),
    "tasks": ("aufgabe", "task", "todo"),
}

BUSINESS_CANDIDATE_LIMIT = 5000
SMART_RESULT_LIMIT = 500


class SmartViewProvider:
    provider_id = "smart"
    label = "SmartView"
    capabilities = ProviderCapabilities(
        read=True, write=False, delete=False, move=False, copy=True, folders=True,
        search=True, metadata=True, streaming=True, smart_view=True,
        server_side_copy=False,
    )

    def __init__(self, root: str | Path):
        self.store = DocumentStore(root)
        self.store.initialize()
        self.root = self.store.root

    def list(self, path: str = "") -> Iterable[ResourceEntry]:
        return self.list_scoped(path)

    def list_scoped(self, path: str = "", context: str = "") -> Iterable[ResourceEntry]:
        """List a SmartView collection, optionally relative to a real subtree.

        Context currently changes the duplicate collection: a duplicate group is
        included only when at least two files with the same SHA-256 exist inside
        that subtree. This makes ``SmartView/Duplikate`` useful from any mounted
        folder without leaking unrelated duplicate groups from elsewhere.
        """
        key = str(path or "").strip("/")
        if not key:
            return [
                ResourceEntry(
                    resource_id=collection,
                    name=label,
                    kind="folder",
                    path=collection,
                    mime_type="inode/directory",
                    provider=self.provider_id,
                )
                for collection, label in SMART_COLLECTIONS.items()
            ]
        if key not in SMART_COLLECTIONS:
            raise ProviderError("Unbekannte SmartView")
        with self.store._db() as db:
            if key == "duplicates":
                rows = self._duplicate_rows(db, context)
            elif key == "recent":
                rows = db.execute(
                    "SELECT relative_path,document_id,size,last_seen_at FROM scan_file ORDER BY last_seen_at DESC LIMIT 500"
                ).fetchall()
            elif key == "inbox":
                rows = db.execute("""SELECT s.relative_path,s.document_id,s.size,s.last_seen_at FROM scan_file s
                    JOIN document_listing d ON d.document_id=s.document_id WHERE d.state='inbox'
                    ORDER BY s.last_seen_at DESC LIMIT 500""").fetchall()
            elif key == "archive":
                rows = db.execute("""SELECT s.relative_path,s.document_id,s.size,s.last_seen_at FROM scan_file s
                    JOIN document_listing d ON d.document_id=s.document_id WHERE d.state='archived'
                    ORDER BY s.last_seen_at DESC LIMIT 500""").fetchall()
            elif key == "unassigned":
                rows = db.execute("""SELECT s.relative_path,s.document_id,s.size,s.last_seen_at FROM scan_file s
                    JOIN document_listing d ON d.document_id=s.document_id
                    WHERE d.has_notes=0 AND d.has_relationships=0 ORDER BY s.last_seen_at DESC LIMIT 500""").fetchall()
            elif key in _COLLECTION_TERMS:
                rows = self._business_rows(db, _COLLECTION_TERMS[key])
            else:
                rows = db.execute(
                    "SELECT relative_path,document_id,size,last_seen_at FROM scan_file ORDER BY relative_path LIMIT 500"
                ).fetchall()
        return [self._entry(row) for row in rows]

    @staticmethod
    def _context_prefix(context: str) -> str:
        value = str(context or "").strip().strip("/")
        if not value:
            return ""
        normalized = Path(value)
        if normalized.is_absolute() or any(part in {"", ".", ".."} for part in normalized.parts):
            raise ProviderError("Ungültiger SmartView-Kontext")
        return normalized.as_posix().rstrip("/") + "/"

    def _duplicate_rows(self, db, context: str):
        prefix = self._context_prefix(context)
        if not prefix:
            return db.execute("""SELECT s.relative_path,s.document_id,s.size,s.last_seen_at
                FROM scan_file s JOIN (
                    SELECT sha256 FROM scan_file
                    WHERE sha256 IS NOT NULL AND sha256 != ''
                    GROUP BY sha256 HAVING COUNT(*) > 1
                ) d USING(sha256)
                ORDER BY s.sha256,s.relative_path LIMIT 500""").fetchall()

        # LIKE is used with a bound parameter; wildcard characters from folder
        # names are escaped so the subtree boundary remains exact.
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = escaped + "%"
        return db.execute("""SELECT s.relative_path,s.document_id,s.size,s.last_seen_at
            FROM scan_file s JOIN (
                SELECT sha256 FROM scan_file
                WHERE sha256 IS NOT NULL AND sha256 != ''
                  AND relative_path LIKE ? ESCAPE '\\'
                GROUP BY sha256 HAVING COUNT(*) > 1
            ) d USING(sha256)
            WHERE s.relative_path LIKE ? ESCAPE '\\'
            ORDER BY s.sha256,s.relative_path LIMIT 500""", (pattern, pattern)).fetchall()

    @staticmethod
    def _business_rows(db, terms: tuple[str, ...]):
        """Use fixed SQL and filter a bounded candidate projection in Python."""
        rows = db.execute("""SELECT s.relative_path,s.document_id,s.size,s.last_seen_at,
                    COALESCE(x.tags,'') AS search_tags, COALESCE(x.attributes,'') AS search_attributes
                FROM scan_file s LEFT JOIN document_search x ON x.document_id=s.document_id
                ORDER BY s.last_seen_at DESC LIMIT 5000""").fetchall()
        lowered_terms = tuple(term.casefold() for term in terms)
        matches = []
        for row in rows[:BUSINESS_CANDIDATE_LIMIT]:
            haystack = "\n".join((
                str(row["relative_path"] or ""),
                str(row["search_tags"] or ""),
                str(row["search_attributes"] or ""),
            )).casefold()
            if any(term in haystack for term in lowered_terms):
                matches.append(row)
                if len(matches) >= SMART_RESULT_LIMIT:
                    break
        return matches

    def _entry(self, row) -> ResourceEntry:
        path = str(row["relative_path"])
        return ResourceEntry(
            resource_id=path,
            name=Path(path).name,
            kind="file",
            path=path,
            size=int(row["size"] or 0),
            modified=str(row["last_seen_at"] or ""),
            provider=self.provider_id,
            metadata={"document_id": str(row["document_id"] or "")},
        )

    def stat(self, resource_id: str) -> ResourceEntry:
        with self.store._db() as db:
            row = db.execute(
                "SELECT relative_path,document_id,size,last_seen_at FROM scan_file WHERE relative_path=?",
                (str(resource_id),),
            ).fetchone()
        if row is None:
            raise ProviderError("SmartView-Ressource nicht gefunden")
        return self._entry(row)

    def open(self, resource_id: str):
        candidate = (self.root / str(resource_id)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ProviderError("Ungültiger SmartView-Pfad") from exc
        if not candidate.is_file() or candidate.is_symlink():
            raise ProviderError("Datei nicht gefunden")
        return candidate.open("rb")

    def search(self, query: str, path: str = "") -> Iterable[ResourceEntry]:
        needle = str(query or "").casefold().strip()
        if not needle:
            return []
        return [
            entry for entry in self.list(path or "documents")
            if needle in entry.name.casefold() or needle in entry.path.casefold()
        ][:SMART_RESULT_LIMIT]

    def upload(self, *args, **kwargs):
        raise ProviderError("SmartView ist virtuell und nicht direkt beschreibbar")

    mkdir = upload
    delete = upload
    move = upload
