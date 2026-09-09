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
        key = str(path or "").strip("/")
        if not key:
            return [ResourceEntry(resource_id=k, name=v, kind="folder", path=k, mime_type="inode/directory", provider=self.provider_id) for k, v in SMART_COLLECTIONS.items()]
        if key not in SMART_COLLECTIONS:
            raise ProviderError("Unbekannte SmartView")
        with self.store._db() as db:
            if key == "duplicates":
                rows = db.execute("""SELECT s.relative_path,s.document_id,s.size,s.last_seen_at
                    FROM scan_file s JOIN (SELECT sha256 FROM scan_file GROUP BY sha256 HAVING COUNT(*) > 1) d USING(sha256)
                    ORDER BY s.relative_path LIMIT 500""").fetchall()
            elif key == "recent":
                rows = db.execute("SELECT relative_path,document_id,size,last_seen_at FROM scan_file ORDER BY last_seen_at DESC LIMIT 500").fetchall()
            elif key == "inbox":
                rows = db.execute("""SELECT s.relative_path,s.document_id,s.size,s.last_seen_at FROM scan_file s
                    JOIN document_listing d ON d.document_id=s.document_id WHERE d.state='inbox' ORDER BY s.last_seen_at DESC LIMIT 500""").fetchall()
            elif key == "archive":
                rows = db.execute("""SELECT s.relative_path,s.document_id,s.size,s.last_seen_at FROM scan_file s
                    JOIN document_listing d ON d.document_id=s.document_id WHERE d.state='archived' ORDER BY s.last_seen_at DESC LIMIT 500""").fetchall()
            elif key == "unassigned":
                rows = db.execute("""SELECT s.relative_path,s.document_id,s.size,s.last_seen_at FROM scan_file s
                    JOIN document_listing d ON d.document_id=s.document_id
                    WHERE d.has_notes=0 AND d.has_relationships=0 ORDER BY s.last_seen_at DESC LIMIT 500""").fetchall()
            elif key in _COLLECTION_TERMS:
                rows = self._business_rows(db, _COLLECTION_TERMS[key])
            else:
                rows = db.execute("SELECT relative_path,document_id,size,last_seen_at FROM scan_file ORDER BY relative_path LIMIT 500").fetchall()
        return [self._entry(row) for row in rows]

    def _business_rows(self, db, terms: tuple[str, ...]):
        clauses = []
        params = []
        for term in terms:
            like = f"%{term.casefold()}%"
            clauses.append("(lower(s.relative_path) LIKE ? OR lower(COALESCE(x.tags,'')) LIKE ? OR lower(COALESCE(x.attributes,'')) LIKE ?)")
            params.extend([like, like, like])
        sql = """SELECT DISTINCT s.relative_path,s.document_id,s.size,s.last_seen_at
            FROM scan_file s LEFT JOIN document_search x ON x.document_id=s.document_id
            WHERE """ + " OR ".join(clauses) + " ORDER BY s.last_seen_at DESC LIMIT 500"
        try:
            return db.execute(sql, params).fetchall()
        except Exception:
            # Some stripped SQLite builds use the compatibility search table.
            fallback = " OR ".join("lower(relative_path) LIKE ?" for _ in terms)
            return db.execute(
                "SELECT relative_path,document_id,size,last_seen_at FROM scan_file WHERE " + fallback + " ORDER BY last_seen_at DESC LIMIT 500",
                [f"%{term.casefold()}%" for term in terms],
            ).fetchall()

    def _entry(self, row) -> ResourceEntry:
        path = str(row["relative_path"])
        return ResourceEntry(resource_id=path, name=Path(path).name, kind="file", path=path,
            size=int(row["size"] or 0), modified=str(row["last_seen_at"] or ""), provider=self.provider_id,
            metadata={"document_id": str(row["document_id"] or "")})

    def stat(self, resource_id: str) -> ResourceEntry:
        with self.store._db() as db:
            row = db.execute("SELECT relative_path,document_id,size,last_seen_at FROM scan_file WHERE relative_path=?", (str(resource_id),)).fetchone()
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
        return [entry for entry in self.list(path or "documents") if needle in entry.name.casefold() or needle in entry.path.casefold()][:500]

    def upload(self, *args, **kwargs): raise ProviderError("SmartView ist virtuell und nicht direkt beschreibbar")
    mkdir = upload
    delete = upload
    move = upload
