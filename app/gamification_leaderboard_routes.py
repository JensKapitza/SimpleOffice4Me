"""Read-only reward leaderboard scoped to users who share game sessions."""
from flask import Blueprint, current_app, g, render_template

from .auth import login_required
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
