"""Web UI for finance accounts and safe bank statement import."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .document_store import CONTROL_DIR
from .finance_statements import FinanceStatementImporter
from .finance_store import FinanceStore\nfrom .finance_fints import FinTSAuthenticationRequired, FinTSUnavailable, discover_accounts\nfrom .finance_fints_sync import FinTSSyncService\nfrom .finance_match_adapters import proposals_for_transaction
from .safe_paths import safe_filename

bp = Blueprint("finance", __name__, url_prefix="/finances")
_STAGE_TTL_SECONDS = 24 * 60 * 60
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _root() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]).expanduser().resolve()


def _actor() -> str:
    return str(g.user["username"])


def _store() -> FinanceStore:
    return FinanceStore(_root())


def _stage_dir(actor: str) -> Path:
    owner = hashlib.sha256(actor.encode("utf-8")).hexdigest()
    path = _root() / CONTROL_DIR / "finance-import-staging" / owner
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup_staging(actor: str) -> None:
    cutoff = time.time() - _STAGE_TTL_SECONDS
    for path in _stage_dir(actor).glob("*.bin"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _stage_upload(data: bytes, actor: str) -> str:
    if not data:
        raise ValueError("Kontoauszug ist leer")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise ValueError("Kontoauszug ist größer als 25 MiB")
    digest = hashlib.sha256(data).hexdigest()
    directory = _stage_dir(actor)
    target = directory / f"{digest}.bin"
    if not target.exists():
        temp = directory / f".{digest}.{os.getpid()}.tmp"
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    return digest


def _read_stage(token: str, actor: str) -> bytes:
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError("Import-Token ist ungültig")
    path = _stage_dir(actor) / f"{token}.bin"
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError("Importvorschau ist abgelaufen; Datei bitte erneut auswählen") from exc
    if hashlib.sha256(data).hexdigest() != token:
        raise ValueError("Zwischengespeicherter Kontoauszug ist beschädigt")
    return data


@bp.get("")
@login_required
def index():
    actor = _actor()
    _cleanup_staging(actor)
    store = _store()
    accounts = store.accounts(actor)
    selected = request.args.get("account_id", "").strip()
    if selected and selected not in {row["account_id"] for row in accounts}:
        selected = ""
    transactions = store.bank_transactions(selected, actor, limit=100) if selected else []
    return render_template(
        "finance/index.html",
        accounts=accounts,
        selected_account_id=selected,
        transactions=transactions,
    )


@bp.post("/accounts")
@login_required
def create_account():
    try:
        account = _store().create_account(request.form.to_dict(), _actor())
    except ValueError as exc:
        flash(f"Konto nicht angelegt: {exc}")
        return redirect(url_for(".index"))
    flash(f"Konto „{account['name']}“ wurde angelegt.")
    return redirect(url_for(".index", account_id=account["account_id"]))


@bp.post("/statements/preview")
@login_required
def statement_preview():
    upload = request.files.get("statement")
    if upload is None or not upload.filename:
        flash("Bitte einen Kontoauszug auswählen.")
        return redirect(url_for(".index"))
    data = upload.stream.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        flash("Kontoauszug ist größer als 25 MiB.")
        return redirect(url_for(".index"))
    actor = _actor()
    filename = safe_filename(upload.filename, fallback="kontoauszug", max_length=240)
    try:
        token = _stage_upload(data, actor)
        preview = FinanceStatementImporter(_store()).preview(
            data,
            filename,
            actor,
            account_id=request.form.get("account_id", "").strip(),
        )
    except ValueError as exc:
        flash(f"Kontoauszug konnte nicht gelesen werden: {exc}")
        return redirect(url_for(".index"))
    return render_template(
        "finance/statement_preview.html",
        preview=preview,
        stage_token=token,
        filename=filename,
        accounts=_store().accounts(actor),
    )


@bp.post("/statements/commit")
@login_required
def statement_commit():
    actor = _actor()
    token = request.form.get("stage_token", "").strip().casefold()
    filename = safe_filename(request.form.get("filename", "kontoauszug"), fallback="kontoauszug", max_length=240)
    try:
        data = _read_stage(token, actor)
        result = FinanceStatementImporter(_store()).commit(
            data,
            filename,
            actor,
            account_id=request.form.get("account_id", "").strip(),
            statement_index=int(request.form.get("statement_index", "0")),
        )
    except (ValueError, TypeError) as exc:
        flash(f"Kontoauszug nicht importiert: {exc}")
        return redirect(url_for(".index"))
    account = result.get("account") or {}
    if result["status"] == "already_imported":
        flash("Dieser Kontoauszug wurde für das Konto bereits importiert. Es wurden keine Buchungen verändert.")
    else:
        flash(
            f"Import abgeschlossen: {result['created']} neu, {result['existing']} bereits vorhanden, "
            f"{result['possible_duplicates']} mögliche Dubletten."
        )
    return redirect(url_for(".index", account_id=account.get("account_id", request.form.get("account_id", ""))))


@bp.post("/bank-connections")
@login_required
def create_bank_connection():
    values = request.form.to_dict()
    for key in ("pin", "tan", "password", "secret"):
        values.pop(key, None)
    try:
        connection = _store().save_bank_connection(values, _actor())
        flash("Bankverbindung gespeichert. Die PIN wurde nicht gespeichert.")
        return redirect(url_for(".index", connection_id=connection["connection_id"]))
    except ValueError as exc:
        flash(f"Bankverbindung nicht gespeichert: {exc}")
        return redirect(url_for(".index"))


@bp.post("/bank-connections/<connection_id>/discover")
@login_required
def discover_bank_accounts(connection_id: str):
    actor = _actor()
    pin = request.form.get("pin", "")
    try:
        store = _store()
        connection = store.bank_connection(connection_id, actor)
        remote_accounts = discover_accounts(connection, pin)
        local_accounts = store.accounts(actor)
        suggestions = []
        for remote in remote_accounts:
            local = store.account_by_iban(remote.get("iban", ""), actor) if remote.get("iban") else None
            suggestions.append({"remote": remote, "local": local})
        return render_template("finance/bank_discovery.html", connection=connection, suggestions=suggestions, accounts=local_accounts)
    except (ValueError, FinTSUnavailable) as exc:
        flash(f"Konten konnten nicht abgerufen werden: {exc}")
        return redirect(url_for(".index"))
    finally:
        pin = ""


@bp.post("/bank-connections/<connection_id>/map-account")
@login_required
def map_bank_account(connection_id: str):
    actor = _actor()
    try:
        mapping = _store().map_remote_account(connection_id, request.form.get("remote_account_id", ""), request.form.get("account_id", ""), request.form.get("remote_iban", ""), actor)
        flash("Bankkonto wurde dem vorhandenen SimpleOffice-Konto zugeordnet.")
        return redirect(url_for(".index", account_id=mapping["account_id"]))
    except ValueError as exc:
        flash(f"Kontozuordnung nicht gespeichert: {exc}")
        return redirect(url_for(".index"))


@bp.post("/bank-connections/<connection_id>/sync")
@login_required
def sync_bank_connection(connection_id: str):
    actor = _actor()
    pin = request.form.get("pin", "")
    try:
        result = FinTSSyncService(_store()).sync_connection(connection_id, pin, actor)
        flash(
            f"Bankabruf abgeschlossen: {result['created']} neu, "
            f"{result['existing']} bereits vorhanden, "
            f"{result['possible_duplicates']} mögliche Dubletten."
        )
    except FinTSAuthenticationRequired as exc:
        _store().update_bank_connection_status(connection_id, actor, "authentication_required", error=str(exc))
        flash(str(exc))
    except (FinTSUnavailable, ValueError) as exc:
        flash(f"Bankabruf nicht möglich: {exc}")
    except Exception:
        # Never surface library exception details here: a provider could include
        # sensitive dialog material. The store contains a bounded diagnostic.
        flash("Bankabruf fehlgeschlagen. Details stehen im Bankverbindungsstatus.")
    finally:
        pin = ""
    return redirect(url_for(".index"))


@bp.get("/transactions/<transaction_id>/matches")
@login_required
def transaction_matches(transaction_id: str):
    actor = _actor()
    try:
        store = _store()
        transaction = store.bank_transaction(transaction_id, actor)
        proposals = proposals_for_transaction(_root(), store, transaction_id, actor)
        confirmed = store.transaction_matches(transaction_id, actor)
        allocation = store.transaction_match_allocation(transaction_id, actor)
        return render_template(
            "finance/transaction_matches.html",
            transaction=transaction,
            proposals=proposals,
            confirmed=confirmed,
            allocation=allocation,
        )
    except ValueError as exc:
        flash(f"Zuordnung nicht möglich: {exc}")
        return redirect(url_for(".index"))


@bp.post("/transactions/<transaction_id>/matches")
@login_required
def confirm_transaction_match(transaction_id: str):
    actor = _actor()
    try:
        match = _store().confirm_transaction_match(
            transaction_id,
            request.form.get("source_type", ""),
            request.form.get("source_id", ""),
            actor,
            score=int(request.form.get("score", "0") or 0),
            allocated_cents=int(request.form.get("allocated_cents", "0") or 0),
            note=request.form.get("note", ""),
        )
        flash("Bankumsatz wurde bestätigt zugeordnet. Die Rohbuchung blieb unverändert.")
        return redirect(url_for(".transaction_matches", transaction_id=match["transaction_id"]))
    except (ValueError, TypeError) as exc:
        flash(f"Zuordnung nicht gespeichert: {exc}")
        return redirect(url_for(".transaction_matches", transaction_id=transaction_id))


def init_app(app) -> None:
    app.register_blueprint(bp)
