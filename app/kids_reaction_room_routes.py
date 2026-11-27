"""Private same-origin endpoints, mounted on the existing Kids game blueprint."""
import ipaddress
from urllib.parse import urlsplit

from flask import current_app, g, jsonify, make_response, render_template, request, session, url_for

from .auth import login_required
from .desktop_proxy_status import _lan_proxy_status
from .federation_qr_image import render_qr_svg
from .kids_reaction_room import CODE, MAX_TICKETS, TOKEN, ReactionRooms, RoomError, digest


PRIVATE_NETWORKS = tuple(map(ipaddress.ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
)))
ACTION_FIELDS = {
    "host": frozenset(), "join": {"code"}, "state": frozenset(),
    "start": {"mode"}, "next": frozenset(), "stop": frozenset(), "leave": frozenset(),
    "ack": {"ticket"}, "press": {"ticket", "ms"},
}


def _local_address(value: str | None) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback or any(address in network for network in PRIVATE_NETWORKS)


def _local_origin(raw: str) -> bool:
    try:
        origin = urlsplit(raw)
        port = origin.port
    except ValueError:
        return False
    return (
        origin.scheme == "https" and (port is None or 0 < port <= 65535)
        and not (origin.username or origin.password or origin.query or origin.fragment)
        and origin.path in {"", "/"}
        and (origin.hostname == "localhost" or _local_address(origin.hostname))
    )


def _room_network() -> bool:
    # The app's LAN proxy middleware already authenticates and consumes proxy
    # headers before rewriting these WSGI values. Never consume raw headers
    # here or accept a public hostname merely because its backend is loopback.
    return (
        request.is_secure and _local_origin(request.host_url) and _local_address(request.remote_addr)
        and not any(name.lower().startswith(("x-forwarded-", "x-real-ip", "forwarded"))
                    for name in request.headers.keys())
    )


def _response(value, status=200):
    response = make_response(value, status)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), display-capture=(), serial=()"
    )
    return response


@login_required
def room_page():
    allowed = _room_network()
    lan_url = ""
    if _local_address(request.remote_addr):
        proxy = _lan_proxy_status(current_app.config["DOCUMENT_ROOT"])
        origin = proxy.get("origin", "")
        if proxy.get("state") == "running" and _local_origin(origin):
            lan_url = origin.rstrip("/") + url_for("kids_reaction.rooms")
    return _response(render_template(
        "kids/reaction_rooms.html", kids_mode=True, rooms_allowed=allowed, room_lan_url=lan_url,
    ))


def _action():
    if request.mimetype != "application/json" or not 2 <= (request.content_length or 0) <= 256:
        raise RoomError("bad_request", 400)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise RoomError("bad_request", 400)
    action = data.get("action")
    if not isinstance(action, str) or action not in ACTION_FIELDS or set(data) != ACTION_FIELDS[action] | {"action"}:
        raise RoomError("bad_request", 400)
    if action == "join":
        code = data["code"]
        if not isinstance(code, str) or len(code) > 16 or not CODE.fullmatch(code.strip().upper()):
            raise RoomError("bad_request", 400)
        data["code"] = code.strip().upper()
    elif action == "start" and data["mode"] not in ("duel", "practice"):
        raise RoomError("bad_request", 400)
    if action in {"ack", "press"} and (type(data["ticket"]) is not int or not 1 <= data["ticket"] <= MAX_TICKETS):
        raise RoomError("bad_request", 400)
    if action == "press" and (type(data["ms"]) is not int or not 0 <= data["ms"] <= 120_000):
        raise RoomError("bad_request", 400)
    return action, data


@login_required
def room_qr():
    # QR encodes the same short code the host reads aloud — no new secret.
    # The payload URL is display-only for the host's own browser.
    if not _room_network():
        return _response(jsonify(error="local_https_only"), 403)
    code = (request.args.get("code", "") or "").strip().upper()
    if len(code) > 16 or not CODE.fullmatch(code):
        return _response(jsonify(error="bad_request"), 400)
    join_url = request.host_url.rstrip("/") + url_for("kids_reaction.rooms") + "?code=" + code
    response = make_response(render_qr_svg(join_url), 200)
    response.headers["Content-Type"] = "image/svg+xml; charset=utf-8"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@login_required
def room_api():
    # Browser mutations also pass the normal app CSRF gate. Do not accept
    # foreign Origin even when the calling browser might have a valid token.
    if not _room_network() or request.headers.get("Origin", request.host_url.rstrip("/")) != request.host_url.rstrip("/"):
        return _response(jsonify(error="local_https_only"), 403)
    session_key = session.get("_csrf_token")
    if not isinstance(session_key, str) or not session_key or not session_key.isascii():
        return _response(jsonify(error="session_required"), 403)
    actor = g.user["id"]
    browser = digest(session_key)
    rooms = current_app.extensions.get("kids_reaction_rooms")
    if rooms is None:
        rooms = current_app.extensions.setdefault("kids_reaction_rooms", ReactionRooms())
    try:
        action, data = _action()
        if action in {"host", "join"}:
            return _response(jsonify(rooms.enter(actor, browser, data.get("code"))), 201)
        token = request.headers.get("X-Reaction-Station", "")
        if not TOKEN.fullmatch(token):
            raise RoomError("room_gone", 410)
        return _response(jsonify(rooms.act(actor, browser, token, action, data)))
    except RoomError as exc:
        return _response(jsonify(error=exc.code), exc.status)
