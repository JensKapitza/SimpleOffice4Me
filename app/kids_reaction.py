"""Logged-in, local-first reaction course; there is no server serial API."""
from urllib.parse import urlsplit

from flask import Blueprint, make_response, render_template, request

from .auth import login_required
from .kids_reaction_room_routes import room_api, room_page, room_qr


bp = Blueprint("kids_reaction", __name__, url_prefix="/kids/reaktionsparcours")


def _direct_loopback() -> bool:
    try:
        host = urlsplit(request.host_url).hostname
    except ValueError:
        return False
    return (
        host in {"localhost", "127.0.0.1", "::1"}
        and request.remote_addr in {"127.0.0.1", "::1"}
        and not any(request.headers.get(header) for header in (
            "Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
            "X-Forwarded-Prefix", "X-Real-IP",
        ))
    )


@bp.get("/")
@login_required
def index():
    usb = _direct_loopback()
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
