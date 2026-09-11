"""Simple administrator page for per-peer federated chat permissions."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, g, render_template

from .access_control import is_admin
from .auth import login_required
from .chat_policy import ACTION_LABELS, chat_policy_state
from .federation_store import FederationStore

bp = Blueprint("chat_policy_admin", __name__, url_prefix="/admin/chat-policies")


@bp.get("")
@bp.get("/")
@login_required
def index():
    if not is_admin(g.user):
        abort(403)
    peers = FederationStore(current_app.config["DOCUMENT_ROOT"]).list_peers()
    for peer in peers:
        peer["chat_policy"] = chat_policy_state(peer.get("policy"))
    return render_template(
        "admin/federation_chat_policies.html",
        peers=peers,
        chat_action_labels=ACTION_LABELS,
    )
