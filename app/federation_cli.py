"""Operational CLI commands for unattended federation synchronization."""
from __future__ import annotations

import json

import click
from flask import current_app

from .document_origin import persist_origin_tags
from .federation_download_worker import process_queue, sync_peer_catalog
from .federation_store import FederationStore


@click.command("federation-sync-indexes")
@click.option("--peer", "peer_ids", multiple=True, help="Only sync the selected peer ID; repeatable.")
@click.option("--page-size", default=500, type=click.IntRange(1, 1000), show_default=True)
def sync_indexes_command(peer_ids: tuple[str, ...], page_size: int) -> None:
    """Refresh remote file indexes while retaining the last good offline copy."""
    root = current_app.config["DOCUMENT_ROOT"]
    store = FederationStore(root)
    selected = set(peer_ids)
    peers = [
        peer for peer in store.list_peers()
        if peer.get("enabled") and (not selected or peer["peer_id"] in selected)
    ]
    results, errors = [], []
    for peer in peers:
        try:
            results.append(sync_peer_catalog(root, peer["peer_id"], page_size=page_size))
        except Exception as exc:  # individual peers must not block the remainder
            errors.append({"peer_id": peer["peer_id"], "error": str(exc)[:1000]})
    click.echo(json.dumps({"synced": results, "errors": errors}, ensure_ascii=False))
    if errors and not results:
        raise click.ClickException("all selected federation index synchronizations failed")


@click.command("federation-run-downloads")
@click.option("--limit", default=10, type=click.IntRange(1, 100), show_default=True)
def run_downloads_command(limit: int) -> None:
    """Run the dynamic priority queue once; suitable for cron/systemd timers."""
    result = process_queue(current_app.config["DOCUMENT_ROOT"], limit=limit)
    click.echo(json.dumps(result, ensure_ascii=False))
    if result["failed"] and not result["completed"] and not result["deferred"]:
        raise click.ClickException("federation download queue completed only with errors")


@click.command("federation-backfill-origin-tags")
@click.option("--actor", default="system:federation", show_default=True)
def backfill_origin_tags_command(actor: str) -> None:
    """Persist known document provenance as ordinary searchable tags."""
    result = persist_origin_tags(current_app.config["DOCUMENT_ROOT"], actor=actor)
    click.echo(json.dumps(result, ensure_ascii=False))
    if result["errors"] and not result["changed"]:
        raise click.ClickException("origin-tag backfill completed only with errors")


def init_app(app) -> None:
    app.cli.add_command(sync_indexes_command)
    app.cli.add_command(run_downloads_command)
    app.cli.add_command(backfill_origin_tags_command)
