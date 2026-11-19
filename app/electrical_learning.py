"""Offline electrical safety and fundamentals learning page."""

from flask import Blueprint, render_template, request

from .auth import login_required


bp = Blueprint("electrical_learning", __name__, url_prefix="/lernen/elektrotechnik")


@bp.after_request
def _private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.get("")
@bp.get("/")
@login_required
def index():
    audience = request.args.get("audience", "adult")
    if audience not in {"adult", "kids"}:
        audience = "adult"
    return render_template("learning/electrical.html", audience=audience, kids_mode=(audience == "kids"))
