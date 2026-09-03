"""Mail search-index, duplicate review, federation recovery and admin cleanup routes."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .federation_mail import (
    MailFederationPolicy,
    MailFederationStore,
    discover_missing,
    recover_source,
)
from .mail_client import MailStore
from .mail_index import MailGroupMutator, MailIndexer, MailSearchIndex
from .mail_webclient import ImapWebClient, MailAccountPolicy, MailReadOnlyError

bp = Blueprint("mail_index", __name__, url_prefix="/documents/mail/index")


def _actor() -> str:
    return str(g.user["username"])


def _store() -> MailStore:
    secret = current_app.config["SECRET_KEY"]
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return MailStore(current_app.config["DOCUMENT_ROOT"], raw)


def _selection(store: MailStore):
    accounts = store.accounts(_actor())
    account_id = request.values.get("account", "").strip()
    selected = next((row for row in accounts if row["id"] == account_id), accounts[0] if accounts else None)
    return accounts, selected


@bp.get("")
@login_required
def index():
    store = _store()
    accounts, selected = _selection(store)
    query = request.args.get("q", "").strip()[:300]
    groups: list[dict] = []
    search_rows: list[dict] = []
    folders: list[dict] = []
    federation_rows: list[dict] = []
    federation_export_enabled = False
    stats = {"total": 0, "present": 0, "missing": 0, "last_seen": "", "fts5": False}
    read_only = True
    connection_error = ""
    if selected:
        search = MailSearchIndex(store)
        stats = search.stats(_actor(), selected["id"])
        groups = search.duplicate_groups(_actor(), selected["id"])
        if query:
            search_rows = search.search(_actor(), selected["id"], query, include_missing=True, limit=300)
        federation_export_enabled = MailFederationPolicy(current_app.config["DOCUMENT_ROOT"]).export_enabled(
            _actor(), selected["id"]
        )
        sources = MailFederationStore(current_app.config["DOCUMENT_ROOT"]).list_sources(
            _actor(), selected["id"], limit=1000
        )
        by_row: dict[int, list[dict]] = {}
        for source in sources:
            by_row.setdefault(int(source["message_row_id"]), []).append(source)
        if by_row:
            indexed = {
                int(row["id"]): row
                for row in search.rows_by_ids(_actor(), selected["id"], by_row.keys())
            }
            federation_rows = [
                {"row": indexed[row_id], "sources": rows}
                for row_id, rows in by_row.items()
                if row_id in indexed
            ]
            federation_rows.sort(
                key=lambda item: (
                    int(item["row"].get("present") or 0),
                    max((int(source.get("confidence") or 0) for source in item["sources"]), default=0),
                ),
                reverse=True,
            )
        read_only = MailAccountPolicy(store).read_only(_actor(), selected["id"])
        try:
            account = store.account(_actor(), selected["id"])
            folders = ImapWebClient(store).folders(account)
        except Exception as exc:
            current_app.logger.warning("Mail index folder list failed for %s: %s", _actor(), type(exc).__name__)
            connection_error = f"Serverordner konnten nicht geladen werden ({type(exc).__name__}). Der lokale Index bleibt nutzbar."
    return render_template(
        "documents/mail_duplicates.html",
        accounts=accounts,
        selected=selected,
        groups=groups,
        search_rows=search_rows,
        query=query,
        stats=stats,
        folders=folders,
        read_only=read_only,
        connection_error=connection_error,
        federation_rows=federation_rows,
        federation_export_enabled=federation_export_enabled,
    )


@bp.post("/refresh")
@login_required
def refresh():
    store = _store()
    account_id = request.form.get("account", "").strip()
    per_folder = max(1, min(request.form.get("per_folder", 100, type=int) or 100, 500))
    try:
        account = store.account(_actor(), account_id)
        result = MailIndexer(store).refresh_account(_actor(), account, per_folder=per_folder)
        flash(
            f"Mailindex aktualisiert: {result['indexed']} neue Mails, {result['missing']} nicht mehr gefundene Ziele markiert, "
            f"{result['errors']} Fehler in {result['folders']} Ordnern."
        )
    except Exception as exc:
        current_app.logger.warning("Mail index refresh failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Mailindex konnte nicht vollständig aktualisiert werden ({type(exc).__name__}).")
    return redirect(url_for("mail_index.index", account=account_id))


def _row_ids() -> list[int]:
    values: list[int] = []
    for raw in request.form.getlist("row_id"):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.append(value)
    return sorted(set(values))[:2000]


@bp.post("/group/move")
@login_required
def move_group():
    store = _store()
    account_id = request.form.get("account", "").strip()
    target = request.form.get("target", "").strip()
    try:
        account = store.account(_actor(), account_id)
        result = MailGroupMutator(store).move(_actor(), account, _row_ids(), target)
        flash(
            f"Gruppe verschoben: {result['moved']} Mails; {result['stale']} veraltete Indexziele markiert; "
            f"{len(result['errors'])} Fehler."
        )
    except MailReadOnlyError as exc:
        flash(str(exc))
    except Exception as exc:
        current_app.logger.warning("Mail duplicate group move failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Gruppe konnte nicht verschoben werden ({type(exc).__name__}).")
    return redirect(url_for("mail_index.index", account=account_id))


@bp.post("/group/delete")
@login_required
def delete_group():
    store = _store()
    account_id = request.form.get("account", "").strip()
    try:
        account = store.account(_actor(), account_id)
        result = MailGroupMutator(store).delete(_actor(), account, _row_ids())
        flash(
            f"Gruppe gelöscht: {result['deleted']} Mails; {result['stale']} veraltete Indexziele markiert; "
            f"{len(result['errors'])} Fehler."
        )
    except MailReadOnlyError as exc:
        flash(str(exc))
    except Exception as exc:
        current_app.logger.warning("Mail duplicate group delete failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Gruppe konnte nicht gelöscht werden ({type(exc).__name__}).")
    return redirect(url_for("mail_index.index", account=account_id))


@bp.post("/federation/export")
@login_required
def federation_export():
    store = _store()
    account_id = request.form.get("account", "").strip()
    enabled = request.form.get("enabled") == "1"
    try:
        # Ownership is mandatory; an administrator cannot silently export another
        # user's mailbox through this owner-facing endpoint.
        store._owned_row(_actor(), account_id)
        MailFederationPolicy(current_app.config["DOCUMENT_ROOT"]).set_export(
            _actor(), account_id, enabled, _actor()
        )
        store.history.record(
            "mail_federation_export_changed", _actor(), "mail-accounts", account_id,
            {"export_enabled": enabled},
        )
        flash(
            "Mailkonto ist für authentifizierte Federation-Fingerprint-Suche und explizite EML-Wiederherstellung freigegeben."
            if enabled else
            "Mail-Federation-Freigabe wurde deaktiviert. Bereits lokal gespeicherte Remote-Quellhinweise bleiben erhalten."
        )
    except Exception as exc:
        current_app.logger.warning("Mail federation export toggle failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Federation-Freigabe konnte nicht geändert werden ({type(exc).__name__}).")
    return redirect(url_for("mail_index.index", account=account_id))


@bp.post("/federation/discover")
@login_required
def federation_discover():
    store = _store()
    account_id = request.form.get("account", "").strip()
    try:
        store._owned_row(_actor(), account_id)
        result = discover_missing(
            current_app.config["DOCUMENT_ROOT"], _actor(), account_id, row_ids=_row_ids() or None
        )
        store.history.record(
            "mail_federation_discovery", _actor(), "mail-accounts", account_id,
            {"queried": result["queried"], "peers": result["peers"], "matches": result["matches"], "errors": len(result["errors"])},
        )
        flash(
            f"Federation-Suche: {result['queried']} fehlende Mailziele geprüft, "
            f"{result['matches']} Quellen auf {result['peers']} Peer(s) gefunden; {len(result['errors'])} Peer-Fehler."
        )
    except Exception as exc:
        current_app.logger.warning("Mail federation discovery failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Federation-Suche fehlgeschlagen ({type(exc).__name__}).")
    return redirect(url_for("mail_index.index", account=account_id))


@bp.post("/federation/recover/<int:source_id>")
@login_required
def federation_recover(source_id: int):
    store = _store()
    account_id = request.form.get("account", "").strip()
    try:
        store._owned_row(_actor(), account_id)
        result = recover_source(
            current_app.config["DOCUMENT_ROOT"], store, _actor(), account_id, source_id
        )
        flash(
            f"Mail von Federation-Peer {result['peer_id']} verifiziert und "
            f"{'bereits vorhandenes Archiv bestätigt' if result['duplicate'] else 'ins private Mailarchiv übernommen'}."
        )
    except Exception as exc:
        current_app.logger.warning("Mail federation recovery failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Federation-Wiederherstellung fehlgeschlagen ({type(exc).__name__}).")
    return redirect(url_for("mail_index.index", account=account_id))


@bp.post("/cleanup-missing")
@login_required
def cleanup_missing():
    if not is_admin(g.user):
        abort(403)
    store = _store()
    account_id = request.form.get("account", "").strip()
    try:
        removed = MailSearchIndex(store).cleanup_missing(_actor(), account_id)
        store.history.record(
            "mail_index_tombstones_cleaned", _actor(), "mail-accounts", account_id,
            {"removed": removed},
        )
        flash(f"Administrator-Aufräumen abgeschlossen: {removed} nicht mehr gefundene Indexziele endgültig entfernt.")
    except Exception as exc:
        current_app.logger.warning("Mail tombstone cleanup failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Index-Aufräumen fehlgeschlagen ({type(exc).__name__}).")
    return redirect(url_for("mail_index.index", account=account_id))
