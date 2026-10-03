"""Browser administration for master and peer moderation."""
import time
from datetime import datetime, timezone

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from . import build_master
from .federation_admin import admin_required
from .federation_identity import FederationIdentity, public_key_fingerprint
from .federation_local_profile import local_peer_id
from .federation_moderation import FederationModerationStore, LOCAL_SOURCE
from .federation_moderation_client import send_report, sync_blacklist

bp = Blueprint("federation_moderation_admin", __name__, url_prefix="/admin/federation/moderation")


def _store():
    return FederationModerationStore(current_app.config["DOCUMENT_ROOT"])


def _actor():
    return str(g.user["username"])


@bp.get("")
@admin_required
def dashboard():
    store = _store()
    sources = store.sources()
    bans = store.bans()
    for ban in bans:
        ban["expires_label"] = _date(ban["expires_at"]) if ban["expires_at"] else "Dauerhaft"
    for source in sources:
        source["fingerprint"] = public_key_fingerprint(source["public_key"])
        source["synced_label"] = _date(source["synced_at"]) if source["synced_at"] else "Noch nicht synchronisiert"
    return render_template(
        "admin/federation_moderation.html", bans=bans, sources=sources,
        reports=store.reports(), peers=store.store.list_peers(), local_source=LOCAL_SOURCE,
        local_peer=local_peer_id(), identity=FederationIdentity(store.root).public_identity(),
        is_master=bool(build_master.LICENSE_MASTER_MODE),
    )


def _date(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


@bp.post("/actions")
@admin_required
def action():
    store = _store()
    operation = request.form.get("operation", "")
    try:
        if request.form.get("confirm") != "1":
            raise ValueError("confirmation required")
        target = request.form.get("peer_id", "")
        if operation == "ban":
            hours = request.form.get("expiry_hours", "").strip()
            if hours and not 1 <= int(hours) <= 8760:
                raise ValueError("invalid expiry")
            store.ban(target, request.form.get("reason", ""), actor=_actor(),
                      expires_at=int(time.time()) + int(hours) * 3600 if hours else None,
                      published=request.form.get("published") == "1")
            flash("Peer gesperrt. Laufende Transfers und V2-Freigaben wurden gestoppt.")
        elif operation == "unban":
            store.unban(target, actor=_actor())
            flash("Eigene Sperre aufgehoben. Sperren anderer Quellen und lokale V2-Sperren bleiben wirksam.")
        elif operation == "subscribe":
            store.subscribe(target, request.form.get("public_key", "").strip(), actor=_actor())
            flash("Blacklist-Quelle mit festem Schlüssel gespeichert. Jetzt synchronisieren.")
        elif operation == "unsubscribe":
            store.unsubscribe(target, actor=_actor())
            flash("Quelle und ihre übernommenen Sperren entfernt. Eigene Sperren bleiben erhalten.")
        elif operation == "sync":
            count = sync_blacklist(store.root, target)
            flash(f"Blacklist geprüft und übernommen: {count} Einträge.")
        elif operation == "report":
            send_report(store.root, request.form.get("receiver", ""), target, request.form.get("reason", ""))
            flash("Meldung übermittelt. Die empfangende Verwaltung entscheidet über die Sperre.")
        elif operation == "review":
            store.review(request.form.get("report_id", ""), request.form.get("decision", ""),
                         actor=_actor(), published=request.form.get("published") == "1")
            flash("Meldung geprüft und Entscheidung gespeichert.")
        elif operation == "delete-report":
            store.delete_report(request.form.get("report_id", ""), actor=_actor())
            flash("Erledigte Meldung entfernt.")
        else:
            raise ValueError("unknown moderation action")
    except (OSError, RuntimeError, TypeError, ValueError):
        current_app.logger.warning("Federation moderation action failed: %s", operation[:32])
        flash("Aktion fehlgeschlagen. Eingaben, Bestätigung, Peer-Zugang und festgelegten Schlüssel prüfen.")
    return redirect(url_for("federation_moderation_admin.dashboard"))
