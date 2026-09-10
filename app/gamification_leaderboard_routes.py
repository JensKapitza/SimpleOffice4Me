"""Reward leaderboard and small user-facing data-roulette actions."""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import has_feature
from .auth import login_required
from .gamification_document_selection import set_document_game_release
from .gamification_proposal_actions import ProposalConflict, apply_supported_proposal
from .gamification_rewards import award_accepted_proposal, leaderboard, profile
from .gamification_store import GamificationStore

bp = Blueprint("gamification_leaderboard", __name__, url_prefix="/gamification")


def _visible_participants(store: GamificationStore, actor: str) -> set[str]:
    visible = {actor}
    with store._db() as db:
        sessions = db.execute(
            "SELECT id,created_by FROM game_session s WHERE s.status='active' AND "
            "(s.created_by=? OR EXISTS(SELECT 1 FROM game_participant gp "
            "WHERE gp.session_id=s.id AND gp.participant=?))",
            (actor, actor),
        ).fetchall()
        for session in sessions:
            creator = str(session["created_by"])
            if creator and not creator.startswith("peer:"):
                visible.add(creator)
            rows = db.execute(
                "SELECT participant FROM game_participant WHERE session_id=?",
                (str(session["id"]),),
            ).fetchall()
            visible.update(
                str(row["participant"]) for row in rows
                if str(row["participant"]) and not str(row["participant"]).startswith("peer:")
            )
    return visible


def _owned_proposal(store: GamificationStore, proposal_id: str, actor: str):
    for scope in ("local", "organization", "federation"):
        proposal = store.get_proposal_for_actor(proposal_id, actor, scope=scope)
        if proposal is not None and str(proposal.get("created_by", "")) == actor:
            return proposal
    return None


@bp.get("/leaderboard")
@login_required
def index():
    actor = str(g.user["username"])
    store = GamificationStore(current_app.config["DOCUMENT_ROOT"])
    return render_template(
        "gamification/leaderboard.html",
        rows=leaderboard(store, 50, _visible_participants(store, actor)),
        reward_profile=profile(store, actor),
    )


@bp.post("/documents/<document_id>/release")
@login_required
def document_release(document_id: str):
    """Toggle the persistent roulette opt-in from a document screen."""
    if not has_feature(g.user, "documents"):
        abort(403)
    released = str(request.form.get("released", "1")).strip() == "1"
    try:
        set_document_game_release(
            current_app.config["DOCUMENT_ROOT"],
            str(g.user["username"]),
            document_id,
            released,
        )
    except ValueError as exc:
        flash(str(exc)[:180])
        return redirect(url_for("documents.detail", document_id=document_id))
    flash("Für Daten-Roulette freigegeben." if released else "Daten-Roulette-Freigabe entfernt.")
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.post("/proposals/<proposal_id>/apply")
@login_required
def apply_proposal(proposal_id: str):
    """Apply one reviewed roulette proposal to its original object.

    Only the creator/owner of the game session may mutate source data. Remote
    and local players can propose values, but cannot use this endpoint to gain
    write access. Existing contact values require a second explicit submit.
    """
    actor = str(g.user["username"])
    if not proposal_id or len(proposal_id) > 80:
        abort(404)
    store = GamificationStore(current_app.config["DOCUMENT_ROOT"])
    proposal = _owned_proposal(store, proposal_id, actor)
    if proposal is None:
        abort(403)
    if proposal.get("accepted_by"):
        return redirect(url_for("gamification.manage", message="Vorschlag wurde bereits übernommen."))

    replace_existing = str(request.form.get("replace_existing", "0")).strip() == "1"
    try:
        applied, message = apply_supported_proposal(
            current_app.config["DOCUMENT_ROOT"], actor, proposal,
            replace_existing=replace_existing,
        )
    except ProposalConflict as conflict:
        message = (
            f"Kontaktfeld {conflict.field_name} enthält bereits „{conflict.current_value[:80]}“. "
            f"Vorschlag: „{conflict.proposed_value[:80]}“. Ersetzen nur nach erneuter Bestätigung."
        )
        return redirect(url_for(
            "gamification.manage", message=message,
            replace_proposal=proposal_id,
        ))
    except ValueError as exc:
        return redirect(url_for("gamification.manage", message=f"Übernahme nicht möglich: {str(exc)[:180]}"))

    mode = "source_applied" if applied else "confirmed_metadata"
    store.accept(proposal_id, actor, mode=mode)
    award_accepted_proposal(store, proposal_id)
    return redirect(url_for("gamification.manage", message=message))
