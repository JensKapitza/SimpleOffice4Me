"""Locally served learning games for children in the Kids area."""
from flask import Blueprint, render_template

from .auth import login_required


bp = Blueprint("kids_learning", __name__, url_prefix="/kids/lernen")


@bp.after_request
def _private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.get("")
@bp.get("/")
@login_required
def index():
    return render_template("kids/learning.html")


@bp.get("/spielpause")
@login_required
def mini_games():
    """Short, touch-friendly games for a quick break in the Kids area."""
    return render_template("kids/mini_games.html")
