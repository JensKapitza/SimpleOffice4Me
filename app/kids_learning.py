"""Locally served learning games for children in the Kids area."""
import base64
import ipaddress
from urllib.parse import urlsplit, urlunsplit

from flask import Blueprint, abort, current_app, render_template, request, url_for

from .auth import login_required
from .federation_qr_image import render_qr_svg
from .settings_store import SettingsStore


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
    parts = urlsplit(game_url)
    hostname = (parts.hostname or "").casefold()
    is_loopback = hostname == "localhost"
    try:
        is_loopback = is_loopback or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        pass
    if is_loopback:
        proxy = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()["lan_proxy"]
        try:
            proxy_address = ipaddress.ip_address(proxy["bind_host"])
        except (KeyError, ValueError):
            proxy_address = ipaddress.ip_address("127.0.0.1")
        if proxy.get("enabled") and not proxy_address.is_loopback and not proxy_address.is_unspecified:
            authority = f"[{proxy_address.compressed}]" if proxy_address.version == 6 else proxy_address.compressed
            scheme = "https" if proxy.get("tls_cert") else "http"
            game_url = urlunsplit((scheme, f"{authority}:{proxy['port']}", parts.path, parts.query, ""))
            is_loopback = False
    qr_data = base64.b64encode(render_qr_svg(game_url).encode("utf-8")).decode("ascii")
    return render_template("kids/share_qr.html", game_url=game_url, qr_data=qr_data, local_only=is_loopback)
