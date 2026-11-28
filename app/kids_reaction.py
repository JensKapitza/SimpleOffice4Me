"""Logged-in, local-first reaction course; there is no server serial API."""
from flask import Blueprint, make_response, render_template

from .auth import login_required
from .local_device_access import direct_loopback_request
from .kids_reaction_room_routes import room_api, room_page, room_qr


bp = Blueprint("kids_reaction", __name__, url_prefix="/kids/reaktionsparcours")


@bp.get("/")
@login_required
def index():
    usb = direct_loopback_request()
    response = make_response(render_template("kids/reaction.html", kids_mode=True, usb_allowed=usb))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    serial = "(self)" if usb else "()"
    response.headers["Permissions-Policy"] = (
        f"camera=(), microphone=(), geolocation=(), display-capture=(), serial={serial}"
    )
    return response


bp.add_url_rule("/rooms", endpoint="rooms", view_func=room_page, methods=["GET"])
bp.add_url_rule("/rooms/api", endpoint="room_api", view_func=room_api, methods=["POST"])
bp.add_url_rule("/rooms/qr", endpoint="room_qr", view_func=room_qr, methods=["GET"])
