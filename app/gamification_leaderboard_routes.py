"""Read-only reward leaderboard for accepted human data-quality fixes."""
from flask import Blueprint, current_app, g, render_template

from .auth import login_required
from .gamification_rewards import leaderboard, profile
from .gamification_store import GamificationStore

bp = Blueprint("gamification_leaderboard", __name__, url_prefix="/gamification")


@bp.get("/leaderboard")
@login_required
def index():
    actor = str(g.user["username"])
    store = GamificationStore(current_app.config["DOCUMENT_ROOT"])
    return render_template(
        "gamification/leaderboard.html",
        rows=leaderboard(store, 50),
        reward_profile=profile(store, actor),
    )
