"""Locally served learning games for children in the Kids area."""
import base64

from flask import Blueprint, abort, render_template, request, url_for

from .auth import login_required
from .federation_qr_image import render_qr_svg


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


@bp.get("/qr")
@login_required
def share_game_qr():
    """Show a QR shortcut that opens the selected activity on another device."""
    game = request.args.get("game", "")
    level = request.args.get("level", "preschool")
    subject = request.args.get("subject", "")
    valid_games = {"maze", "objects", "letters", "directions", "safety", "quiz"}
    valid_levels = {"preschool", "1", "2", "3", "4", "5"}
    valid_subjects = {"math", "german", "english", "world", "mixed"}
    if game not in valid_games or level not in valid_levels or (game == "quiz" and subject not in valid_subjects):
        abort(400)
    game_url = url_for("kids_learning.index", game=game, level=level, **({"subject": subject} if game == "quiz" else {}), _external=True)
    qr_data = base64.b64encode(render_qr_svg(game_url).encode("utf-8")).decode("ascii")
    return render_template("kids/share_qr.html", game_url=game_url, qr_data=qr_data)
