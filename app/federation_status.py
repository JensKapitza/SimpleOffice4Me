"""Read-only, opt-in federation health status for authenticated users."""
from __future__ import annotations

import time

from flask import Blueprint, current_app, render_template

from .auth import login_required
from .federation_store import FederationStore

bp = Blueprint("federation_status", __name__, url_prefix="/federation")
STALE_AFTER_SECONDS = 300


def _status(row: dict | None, now: int) -> dict[str, str]:
    if row is None:
        return {"label": "unbekannt"}
    stale = now - int(row["checked_at"]) > STALE_AFTER_SECONDS
    label = "veraltet" if stale else "erreichbar" if row["status"] == "success" else "nicht erreichbar"
    return {"label": label}


@bp.get("/diagnostics")
@login_required
def shared_diagnostics():
    store = FederationStore(current_app.config["DOCUMENT_ROOT"])
    visible = []
    peers = []
    for peer in store.list_peers():
        policy = peer.get("policy") or {}
        shared = policy.get("network_diagnostics", {}) if isinstance(policy, dict) else {}
        if peer.get("enabled") and isinstance(shared, dict) and shared.get("share_status_with_users") is True:
            peers.append(peer)
    records = store.network_diagnostics([peer["peer_id"] for peer in peers])
    now = int(time.time())
    for peer in peers:
        rows = records.get(peer["peer_id"], [])
        by_direction = {row["direction"]: row for row in rows if row["source"] in {"local", peer["peer_id"]}}
        visible.append({
            "label": str(peer.get("label") or "Federation-Peer")[:160],
            "outbound": _status(by_direction.get("outbound"), now),
            "inbound": _status(by_direction.get("inbound"), now),
        })
    return render_template("federation/diagnostics.html", peers=visible)
