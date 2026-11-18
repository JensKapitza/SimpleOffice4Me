"""File based document storage and repairable scan index.

The implementation is split into focused mixins to keep every source file below
the maintenance limit while preserving the public app.document_store API.
"""
from __future__ import annotations

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
from .safe_paths import relative_under


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
        from .mail_document_access import mail_document_visible
        from flask import request
        sending = request.endpoint == "mail_reader.send_case_draft" and request.method == "POST"
        view_args = request.view_args or {}
        read_case = ""
        read_id = ""
        if request.method == "GET" and request.endpoint == "mail_reader.case_attachment":
            read_case = str(view_args.get("case_id", ""))
            read_id = str(view_args.get("archive_id", ""))
        elif request.method == "GET" and request.endpoint == "mail_reader.index":
            read_case = request.args.get("case", "")
            read_id = request.args.get("case_mail", "")
        return document_visible(metadata, actor[0], actor[1]) and mail_document_visible(
            metadata, actor[0], self.root,
            case_context=request.blueprint in {"mail_reader", "mail_client"},
            archive_send_case=str(view_args.get("case_id", "")) if sending else "",
            archive_send_draft=str(view_args.get("draft_id", "")) if sending else "",
            archive_read_case=read_case, archive_read_id=read_id,
        )

    def _filter_request_documents(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._request_actor() is None:
            return documents
        return [item for item in documents if self._visible_for_request(item)]

    def _all_documents(self) -> list[dict[str, Any]]:
        return self._filter_request_documents(super()._all_documents())

    def list_documents(self) -> list[dict[str, Any]]:
        return sorted(self._all_documents(), key=lambda item: (item.get("last_seen_at", ""), item.get("last_path", "")), reverse=True)

    def _visible_document_page(self, statement: str, page: int, page_size: int) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(500, int(page_size)))
        start = (page - 1) * page_size
        end = start + page_size
        visible_total = 0
        documents: list[dict[str, Any]] = []
        self.initialize()
        with self._db() as db:
            rows = db.execute(statement)
            for (document_id,) in rows:
                metadata = self._read_json(self.documents / f"{document_id}.json", {})
                if not metadata.get("document_id") or not self._visible_for_request(metadata):
                    continue
                if start <= visible_total <= end:
                    documents.append(metadata)
                visible_total += 1
                if visible_total > end:
                    break
        has_next = visible_total > end
        return {
            "documents": documents[:page_size],
            "page": page,
            "page_size": page_size,
            # A scoped count needs a complete visibility scan. Keep page loads
            # bounded and report an exact count only when this cursor exhausts.
            "total": None if has_next else visible_total,
            "has_next": has_next,
        }

    def document_page(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        if self._request_actor() is None:
            return super().document_page(page=page, page_size=page_size)
        return self._visible_document_page(
            "SELECT document_id FROM scan_file GROUP BY document_id "
            "ORDER BY MAX(last_seen_at) DESC, MAX(relative_path)",
            page, page_size,
        )

    def inbox_page(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        if self._request_actor() is None:
            return super().inbox_page(page=page, page_size=page_size)
        return self._visible_document_page(
            "SELECT document_id FROM document_listing "
            "WHERE state='new' AND has_notes=0 AND has_relationships=0 "
            "ORDER BY last_seen_at DESC, path",
            page, page_size,
        )

    def search_page(self, query: str, page: int = 1, page_size: int = 25) -> dict[str, Any]:
        if self._request_actor() is None:
            return super().search_page(query, page=page, page_size=page_size)
        query = query.strip()
        if not query:
            return super().search_page(query, page=page, page_size=page_size)

        # One extra visible match establishes whether the next page exists.
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        visible_start = (page - 1) * page_size
        visible_end = visible_start + page_size
        results: list[dict[str, Any]] = []
        visible_count = 0
        self.initialize()
        with self._db() as db:
            for row in self._search_cursor(db, query):
                item = {"document_id": row[0], "path": row[1], "state": row[2]}
                metadata = self._read_json(
                    self.documents / f"{item['document_id']}.json", {}
                )
                if not metadata.get("document_id") or not self._visible_for_request(metadata):
                    continue
                if visible_start <= visible_count <= visible_end:
                    results.append(item)
                visible_count += 1
                if len(results) > page_size:
                    break

        has_next = len(results) > page_size
        return {
            "results": results[:page_size],
            "page": page,
            "page_size": page_size,
            "has_next": has_next,
        }

    def _visible_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Relativize ``last_path`` and enforce browser visibility."""
        last_path = str(metadata.get("last_path", "") or "")
        if last_path and not last_path.startswith("[external]"):
            try:
                metadata["last_path"] = relative_under(self.root, last_path, require_name=True).as_posix()
            except (OSError, ValueError) as exc:
                raise ValueError("document metadata contains an unsafe path") from exc
        if not self._visible_for_request(metadata):
            raise ValueError("unknown document")
        return metadata

    def get_document(self, reference: str | Path) -> dict[str, Any]:
        """Resolve document IDs and registered paths, enforce visibility.

        Lookup only: the reference is an opaque catalog key and never
        touches the filesystem (unregistered files raise ``ValueError``).
        """
        self.initialize()
        return self._visible_metadata(super().get_document(reference))

    def scan_and_get_document(self, path: str | Path) -> dict[str, Any]:
        """Scan an app-produced file, with the same visibility check."""
        self.initialize()
        return self._visible_metadata(super().scan_and_get_document(path))

    def create_share(self, reference: str | Path, password: str, expires_days: int, actor: str, note_id: str = "") -> dict[str, Any]:
        document = self.get_document(reference)
        from .mail_document_access import private_mail_document
        if private_mail_document(document):
            raise ValueError("Private Maildateien können nicht über öffentliche Freigabelinks geteilt werden")
        from .chat_access import is_private_chat_document
        if is_private_chat_document(document):
            raise ValueError("Chat-private Anhänge können nicht über einen öffentlichen Freigabelink geteilt werden")
        return super().create_share(reference, password, expires_days, actor, note_id)

    def open_share(self, share_id: str, password: str, remote_addr: str = "") -> dict[str, Any]:
        result = super().open_share(share_id, password, remote_addr)
        from .mail_document_access import private_mail_document
        if private_mail_document(result.get("document", {})):
            raise ValueError("Private Maildateien sind nicht über öffentliche Freigabelinks verfügbar")
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
    from . import chat_policy_admin, chat_routes, federation_chat_http, google_drive_admin, google_drive_schema
    google_drive_schema.init_app(app)
    if "google_drive_admin" not in app.blueprints:
        app.register_blueprint(google_drive_admin.bp)
    if "chat" not in app.blueprints:
        app.register_blueprint(chat_routes.bp)
    if "federation_chat" not in app.blueprints:
        app.register_blueprint(federation_chat_http.bp)
    if "chat_policy_admin" not in app.blueprints:
        app.register_blueprint(chat_policy_admin.bp)
