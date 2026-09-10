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


class DocumentStore(_DocumentStorePart1, _DocumentStorePart2, _DocumentStorePart3, _DocumentStorePart4, _DocumentStorePart5):
    'Filesystem store with xattrs when available and JSON sidecars otherwise.'


# Some tests and callers intentionally patch app.document_store.sha256_file.
# Keep method lookups routed through that public compatibility surface rather
# than freezing an alias in a mixin module.
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
    """Create the control files and a rebuildable index for ROOT."""
    DocumentStore(root).initialize()
    click.echo(f"Document store initialized: {root}")


@click.command("scan-documents")
@click.option("--root", type=click.Path(path_type=Path), default=None, help="Document root; defaults to SIMPLEOFFICE_DOCUMENT_ROOT.")
@click.option(
    "--verify-hashes",
    is_flag=True,
    help="Recalculate every SHA-256 checksum even when size and modification time are unchanged.",
)
@with_appcontext
def scan_documents_command(root: Path | None, verify_hashes: bool) -> None:
    """Scan documents and update the repairable index."""
    store = DocumentStore(root or current_app.config["DOCUMENT_ROOT"])
    report = store.scan(verify_hashes=verify_hashes)
    click.echo(
        f"files={report.files} new={report.new_files} updated={report.updated_files} duplicates={report.duplicates} "
        f"symlinks={report.symlinks} boundaries={report.skipped_boundaries} errors={report.errors}"
    )


@click.command("document-note")
@click.argument("document")
@click.argument("text")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_note_command(document: str, text: str, actor: str) -> None:
    """Add TEXT as a note to DOCUMENT (ID or relative file path)."""
    note = DocumentStore(current_app.config["DOCUMENT_ROOT"]).add_note(document, text, actor)
    click.echo(note["id"])


@click.command("document-state")
@click.argument("document")
@click.argument("state")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_state_command(document: str, state: str, actor: str) -> None:
    """Set the human workflow STATE of DOCUMENT."""
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
    """Link SOURCE to TARGET for the document mindmap."""
    link = DocumentStore(current_app.config["DOCUMENT_ROOT"]).add_link(source, target, relation_type, label, actor)
    click.echo(link["id"])


@click.command("document-graph")
@click.argument("document")
@with_appcontext
def document_graph_command(document: str) -> None:
    """Print graph data for DOCUMENT as JSON."""
    graph = DocumentStore(current_app.config["DOCUMENT_ROOT"]).graph(document)
    click.echo(json.dumps(graph, ensure_ascii=False, indent=2))


@click.command("document-attribute")
@click.argument("document")
@click.argument("key")
@click.argument("value")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_attribute_command(document: str, key: str, value: str, actor: str) -> None:
    """Set a freely modelled KEY/VALUE attribute on DOCUMENT."""
    DocumentStore(current_app.config["DOCUMENT_ROOT"]).set_attribute(document, key, value, actor)
    click.echo(key)


@click.command("document-deadline")
@click.argument("document")
@click.argument("expires_at")
@click.option("--kind", type=click.Choice(["retention", "work"]), default="retention")
@click.option("--label", default="")
@click.option("--user", "actor", required=True)
@with_appcontext
def document_deadline_command(
    document: str, expires_at: str, kind: str, label: str, actor: str
) -> None:
    """Append one retention or work deadline to DOCUMENT."""
    deadline = DocumentStore(current_app.config["DOCUMENT_ROOT"]).add_deadline(
        document, kind, expires_at, label, actor
    )
    click.echo(json.dumps(deadline, ensure_ascii=False))


@click.command("retention-status")
@click.argument("document")
@with_appcontext
def retention_status_command(document: str) -> None:
    """Explain every direct, inherited and transitive deadline."""
    status = DocumentStore(current_app.config["DOCUMENT_ROOT"]).retention_status(document)
    click.echo(json.dumps(status, ensure_ascii=False, indent=2))


@click.command("retention-cleanup")
@click.option("--destination", default="Aussonderung", show_default=True)
@click.option("--apply", is_flag=True, help="Move eligible files after an explicit confirmation.")
@click.option("--confirm", default="", help="Required value for --apply: AUSSONDERN")
@click.option("--user", "actor", required=True)
@with_appcontext
def retention_cleanup_command(destination: str, apply: bool, confirm: str, actor: str) -> None:
    """Preview cleanup candidates or move them; never delete document files."""
    if apply and confirm != "AUSSONDERN":
        raise click.UsageError("--apply requires --confirm AUSSONDERN")
    result = DocumentStore(current_app.config["DOCUMENT_ROOT"]).cleanup_expired(
        destination, actor, apply=apply
    )
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@click.command("search-documents")
@click.argument("query")
@click.option("--limit", default=50, show_default=True)
@with_appcontext
def search_documents_command(query: str, limit: int) -> None:
    """Search document paths, states, tags, notes and domain attributes."""
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
