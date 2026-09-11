"""File based document storage and repairable scan index.

The implementation is split into focused mixins to keep every source file below
the maintenance limit while preserving the public app.document_store API.
"""
from __future__ import annotations

import re

from . import document_store_core as _core
from .document_store_core import *  # noqa: F401,F403
from . import document_store_part_1 as _part_module_1
from .document_store_part_1 import _DocumentStorePart1
from . import document_store_part_2 as _part_module_2
from .document_store_part_2 import _DocumentStorePart2
from . import document_store_part_3 as _part_module_3
from .document_store_part_3 import _DocumentStorePart3
from . import document_store_part_4 as _part_module_4
from .document_store_part_4 import _DocumentStorePart4
from . import document_store_part_5 as _part_module_5
from .document_store_part_5 import _DocumentStorePart5
from .safe_paths import normalize_path, relative_under, resolve_file_under, resolve_under

_SAFE_DOCUMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


class DocumentStore(_DocumentStorePart1, _DocumentStorePart2, _DocumentStorePart3, _DocumentStorePart4, _DocumentStorePart5):
    """Filesystem store with a validated public document lookup boundary."""

    @staticmethod
    def _request_actor() -> tuple[str, bool] | None:
        try:
            from flask import g, has_request_context
        except ImportError:
            return None
        if not has_request_context():
            return None
        user = getattr(g, "user", None)
        if user is None:
            return None
        try:
            return str(user["username"]), bool(user["is_admin"])
        except (KeyError, TypeError, IndexError):
            return None

    def _visible_for_request(self, metadata: dict[str, Any]) -> bool:
        actor = self._request_actor()
        if actor is None:
            return True
        from .chat_access import document_visible
        return document_visible(metadata, actor[0], actor[1])

    def _filter_request_documents(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._request_actor() is None:
            return documents
        return [item for item in documents if self._visible_for_request(item)]

    def _all_documents(self) -> list[dict[str, Any]]:
        return self._filter_request_documents(super()._all_documents())

    def list_documents(self) -> list[dict[str, Any]]:
        return sorted(self._all_documents(), key=lambda item: (item.get("last_seen_at", ""), item.get("last_path", "")), reverse=True)

    def document_page(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        result = super().document_page(page=page, page_size=page_size)
        result["documents"] = self._filter_request_documents(list(result.get("documents", [])))
        return result

    def search_page(self, query: str, page: int = 1, page_size: int = 25) -> dict[str, Any]:
        result = super().search_page(query, page=page, page_size=page_size)
        if self._request_actor() is None:
            return result
        visible = []
        for item in result.get("results", []):
            metadata = self._read_json(self.documents / f"{item.get('document_id', '')}.json", {})
            if metadata.get("document_id") and self._visible_for_request(metadata):
                visible.append(item)
        result["results"] = visible
        return result

    def get_document(self, reference: str | Path) -> dict[str, Any]:
        """Resolve document IDs and path references and enforce browser visibility."""
        self.initialize()
        raw = str(reference or "")
        root = normalize_path(self.root, strict=True)
        safe_reference: str | Path | None = None
        try:
            requested = Path(reference).expanduser()
            if requested.is_absolute():
                candidate = normalize_path(requested, strict=False)
                candidate.relative_to(root)
            else:
                candidate = resolve_under(root, raw, strict=False)
            relative = candidate.relative_to(root).as_posix()
            if candidate.exists():
                if not candidate.is_file():
                    raise ValueError("document path does not name a regular file")
                safe_reference = resolve_file_under(root, relative)
            else:
                with self._db() as db:
                    row = db.execute("SELECT document_id FROM scan_file WHERE relative_path = ?", (relative,)).fetchone()
                if row and _SAFE_DOCUMENT_ID.fullmatch(str(row[0])):
                    safe_reference = str(row[0])
        except (OSError, ValueError):
            path_like = Path(raw).is_absolute() or "/" in raw or "\\" in raw or raw in {".", ".."} or raw.startswith(".")
            if path_like:
                raise ValueError("document path is outside the managed store") from None
        if safe_reference is None:
            if not _SAFE_DOCUMENT_ID.fullmatch(raw) or raw in {".", ".."}:
                raise ValueError("invalid document reference")
            safe_reference = raw
        metadata = super().get_document(safe_reference)
        last_path = str(metadata.get("last_path", "") or "")
        if last_path and not last_path.startswith("[external]"):
            try:
                metadata["last_path"] = relative_under(self.root, last_path, require_name=True).as_posix()
            except (OSError, ValueError) as exc:
                raise ValueError("document metadata contains an unsafe path") from exc
        if not self._visible_for_request(metadata):
            raise ValueError("unknown document")
        return metadata

    def create_share(self, reference: str | Path, password: str, expires_days: int, actor: str, note_id: str = "") -> dict[str, Any]:
        document = self.get_document(reference)
        from .chat_access import is_private_chat_document
        if is_private_chat_document(document):
            raise ValueError("Chat-private Anhänge können nicht über einen öffentlichen Freigabelink geteilt werden")
        return super().create_share(reference, password, expires_days, actor, note_id)

    def open_share(self, share_id: str, password: str, remote_addr: str = "") -> dict[str, Any]:
        result = super().open_share(share_id, password, remote_addr)
        from .chat_access import is_private_chat_document
        if is_private_chat_document(result.get("document", {})):
            raise ValueError("Chat-private Anhänge sind nicht über öffentliche Freigabelinks verfügbar")
        return result


def _sha256_file_proxy(path):
    return sha256_file(path)


_core.DocumentStore = DocumentStore
_part_module_1.DocumentStore = DocumentStore
_part_module_1.sha256_file = _sha256_file_proxy
_part_module_2.DocumentStore = DocumentStore
_part_module_2.sha256_file = _sha256_file_proxy
_part_module_3.DocumentStore = DocumentStore
_part_module_3.sha256_file = _sha256_file_proxy
_part_module_4.DocumentStore = DocumentStore
_part_module_4.sha256_file = _sha256_file_proxy
_part_module_5.DocumentStore = DocumentStore
_part_module_5.sha256_file = _sha256_file_proxy


@click.command("init-document-store")
@click.argument("root", type=click.Path(path_type=Path))
def init_document_store_command(root: Path) -> None:
    DocumentStore(root).initialize()
    click.echo(f"Document store initialized: {root}")


@click.command("scan-documents")
@click.option("--root", type=click.Path(path_type=Path), default=None, help="Document root; defaults to SIMPLEOFFICE_DOCUMENT_ROOT.")
@click.option("--verify-hashes", is_flag=True, help="Recalculate every SHA-256 checksum even when size and modification time are unchanged.")
@with_appcontext
def scan_documents_command(root: Path | None, verify_hashes: bool) -> None:
    store = DocumentStore(root or current_app.config["DOCUMENT_ROOT"])
    report = store.scan(verify_hashes=verify_hashes)
    click.echo(f"files={report.files} new={report.new_files} updated={report.updated_files} duplicates={report.duplicates} symlinks={report.symlinks} boundaries={report.skipped_boundaries} errors={report.errors}")


@click.command("document-note")
@click.argument("document")
@click.argument("text")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_note_command(document: str, text: str, actor: str) -> None:
    note = DocumentStore(current_app.config["DOCUMENT_ROOT"]).add_note(document, text, actor)
    click.echo(note["id"])


@click.command("document-state")
@click.argument("document")
@click.argument("state")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_state_command(document: str, state: str, actor: str) -> None:
    changed = DocumentStore(current_app.config["DOCUMENT_ROOT"]).set_state(document, state, actor)
    click.echo(json.dumps(changed, ensure_ascii=False))


@click.command("document-link")
@click.argument("source")
@click.argument("target")
@click.option("--type", "relation_type", default="related", show_default=True)
@click.option("--label", default="")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_link_command(source: str, target: str, relation_type: str, label: str, actor: str) -> None:
    link = DocumentStore(current_app.config["DOCUMENT_ROOT"]).add_link(source, target, relation_type, label, actor)
    click.echo(link["id"])


@click.command("document-graph")
@click.argument("document")
@with_appcontext
def document_graph_command(document: str) -> None:
    graph = DocumentStore(current_app.config["DOCUMENT_ROOT"]).graph(document)
    click.echo(json.dumps(graph, ensure_ascii=False, indent=2))


@click.command("document-attribute")
@click.argument("document")
@click.argument("key")
@click.argument("value")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_attribute_command(document: str, key: str, value: str, actor: str) -> None:
    DocumentStore(current_app.config["DOCUMENT_ROOT"]).set_attribute(document, key, value, actor)
    click.echo(key)


@click.command("document-deadline")
@click.argument("document")
@click.argument("expires_at")
@click.option("--kind", type=click.Choice(["retention", "work"]), default="retention")
@click.option("--label", default="")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_deadline_command(document: str, expires_at: str, kind: str, label: str, actor: str) -> None:
    deadline = DocumentStore(current_app.config["DOCUMENT_ROOT"]).add_deadline(document, kind, expires_at, label, actor)
    click.echo(json.dumps(deadline, ensure_ascii=False))


@click.command("retention-status")
@click.argument("document")
@with_appcontext
def retention_status_command(document: str) -> None:
    status = DocumentStore(current_app.config["DOCUMENT_ROOT"]).retention_status(document)
    click.echo(json.dumps(status, ensure_ascii=False, indent=2))


@click.command("retention-cleanup")
@click.option("--destination", default="Aussonderung", show_default=True)
@click.option("--apply", is_flag=True, help="Move eligible files after an explicit confirmation.")
@click.option("--confirm", default="", help="Required value for --apply: AUSSONDERN")
@click.option("--user", "actor", required=True)
@with_appcontext
def retention_cleanup_command(destination: str, apply: bool, confirm: str, actor: str) -> None:
    if apply and confirm != "AUSSONDERN":
        raise click.UsageError("--apply requires --confirm AUSSONDERN")
    result = DocumentStore(current_app.config["DOCUMENT_ROOT"]).cleanup_expired(destination, actor, apply=apply)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@click.command("search-documents")
@click.argument("query")
@click.option("--limit", default=50, show_default=True)
@with_appcontext
def search_documents_command(query: str, limit: int) -> None:
    results = DocumentStore(current_app.config["DOCUMENT_ROOT"]).search(query, limit)
    click.echo(json.dumps(results, ensure_ascii=False, indent=2))


def init_app(app: Any) -> None:
    app.cli.add_command(init_document_store_command)
    app.cli.add_command(scan_documents_command)
    app.cli.add_command(document_note_command)
    app.cli.add_command(document_state_command)
    app.cli.add_command(document_link_command)
    app.cli.add_command(document_graph_command)
    app.cli.add_command(document_attribute_command)
    app.cli.add_command(document_deadline_command)
    app.cli.add_command(retention_status_command)
    app.cli.add_command(retention_cleanup_command)
    app.cli.add_command(search_documents_command)
    from . import chat_routes, federation_chat_http
    if "chat" not in app.blueprints:
        app.register_blueprint(chat_routes.bp)
    if "federation_chat" not in app.blueprints:
        app.register_blueprint(federation_chat_http.bp)
