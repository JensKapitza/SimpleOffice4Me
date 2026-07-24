"""Compatibility commands for importing files safely into the document store."""

from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from .document_store import DocumentStore


@click.command("import-file")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--version-of", default=None, help="Existing document ID or relative path.")
@with_appcontext
def import_file(file: Path, version_of: str | None) -> None:
    """Copy FILE into the inbox; OCR is deliberately handled by a later worker."""
    store = DocumentStore(current_app.config["DOCUMENT_ROOT"])
    if version_of:
        version = store.import_version(file, version_of)
        click.echo(f"imported-version={version['document_id']} number={version['version_number']}")
        return
    target = store.import_file(file)
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
