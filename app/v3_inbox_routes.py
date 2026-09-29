"""Admin/user view for the optional universal inbox provenance layer."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from .auth import login_required
from .v3_capabilities import enabled
from .v3_inbox import InboxStore

bp=Blueprint("v3_inbox",__name__,url_prefix="/documents/inbox-v3")

@bp.app_context_processor
def inbox_capability_context():
    return {"v3_inbox_enabled": enabled("v3.inbox")}



@bp.get("")
@login_required
def index():
    if not enabled("v3.inbox"):
        return render_template(
            "documents/v3_inbox.html",
            enabled=False,
            items=[],
            source="",
            status="",
        )
    store=InboxStore(current_app.config["DOCUMENT_ROOT"])
    source=request.args.get("source","").strip()
    status=request.args.get("status","").strip()
    return render_template(
        "documents/v3_inbox.html",
        enabled=True,
        items=store.list(source=source,status=status),
        source=source,
        status=status,
    )


@bp.get("/<item_id>")
@login_required
def detail(item_id):
    if not enabled("v3.inbox"):
        abort(404)
    store=InboxStore(current_app.config["DOCUMENT_ROOT"])
    try:
        item=store.get(item_id)
    except LookupError:
        abort(404)
    if item.actor != str(g.user["username"]) and not g.user["is_admin"]:
        abort(404)
    return render_template(
        "documents/v3_inbox_detail.html",
        item=item,
        steps=store.steps(item_id),
    )


@bp.post("/<item_id>/assign")
@login_required
def assign(item_id):
    if not enabled("v3.inbox"):
        abort(404)
    store=InboxStore(current_app.config["DOCUMENT_ROOT"])
    try:
        item=store.get(item_id)
        if item.actor != str(g.user["username"]) and not g.user["is_admin"]:
            abort(404)
        store.assign(
            item_id,
            request.form.get("target_type",""),
            request.form.get("target_id",""),
            str(g.user["username"]),
        )
    except (LookupError,ValueError):
        abort(400)
    return redirect(url_for("v3_inbox.detail",item_id=item_id))
