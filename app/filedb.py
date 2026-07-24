"""Compatibility commands for importing files safely into the document store."""

from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from .document_store import DocumentStore


@click.command("import-file")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--version-of", default=None, help="Existing document ID or relative path.")
@click.option("--user", "actor", required=True, help="User performing the write action.")
@with_appcontext
def import_file(file: Path, version_of: str | None, actor: str) -> None:
    """Copy FILE into the inbox; OCR is deliberately handled by a later worker."""
    store = DocumentStore(current_app.config["DOCUMENT_ROOT"])
    if version_of:
        matches = store.find_matches(version_of)
        if not matches:
            raise click.ClickException(f"Kein Dokument passt zu: {version_of}")
        click.echo("Mögliche Vorgängerversionen:")
        for number, match in enumerate(matches, start=1):
            click.echo(f"{number}: {match['path']} | Zustand={match['state']} | Version={match['version']} | ID={match['document_id']}")
        selection = click.prompt("Passende Vorgängerversion", type=click.IntRange(1, len(matches)))
        selected = matches[selection - 1]
        if not click.confirm(f"{selected['path']} wirklich als Vorgängerversion übernehmen?", default=False):
            raise click.Abort()
        version = store.import_version(file, selected["document_id"], actor)
        click.echo(f"imported-version={version['document_id']} number={version['version_number']}")
        return
    target = store.import_file(file, actor)
    report = store.scan()
    click.echo(
        f"imported={target} files={report.files} duplicates={report.duplicates} "
        f"symlinks={report.symlinks} boundaries={report.skipped_boundaries}"
    )


@click.command("list-files")
@click.option("--root", type=click.Path(path_type=Path), default=None)
@with_appcontext
def list_files(root: Path | None) -> None:
    """List indexed document paths from the current filesystem store."""
    store = DocumentStore(root or current_app.config["DOCUMENT_ROOT"])
    store.initialize()
    with store._db() as db:
        for (relative_path,) in db.execute("SELECT relative_path FROM scan_file ORDER BY relative_path"):
            click.echo(relative_path)


def init_app(app):
    app.cli.add_command(list_files)
    app.cli.add_command(import_file)
