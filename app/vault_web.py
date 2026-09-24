"""Authenticated web/UI boundary for the encrypted password vault.

Vault keys are cached only in process memory for a short bounded interval.  The
browser session stores an opaque random token, never a vault key or master
password.  Search endpoints return non-secret projections; full credential data
requires an explicit detail view or CSRF-protected reveal request.
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Any

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .auth import login_required
from .mail_client import MailStore
from .mail_index import MailSearchIndex
from .password_interop import (
    browser_csv_export,
    browser_csv_import,
    no_install_browser_capabilities,
    normalized_http_url,
)
from .password_vault import PasswordVault, generate_password
from .v2.vault_service import VaultSearchFilters, VaultService


bp = Blueprint("vault", __name__, url_prefix="/vault")

_UNLOCKS: dict[str, tuple[str, bytes, float]] = {}
_UNLOCK_LOCK = threading.Lock()
_MAX_UNLOCKS = 1024


def _actor() -> str:
    return str(g.user["username"])


def _vault() -> PasswordVault:
    return PasswordVault(current_app.config["DOCUMENT_ROOT"])


def _unlock_seconds() -> int:
    try:
        value = int(current_app.config.get("VAULT_UNLOCK_SECONDS", 300))
    except (TypeError, ValueError):
        value = 300
    return max(60, min(value, 1800))


def _purge_unlocks(now: float) -> None:
    expired = [token for token, (_actor_id, _key, expiry) in _UNLOCKS.items() if expiry <= now]
    for token in expired:
        _UNLOCKS.pop(token, None)
    if len(_UNLOCKS) <= _MAX_UNLOCKS:
        return
    overflow = len(_UNLOCKS) - _MAX_UNLOCKS
    oldest = sorted(_UNLOCKS.items(), key=lambda item: item[1][2])[:overflow]
    for token, _value in oldest:
        _UNLOCKS.pop(token, None)


def _cache_unlock(vault_key: bytes) -> None:
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _UNLOCK_LOCK:
        _purge_unlocks(now)
        _UNLOCKS[token] = (_actor(), bytes(vault_key), now + _unlock_seconds())
    session["vault_unlock_token"] = token


def _clear_unlock() -> None:
    token = session.pop("vault_unlock_token", None)
    if isinstance(token, str):
        with _UNLOCK_LOCK:
            _UNLOCKS.pop(token, None)


def _vault_key() -> bytes | None:
    token = session.get("vault_unlock_token")
    if not isinstance(token, str):
        return None
    now = time.monotonic()
    with _UNLOCK_LOCK:
        _purge_unlocks(now)
        cached = _UNLOCKS.get(token)
        if cached is None:
            session.pop("vault_unlock_token", None)
            return None
        actor, key, expiry = cached
        if actor != _actor() or expiry <= now:
            _UNLOCKS.pop(token, None)
            session.pop("vault_unlock_token", None)
            return None
        _UNLOCKS[token] = (actor, key, now + _unlock_seconds())
        return bytes(key)


def _require_key() -> bytes:
    key = _vault_key()
    if key is None:
        raise ValueError("Vault ist gesperrt")
    return key


def _mail_service(vault: PasswordVault) -> VaultService:
    secret = current_app.config["SECRET_KEY"]
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    store = MailStore(current_app.config["DOCUMENT_ROOT"], raw)
    return VaultService(vault, mail_accounts=store, mail_search=MailSearchIndex(store))


def _filters() -> VaultSearchFilters:
    tags = tuple(
        value.strip()
        for value in request.args.get("tags", "").split(",")
        if value.strip()
    )
    return VaultSearchFilters(
        text=request.args.get("q", ""),
        username=request.args.get("username", ""),
        email=request.args.get("email", ""),
        domain=request.args.get("domain", ""),
        tags=tags,
        folder=request.args.get("folder", ""),
        favorites_only=request.args.get("favorites") == "1",
        limit=max(1, min(request.args.get("limit", 250, type=int) or 250, 1000)),
    )


def _payload_from_request() -> dict[str, Any]:
    source: dict[str, Any]
    if request.is_json:
        value = request.get_json(silent=True)
        source = value if isinstance(value, dict) else {}
    else:
        source = request.form.to_dict(flat=True)
    kind = str(source.get("type") or "login").strip()
    payload: dict[str, Any] = {
        "type": kind,
        "name": str(source.get("name") or "").strip(),
        "username": str(source.get("username") or ""),
        "email": str(source.get("email") or "").strip(),
        "password": str(source.get("password") or ""),
        "notes": str(source.get("notes") or ""),
        "totp": str(source.get("totp") or ""),
        "folder": str(source.get("folder") or "").strip(),
        "favorite": str(source.get("favorite") or "").strip().casefold()
        in {"1", "true", "yes", "on"},
    }
    url = str(source.get("url") or "").strip()
    if url:
        payload["url"] = normalized_http_url(url)
    tags_raw = source.get("tags", "")
    if isinstance(tags_raw, (list, tuple)):
        tags = [str(value).strip() for value in tags_raw if str(value).strip()]
    else:
        tags = [value.strip() for value in str(tags_raw or "").split(",") if value.strip()]
    payload["tags"] = tags[:64]
    if not payload["name"] and payload.get("url"):
        payload["name"] = str(payload["url"])
    if not payload["name"]:
        raise ValueError("Ein Name ist erforderlich")
    return payload


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _redirect_index():
    return redirect(url_for("vault.index"))


@bp.get("/")
@login_required
def index():
    vault = _vault()
    actor = _actor()
    configured = vault.configured(actor)
    key = _vault_key() if configured else None
    rows: list[dict[str, Any]] = []
    search_error = ""
    if key is not None:
        try:
            rows = VaultService(vault).search(actor, key, _filters())
        except (TypeError, ValueError) as exc:
            search_error = str(exc)
    response = make_response(
        render_template(
            "vault/index.html",
            configured=configured,
            unlocked=key is not None,
            recovery_configured=vault.recovery_configured(actor) if configured else False,
            entries=rows,
            search_error=search_error,
            capabilities=no_install_browser_capabilities(),
            unlock_seconds=_unlock_seconds(),
        )
    )
    return _no_store(response)


@bp.post("/setup")
@login_required
def setup():
    vault = _vault()
    actor = _actor()
    if vault.configured(actor):
        flash("Der Passwort-Vault ist bereits eingerichtet.")
        return _redirect_index()
    password = request.form.get("master_password", "")
    confirmation = request.form.get("master_password_confirm", "")
    if password != confirmation:
        flash("Die Master-Passwörter stimmen nicht überein.")
        return _redirect_index()
    try:
        _cache_unlock(vault.create(actor, password))
        flash("Passwort-Vault eingerichtet und entsperrt.")
    except (RuntimeError, ValueError):
        flash("Der Passwort-Vault konnte nicht eingerichtet werden.")
    return _redirect_index()


@bp.post("/unlock")
@login_required
def unlock():
    vault = _vault()
    try:
        key = vault.unlock(_actor(), request.form.get("master_password", ""))
        _cache_unlock(key)
        flash("Vault entsperrt.")
    except (RuntimeError, ValueError):
        _clear_unlock()
        flash("Vault konnte nicht entsperrt werden.")
    return _redirect_index()


@bp.post("/lock")
@login_required
def lock():
    _clear_unlock()
    flash("Vault gesperrt.")
    return _redirect_index()


@bp.post("/master-password")
@login_required
def change_master_password():
    vault = _vault()
    old = request.form.get("old_password", "")
    new = request.form.get("new_password", "")
    confirmation = request.form.get("new_password_confirm", "")
    if new != confirmation:
        flash("Die neuen Master-Passwörter stimmen nicht überein.")
        return _redirect_index()
    try:
        vault.change_master_password(_actor(), old, new)
        _cache_unlock(vault.unlock(_actor(), new))
        flash("Master-Passwort geändert.")
    except (RuntimeError, ValueError):
        flash("Master-Passwort konnte nicht geändert werden.")
    return _redirect_index()


@bp.get("/entries/<entry_id>")
@login_required
def entry(entry_id: str):
    vault = _vault()
    try:
        key = _require_key()
        wrapper = VaultService(vault).credential(_actor(), key, entry_id)
        try:
            mail_references = _mail_service(vault).credential_mail_references(
                _actor(), key, entry_id, limit=100
            )
        except (OSError, RuntimeError, ValueError):
            mail_references = []
    except (RuntimeError, ValueError):
        flash("Vault ist gesperrt oder der Eintrag ist nicht verfügbar.")
        return _redirect_index()
    response = make_response(
        render_template(
            "vault/entry.html",
            entry=wrapper,
            mail_references=mail_references,
        )
    )
    return _no_store(response)


@bp.post("/entries")
@bp.post("/entries/<entry_id>")
@login_required
def save_entry(entry_id: str = ""):
    vault = _vault()
    try:
        key = _require_key()
        row = vault.put(
            _actor(),
            key,
            _payload_from_request(),
            entry_id=entry_id or None,
        )
        flash("Credential verschlüsselt gespeichert.")
        return redirect(url_for("vault.entry", entry_id=row["entry_id"]))
    except (RuntimeError, ValueError):
        flash("Credential konnte nicht gespeichert werden.")
        return _redirect_index()


@bp.post("/entries/<entry_id>/delete")
@login_required
def delete_entry(entry_id: str):
    vault = _vault()
    try:
        _require_key()
        vault.delete(_actor(), entry_id)
        flash("Credential gelöscht.")
    except (RuntimeError, ValueError):
        flash("Credential konnte nicht gelöscht werden.")
    return _redirect_index()


def _candidate_key(data: dict[str, Any]) -> tuple[str, str]:
    url = str(data.get("url") or "").strip().casefold()
    username = str(data.get("username") or "").strip().casefold()
    return url, username


@bp.post("/import/browser-csv")
@login_required
def import_browser_csv():
    vault = _vault()
    try:
        key = _require_key()
        upload = request.files.get("file")
        if upload is None:
            raise ValueError("CSV-Datei fehlt")
        raw = upload.stream.read(16 * 1024 * 1024 + 1)
        candidates = browser_csv_import(raw)
        existing = {
            _candidate_key(wrapper.get("data", {}))
            for wrapper in vault.entries(_actor(), key)
            if isinstance(wrapper.get("data"), dict)
        }
        duplicates = sum(1 for row in candidates if _candidate_key(row) in existing)
        pending = [row for row in candidates if _candidate_key(row) not in existing]
        if request.form.get("confirm") != "1":
            flash(
                f"CSV geprüft: {len(candidates)} Zeilen, {duplicates} vorhandene Zugänge, "
                f"{len(pending)} neue Zugänge. Zum Import dieselbe Datei erneut auswählen und bestätigen."
            )
            return _redirect_index()
        imported = 0
        for row in pending:
            vault.put(_actor(), key, row)
            imported += 1
        flash(f"CSV importiert: {imported} neu, {duplicates} vorhandene Einträge nicht überschrieben.")
    except (OSError, RuntimeError, ValueError):
        flash("Passwort-CSV konnte nicht verarbeitet werden.")
    return _redirect_index()


@bp.post("/export/browser-csv")
@login_required
def export_browser_csv():
    vault = _vault()
    if request.form.get("confirm") != "EXPORT":
        flash("Klartext-Export nicht bestätigt.")
        return _redirect_index()
    try:
        _require_key()
        key = vault.unlock(_actor(), request.form.get("master_password", ""))
        raw = browser_csv_export(vault.entries(_actor(), key))
    except (RuntimeError, ValueError):
        flash("Klartext-Export konnte nicht erstellt oder erneut authentifiziert werden.")
        return _redirect_index()
    response = Response(raw, mimetype="text/csv")
    response.headers["Content-Disposition"] = 'attachment; filename="simpleoffice-passwords.csv"'
    return _no_store(response)


@bp.post("/export/backup")
@login_required
def export_backup():
    vault = _vault()
    try:
        _require_key()
        raw = vault.export_backup(_actor())
    except (RuntimeError, ValueError):
        flash("Verschlüsseltes Vault-Backup konnte nicht erstellt werden.")
        return _redirect_index()
    response = Response(raw, mimetype="application/json")
    response.headers["Content-Disposition"] = 'attachment; filename="simpleoffice-vault-backup.json"'
    return _no_store(response)


@bp.post("/import/backup")
@login_required
def import_backup():
    vault = _vault()
    actor = _actor()
    try:
        if vault.configured(actor):
            _require_key()
            vault.unlock(actor, request.form.get("master_password", ""))
        if request.form.get("confirm") != "REPLACE":
            raise ValueError("replacement confirmation required")
        upload = request.files.get("file")
        if upload is None:
            raise ValueError("backup file is required")
        raw = upload.stream.read(64 * 1024 * 1024 + 1)
        result = vault.import_backup(
            raw,
            target_user_id=actor,
            replace=vault.configured(actor),
        )
        _clear_unlock()
        flash(f"Verschlüsseltes Vault-Backup importiert: {result['entries']} Einträge. Vault neu entsperren.")
    except (OSError, RuntimeError, ValueError):
        flash("Vault-Backup konnte nicht importiert werden.")
    return _redirect_index()


@bp.get("/api/v1/search")
@login_required
def api_search():
    vault = _vault()
    try:
        rows = VaultService(vault).search(_actor(), _require_key(), _filters())
        return _no_store(jsonify({"ok": True, "entries": rows}))
    except (RuntimeError, ValueError):
        return _no_store(jsonify({"ok": False, "error": "vault_locked_or_invalid_query"})), 423


@bp.post("/api/v1/credentials/<entry_id>/reveal")
@login_required
def api_reveal(entry_id: str):
    vault = _vault()
    try:
        row = VaultService(vault).credential(_actor(), _require_key(), entry_id)
        return _no_store(jsonify({"ok": True, "credential": row}))
    except (RuntimeError, ValueError):
        return _no_store(jsonify({"ok": False, "error": "credential_unavailable"})), 404


@bp.get("/api/v1/credentials/<entry_id>/mail")
@login_required
def api_mail_references(entry_id: str):
    vault = _vault()
    try:
        rows = _mail_service(vault).credential_mail_references(
            _actor(), _require_key(), entry_id, limit=100
        )
        return _no_store(jsonify({"ok": True, "messages": rows}))
    except (OSError, RuntimeError, ValueError):
        return _no_store(jsonify({"ok": False, "error": "mail_references_unavailable"})), 404


@bp.get("/api/v1/generate-password")
@login_required
def api_generate_password():
    length = request.args.get("length", 24, type=int) or 24
    return _no_store(jsonify({"ok": True, "password": generate_password(length)}))
