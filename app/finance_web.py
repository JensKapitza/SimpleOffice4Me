"""Web UI for finance accounts and safe bank statement import."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import time
from pathlib import Path
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit
from typing import Any

from flask import Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request, url_for

from .auth import login_required
from .access_control import audit, is_admin
from .db import get_db
from .document_store import CONTROL_DIR
from .settings_store import (
    DEFAULT_BANK_BROWSER_TIMEOUT_SECONDS,
    DEFAULT_FINANCE_IMPORT_LIMIT_BYTES,
    DEFAULT_FINANCE_STAGING_TTL_SECONDS,
    MAX_FINANCE_IMPORT_LIMIT_BYTES,
    MAX_FINANCE_STAGING_TTL_SECONDS,
    MAX_BANK_BROWSER_TIMEOUT_SECONDS,
    MIN_FINANCE_IMPORT_LIMIT_BYTES,
    MIN_FINANCE_STAGING_TTL_SECONDS,
    MIN_BANK_BROWSER_TIMEOUT_SECONDS,
    SettingsStore,
)
from .finance_statements import FinanceStatementImporter
from .finance_store import FinanceStore
from .finance_fints import (
    FinTSAuthenticationRequired,
    FinTSFeatureUnavailable,
    FinTSUnavailable,
    discover_accounts_with_capabilities,
)
from .finance_fints_balances import fetch_balance
from .finance_fints_sync import FinTSSyncService
from .finance_browser_download import capture_santander_statement
from .finance_match_adapters import proposals_for_transaction
from .safe_paths import safe_filename

bp = Blueprint("finance", __name__, url_prefix="/finances")


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


def _cleanup_staging(actor: str, ttl_seconds: int | None = None) -> None:
    ttl = ttl_seconds if ttl_seconds is not None else _finance_controls()[1]
    cutoff = time.time() - ttl
    for path in _stage_dir(actor).glob("*.bin"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _stage_upload(data: bytes, actor: str) -> str:
    if not data:
        raise ValueError("Kontoauszug ist leer")
    max_bytes = _finance_controls()[0]
    if len(data) > max_bytes:
        raise ValueError(f"Kontoauszug ist größer als {max_bytes // (1024 * 1024)} MiB")
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
        info = path.stat()
        max_bytes, ttl_seconds = _finance_controls()
        if info.st_mtime < time.time() - ttl_seconds:
            path.unlink(missing_ok=True)
            raise ValueError("Importvorschau ist abgelaufen; Datei bitte erneut auswählen")
        if info.st_size > max_bytes:
            raise ValueError("Zwischengespeicherter Kontoauszug überschreitet das Importlimit")
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Zwischengespeicherter Kontoauszug überschreitet das Importlimit")
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("Importvorschau ist abgelaufen; Datei bitte erneut auswählen") from exc
    if hashlib.sha256(data).hexdigest() != token:
        raise ValueError("Zwischengespeicherter Kontoauszug ist beschädigt")
    return data


@bp.get("")
@login_required
def index():
    actor = _actor()
    import_limit, staging_ttl = _finance_controls()
    _cleanup_staging(actor, staging_ttl)
    store = _store()
    accounts = store.accounts(actor)
    connections = store.bank_connections(actor)
    bank_account_mappings = {
        connection["connection_id"]: store.bank_account_mappings(connection["connection_id"], actor)
        for connection in connections
    }
    selected = request.args.get("account_id", "").strip()
    if selected and selected not in {row["account_id"] for row in accounts}:
        selected = ""
    transactions = store.bank_transactions(selected, actor, limit=100) if selected else []
    return render_template(
        "finance/index.html",
        accounts=accounts,
        bank_connections=connections,
        bank_account_mappings=bank_account_mappings,
        selected_account_id=selected,
        transactions=transactions,
        bank_browser_enabled=_bank_browser_setting(),
        bank_browser_timeout_seconds=_bank_browser_timeout_seconds(),
        statement_import_limit_mib=import_limit // (1024 * 1024),
        statement_staging_ttl_hours=staging_ttl // (60 * 60),
        local_bank_browser_enabled=_local_bank_browser_enabled(),
        bank_browser_admin=is_admin(g.user),
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


def _bank_browser_setting() -> bool:
    """Read the persistent setting and migrate the earlier draft table once."""
    settings = SettingsStore(_root())
    values = settings.settings()
    enabled = values["finance"].get("bank_browser_enabled", True)
    if _has_persisted_bank_browser_setting(settings):
        return enabled is True

    db = get_db()
    legacy_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        ("finance_ui_settings",),
    ).fetchone()
    if legacy_table:
        row = db.execute(
            "SELECT value FROM finance_ui_settings WHERE name=?",
            ("bank_browser_enabled",),
        ).fetchone()
        if row and row["value"] in {"0", "1"}:
            enabled = row["value"] == "1"
            settings.save({"finance": {"bank_browser_enabled": enabled}}, "system-migration")
            audit(
                "finance_bank_browser_setting_migrated",
                "settings",
                "finance.bank_browser_enabled",
                detail={"enabled": enabled, "source": "legacy_finance_ui_settings"},
            )
            return enabled
    return enabled is True


def _bank_browser_timeout_seconds() -> int:
    value = SettingsStore(_root()).settings()["finance"].get(
        "bank_browser_timeout_seconds", DEFAULT_BANK_BROWSER_TIMEOUT_SECONDS,
    )
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and MIN_BANK_BROWSER_TIMEOUT_SECONDS <= value <= MAX_BANK_BROWSER_TIMEOUT_SECONDS
    ):
        return value
    current_app.logger.warning("Invalid persisted bank browser timeout; using default")
    return DEFAULT_BANK_BROWSER_TIMEOUT_SECONDS


def _finance_controls() -> tuple[int, int]:
    finance = SettingsStore(_root()).settings()["finance"]
    import_limit = finance.get("statement_import_limit_bytes", DEFAULT_FINANCE_IMPORT_LIMIT_BYTES)
    staging_ttl = finance.get("statement_staging_ttl_seconds", DEFAULT_FINANCE_STAGING_TTL_SECONDS)
    if (
        isinstance(import_limit, bool)
        or not isinstance(import_limit, int)
        or not MIN_FINANCE_IMPORT_LIMIT_BYTES <= import_limit <= MAX_FINANCE_IMPORT_LIMIT_BYTES
    ):
        current_app.logger.warning("Invalid persisted finance import limit; using default")
        import_limit = DEFAULT_FINANCE_IMPORT_LIMIT_BYTES
    if (
        isinstance(staging_ttl, bool)
        or not isinstance(staging_ttl, int)
        or not MIN_FINANCE_STAGING_TTL_SECONDS <= staging_ttl <= MAX_FINANCE_STAGING_TTL_SECONDS
    ):
        current_app.logger.warning("Invalid persisted finance staging TTL; using default")
        staging_ttl = DEFAULT_FINANCE_STAGING_TTL_SECONDS
    return import_limit, staging_ttl


def _has_persisted_bank_browser_setting(settings: SettingsStore) -> bool:
    try:
        stored = json.loads(settings.path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    finance = stored.get("finance") if isinstance(stored, dict) else None
    return isinstance(finance, dict) and "bank_browser_enabled" in finance


def _local_bank_browser_enabled() -> bool:
    address = request.remote_addr
    try:
        remote_is_loopback = bool(address and ipaddress.ip_address(address).is_loopback)
        hostname = urlsplit(f"//{request.host}").hostname or ""
        try:
            host_is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            host_is_loopback = hostname.rstrip(".").casefold() == "localhost"
    except (TypeError, ValueError):
        return False
    # A local reverse proxy may make a remote request appear to come from
    # loopback while preserving its public host.
    return _bank_browser_setting() and remote_is_loopback and host_is_loopback


@bp.post("/settings/bank-browser")
@login_required
def update_bank_browser_setting():
    if not is_admin(g.user):
        abort(403)
    reset_defaults = request.form.get("reset") == "1"
    value = "1" if reset_defaults else request.form.get("enabled", "")
    if value not in {"0", "1"}:
        abort(400)
    enabled = value == "1"
    settings = SettingsStore(_root())
    current = settings.settings()
    timeout_raw = (
        str(DEFAULT_BANK_BROWSER_TIMEOUT_SECONDS)
        if reset_defaults else request.form.get("timeout_seconds")
    )
    if timeout_raw is None:
        timeout_seconds = _bank_browser_timeout_seconds()
    else:
        if len(timeout_raw) > 4 or not timeout_raw.isascii() or not timeout_raw.isdigit():
            abort(400)
        timeout_seconds = int(timeout_raw)
        if not MIN_BANK_BROWSER_TIMEOUT_SECONDS <= timeout_seconds <= MAX_BANK_BROWSER_TIMEOUT_SECONDS:
            abort(400)
    current_import_limit, current_staging_ttl = _finance_controls()
    import_limit_raw = (
        str(DEFAULT_FINANCE_IMPORT_LIMIT_BYTES // (1024 * 1024))
        if reset_defaults else request.form.get("import_limit_mib")
    )
    if import_limit_raw is None:
        import_limit = current_import_limit
    else:
        if len(import_limit_raw) > 2 or not import_limit_raw.isascii() or not import_limit_raw.isdigit():
            abort(400)
        import_limit = int(import_limit_raw) * 1024 * 1024
        if not MIN_FINANCE_IMPORT_LIMIT_BYTES <= import_limit <= MAX_FINANCE_IMPORT_LIMIT_BYTES:
            abort(400)
    staging_ttl_raw = (
        str(DEFAULT_FINANCE_STAGING_TTL_SECONDS // 3600)
        if reset_defaults else request.form.get("staging_ttl_hours")
    )
    if staging_ttl_raw is None:
        staging_ttl = current_staging_ttl
    else:
        if len(staging_ttl_raw) > 3 or not staging_ttl_raw.isascii() or not staging_ttl_raw.isdigit():
            abort(400)
        staging_ttl = int(staging_ttl_raw) * 60 * 60
        if not MIN_FINANCE_STAGING_TTL_SECONDS <= staging_ttl <= MAX_FINANCE_STAGING_TTL_SECONDS:
            abort(400)
    finance_settings = dict(current["finance"])
    changed = (
        finance_settings.get("bank_browser_enabled") is not enabled
        or finance_settings.get("bank_browser_timeout_seconds") != timeout_seconds
        or finance_settings.get("statement_import_limit_bytes") != import_limit
        or finance_settings.get("statement_staging_ttl_seconds") != staging_ttl
    )
    if changed:
        finance_settings["bank_browser_enabled"] = enabled
        finance_settings["bank_browser_timeout_seconds"] = timeout_seconds
        finance_settings["statement_import_limit_bytes"] = import_limit
        finance_settings["statement_staging_ttl_seconds"] = staging_ttl
        settings.save({"finance": finance_settings}, _actor())
        audit(
            "finance_bank_browser_setting_updated",
            "settings",
            "finance",
            detail={
                "enabled": enabled,
                "timeout_seconds": timeout_seconds,
                "import_limit_bytes": import_limit,
                "staging_ttl_seconds": staging_ttl,
                "reset_to_defaults": reset_defaults,
            },
        )
    flash(
        "Finanz- und Banking-Einstellungen auf Standardwerte zurückgesetzt."
        if reset_defaults else "Finanz- und Banking-Einstellungen gespeichert."
    )
    return redirect(url_for(".index"))


@bp.post("/bank-browser/santander")
@login_required
def santander_browser_download():
    if not is_admin(g.user) or not _local_bank_browser_enabled():
        abort(403)
    actor = _actor()
    try:
        timeout = _bank_browser_timeout_seconds()
        result = capture_santander_statement(timeout_seconds=timeout)
        preview = FinanceStatementImporter(_store()).preview(
            result.content,
            result.filename,
            actor,
            account_id=request.form.get("account_id", "").strip(),
        )
        token = _stage_upload(result.content, actor)
        return render_template(
            "finance/statement_preview.html",
            preview=preview,
            stage_token=token,
            filename=result.filename,
            accounts=_store().accounts(actor),
        )
    except (ValueError, RuntimeError, TimeoutError, OSError) as exc:
        # Do not show provider exception bodies that might contain sensitive data.
        current_app.logger.warning("Local bank browser export failed: %s", type(exc).__name__)
        flash("Santander-Export fehlgeschlagen oder abgebrochen. Bitte Browser, Freigabe und Export prüfen.")
        return redirect(url_for(".index"))


@bp.post("/statements/preview")
@login_required
def statement_preview():
    upload = request.files.get("statement")
    if upload is None or not upload.filename:
        flash("Bitte einen Kontoauszug auswählen.")
        return redirect(url_for(".index"))
    max_upload_bytes = _finance_controls()[0]
    data = upload.stream.read(max_upload_bytes + 1)
    if len(data) > max_upload_bytes:
        flash(f"Kontoauszug ist größer als {max_upload_bytes // (1024 * 1024)} MiB.")
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
        remote_accounts, bank_capabilities = discover_accounts_with_capabilities(connection, pin)
        local_accounts = store.accounts(actor)
        suggestions = []
        for remote in remote_accounts:
            local = store.account_by_iban(remote.get("iban", ""), actor) if remote.get("iban") else None
            account_capabilities = bank_capabilities["accounts"].get(remote["remote_account_id"], {})
            account_capabilities = {
                feature: account_capabilities.get(feature)
                if account_capabilities.get(feature) is not None
                else bank_capabilities["read"][feature]
                for feature in ("balance", "transactions")
            }
            suggestions.append({"remote": remote, "local": local, "capabilities": account_capabilities})
        response = make_response(render_template(
            "finance/bank_discovery.html", connection=connection, suggestions=suggestions,
            accounts=local_accounts, bank_capabilities=bank_capabilities,
        ))
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    except FinTSAuthenticationRequired:
        flash("Die Bank verlangt bei der Kontenermittlung eine TAN/App-Freigabe. Dieser Vorgang kann hier nicht fortgesetzt werden.")
        return redirect(url_for(".index"))
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


@bp.post("/bank-connections/<connection_id>/balance")
@login_required
def read_bank_connection_balance(connection_id: str):
    actor = _actor()
    store = _store()
    try:
        connection = store.bank_connection(connection_id, actor)
        if str(connection.get("status", "")).casefold() == "disabled":
            abort(404)
        remote_account_id = str(request.form.get("remote_account_id", "")).strip()
        if not remote_account_id or len(remote_account_id) > 200:
            abort(404)
        mapping = next((
            item for item in store.bank_account_mappings(connection_id, actor)
            if item["remote_account_id"] == remote_account_id
        ), None)
        if mapping is None:
            abort(404)
    except ValueError:
        abort(404)

    pin = request.form.get("pin", "")
    try:
        balance = fetch_balance(connection, remote_account_id, pin)
    except FinTSAuthenticationRequired as exc:
        if exc.challenge_state:
            try:
                token = store.create_fints_challenge(connection_id, exc.challenge_state, actor)
            except ValueError:
                flash("Die TAN-Freigabe konnte nicht sicher vorbereitet werden. Bitte den Saldoabruf erneut starten.")
                return redirect(url_for(".index", account_id=mapping["account_id"]))
            return _balance_challenge_response(mapping, connection_id, token, exc.challenge_state)
        flash("Die Bank verlangt eine nicht unterstützte TAN/App-Freigabe. Bitte den Bankabruf erneut starten.")
        return redirect(url_for(".index", account_id=mapping["account_id"]))
    except FinTSUnavailable as exc:
        flash(str(exc))
        return redirect(url_for(".index", account_id=mapping["account_id"]))
    except FinTSFeatureUnavailable as exc:
        flash(str(exc))
        return redirect(url_for(".index", account_id=mapping["account_id"]))
    except ValueError:
        flash("Kontostand konnte nicht abgerufen werden. Bitte Bankverbindung, PIN und Kontozuordnung prüfen.")
        return redirect(url_for(".index", account_id=mapping["account_id"]))
    except Exception as exc:
        current_app.logger.warning("FinTS balance read failed: %s", type(exc).__name__)
        flash("Kontostand konnte nicht sicher abgerufen werden.")
        return redirect(url_for(".index", account_id=mapping["account_id"]))
    finally:
        pin = ""

    response = make_response(render_template(
        "finance/balance_report.html",
        account=mapping,
        balance=balance,
    ))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.post("/bank-connections/<connection_id>/balance/challenge")
@login_required
def continue_bank_connection_balance(connection_id: str):
    actor = _actor()
    store = _store()
    pin = request.form.get("pin", "")
    tan = request.form.get("tan", "")
    token = request.form.get("challenge_token", "")
    try:
        connection = store.bank_connection(connection_id, actor)
        challenge_state = store.consume_fints_challenge(token, connection_id, actor)
        mapping = next((
            item for item in store.bank_account_mappings(connection_id, actor)
            if item["remote_account_id"] == challenge_state["remote_account_id"]
        ), None)
        if mapping is None:
            raise ValueError("FinTS-SCA-Kontozuordnung ist nicht mehr verfügbar")
        balance = fetch_balance(
            connection,
            challenge_state["remote_account_id"],
            pin,
            challenge_state=challenge_state,
            tan=tan,
        )
    except FinTSAuthenticationRequired as exc:
        if exc.challenge_state and "mapping" in locals():
            try:
                next_token = store.create_fints_challenge(connection_id, exc.challenge_state, actor)
            except ValueError:
                flash("Die TAN-Freigabe ist abgelaufen. Bitte den Saldoabruf neu starten.")
                return redirect(url_for(".index"))
            return _balance_challenge_response(mapping, connection_id, next_token, exc.challenge_state)
        flash("Die Bank verlangt eine nicht unterstützte TAN/App-Freigabe. Bitte erneut starten.")
        return redirect(url_for(".index"))
    except FinTSFeatureUnavailable as exc:
        flash(str(exc))
        return redirect(url_for(".index"))
    except (ValueError, FinTSUnavailable):
        flash("TAN-Freigabe fehlgeschlagen oder abgelaufen. Bitte den Saldoabruf neu starten.")
        return redirect(url_for(".index"))
    except Exception as exc:
        current_app.logger.warning("FinTS SCA continuation failed: %s", type(exc).__name__)
        flash("TAN-Freigabe konnte nicht sicher fortgesetzt werden. Bitte erneut starten.")
        return redirect(url_for(".index"))
    finally:
        pin = ""
        tan = ""
    response = make_response(render_template(
        "finance/balance_report.html", account=mapping, balance=balance,
    ))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _balance_challenge_response(mapping, connection_id: str, token: str, challenge_state: dict):
    response = make_response(render_template(
        "finance/balance_challenge.html",
        account=mapping,
        connection_id=connection_id,
        challenge_token=token,
        challenge_text=challenge_state["challenge_text"],
        decoupled=challenge_state["decoupled"],
    ))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.post("/bank-connections/<connection_id>/sync")
@login_required
def sync_bank_connection(connection_id: str):
    actor = _actor()
    store = _store()
    pin = request.form.get("pin", "")
    try:
        result = FinTSSyncService(store).sync_connection(connection_id, pin, actor)
        return _sync_report_response(result)
    except FinTSAuthenticationRequired as exc:
        store.update_bank_connection_status(connection_id, actor, "authentication_required", error=str(exc))
        if exc.challenge_state:
            try:
                token = store.create_fints_challenge(connection_id, exc.challenge_state, actor)
            except ValueError:
                flash("Die TAN-Freigabe konnte nicht sicher vorbereitet werden. Bereits abgeschlossene Konten bleiben importiert.")
                return redirect(url_for(".index"))
            return _transaction_sync_challenge_response(connection_id, token, exc.challenge_state)
        flash(str(exc))
    except FinTSFeatureUnavailable as exc:
        flash(str(exc))
        return redirect(url_for(".index"))
    except (FinTSUnavailable, ValueError) as exc:
        flash(f"Bankabruf nicht möglich: {exc}")
    except Exception:
        # Never surface library exception details here: a provider could include
        # sensitive dialog material. The store contains a bounded diagnostic.
        flash("Bankabruf fehlgeschlagen. Details stehen im Bankverbindungsstatus.")
    finally:
        pin = ""
    return redirect(url_for(".index"))


@bp.post("/bank-connections/<connection_id>/sync/challenge")
@login_required
def continue_bank_connection_sync(connection_id: str):
    actor = _actor()
    store = _store()
    pin = request.form.get("pin", "")
    tan = request.form.get("tan", "")
    try:
        challenge_state = store.consume_fints_challenge(
            request.form.get("challenge_token", ""), connection_id, actor,
        )
        if challenge_state.get("operation") != "transactions":
            raise ValueError("Diese TAN-Freigabe ist kein Umsatzabruf")
        result = FinTSSyncService(store).sync_connection(
            connection_id, pin, actor, challenge_state=challenge_state, tan=tan,
        )
        return _sync_report_response(result)
    except FinTSAuthenticationRequired as exc:
        if exc.challenge_state:
            try:
                token = store.create_fints_challenge(connection_id, exc.challenge_state, actor)
            except ValueError:
                flash("Die TAN-Freigabe ist abgelaufen oder konnte nicht sicher erneuert werden. Bitte den Abruf neu starten.")
                return redirect(url_for(".index"))
            return _transaction_sync_challenge_response(connection_id, token, exc.challenge_state)
        flash("Die Bank verlangt eine nicht unterstützte TAN/App-Freigabe. Bitte den Bankabruf neu starten.")
        return redirect(url_for(".index"))
    except FinTSFeatureUnavailable as exc:
        flash(str(exc))
        return redirect(url_for(".index"))
    except (ValueError, FinTSUnavailable):
        flash("TAN-Freigabe fehlgeschlagen oder abgelaufen. Bitte den Bankabruf neu starten.")
        return redirect(url_for(".index"))
    except Exception as exc:
        current_app.logger.warning("FinTS transaction continuation failed: %s", type(exc).__name__)
        flash("TAN-Freigabe konnte nicht sicher fortgesetzt werden. Bereits importierte Umsätze bleiben erhalten.")
        return redirect(url_for(".index"))
    finally:
        pin = ""
        tan = ""


def _private_finance_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _sync_report_response(result: dict[str, Any]):
    return _private_finance_response(make_response(render_template("finance/sync_report.html", result=result)))


def _transaction_sync_challenge_response(connection_id: str, token: str, challenge_state: dict[str, Any]):
    store = _store()
    try:
        connection = store.bank_connection(connection_id, _actor())
        mappings = store.bank_account_mappings(connection_id, _actor())
    except ValueError:
        abort(404)
    mapping = next((item for item in mappings if item["remote_account_id"] == challenge_state["remote_account_id"]), None)
    if mapping is None or mapping["account_id"] != challenge_state["local_account_id"]:
        abort(404)
    response = make_response(render_template(
        "finance/sync_challenge.html",
        connection=connection,
        mapping=mapping,
        connection_id=connection_id,
        challenge_token=token,
        challenge_text=challenge_state["challenge_text"],
        decoupled=challenge_state["decoupled"],
        completed_count=len(challenge_state["completed_mappings"]),
    ))
    return _private_finance_response(response)


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
        return _private_finance_response(make_response(render_template(
            "finance/transaction_matches.html",
            transaction=transaction,
            proposals=proposals,
            confirmed=confirmed,
            allocation=allocation,
        )))
    except ValueError as exc:
        flash(f"Zuordnung nicht möglich: {exc}")
        return redirect(url_for(".index"))


@bp.post("/transactions/<transaction_id>/matches")
@login_required
def confirm_transaction_match(transaction_id: str):
    actor = _actor()
    try:
        try:
            allocated_cents = int((Decimal(request.form.get("allocated_amount", "0").replace(",", ".")) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, OverflowError, ValueError):
            raise ValueError("Ungültiger Zuordnungsbetrag")
        selected_source_type = request.form.get("source_type", "")
        selected_source_id = request.form.get("source_id", "")
        store = _store()
        proposal = next((
            item for item in proposals_for_transaction(_root(), store, transaction_id, actor)
            if item["source_type"] == selected_source_type
            and item["source_id"] == selected_source_id
        ), None)
        if proposal is None:
            raise ValueError("Die gewählte Zuordnung ist nicht mehr als aktueller Vorschlag verfügbar")
        match = store.confirm_transaction_match(
            transaction_id,
            selected_source_type,
            selected_source_id,
            actor,
            score=int(proposal["score"]),
            allocated_cents=allocated_cents,
            note=request.form.get("note", ""),
        )
        flash("Bankumsatz wurde bestätigt zugeordnet. Die Rohbuchung blieb unverändert.")
        return redirect(url_for(".transaction_matches", transaction_id=match["transaction_id"]))
    except (ValueError, TypeError) as exc:
        flash(f"Zuordnung nicht gespeichert: {exc}")
        return redirect(url_for(".transaction_matches", transaction_id=transaction_id))


def init_app(app) -> None:
    app.register_blueprint(bp)
