"""Reward leaderboard and small user-facing data-roulette actions."""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import has_feature
from .auth import login_required
from .gamification_document_selection import set_document_game_release
from .gamification_rewards import leaderboard, profile
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
