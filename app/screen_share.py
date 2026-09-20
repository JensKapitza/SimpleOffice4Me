"""Cross-platform screen sharing and Miracast/Wi-Fi Display launcher."""
from __future__ import annotations

import functools
import json
import platform
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .access_control import audit
from .auth import login_required
from .federation_qr_image import render_qr_svg


bp = Blueprint("screen", __name__, url_prefix="/screen")
_WINDOWS_CAPABILITY = "App.WirelessDisplay.Connect~~~~0.0.1.0"
_SESSION_TTL_SECONDS = 1800
_MAX_SIGNAL_MESSAGES = 256
_MAX_SESSIONS = 64
_MAX_SESSIONS_PER_USER = 4
_MAX_SIGNAL_BYTES = 128000
_MAX_SESSION_SIGNAL_BYTES = 512000
_sessions_lock = threading.RLock()
_sessions: dict[str, dict[str, Any]] = {}


def _system() -> str:
    return platform.system().strip().casefold()


def _android_client() -> bool:
    return "simpleoffice4me-android/" in request.headers.get("User-Agent", "").casefold()


def _flatpak_network_displays() -> bool:
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return False
    try:
        result = subprocess.run([flatpak, "info", "org.gnome.NetworkDisplays"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@functools.lru_cache(maxsize=1)
def _windows_wireless_display_state() -> str:
    if _system() != "windows":
        return "not-applicable"
    dism = shutil.which("dism.exe") or shutil.which("dism")
    if not dism:
        return "unknown"
    try:
        result = subprocess.run([dism, "/Online", "/Get-CapabilityInfo", f"/CapabilityName:{_WINDOWS_CAPABILITY}", "/English"], capture_output=True, text=True, timeout=12, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = (result.stdout + "\n" + result.stderr).casefold()
    if "state : installed" in text:
        return "installed"
    if "state : not present" in text:
        return "not-installed"
    return "unknown"


def platform_status() -> dict[str, Any]:
    system = _system()
    native_gnome = shutil.which("gnome-network-displays")
    miracle_sink = shutil.which("miracle-sinkctl")
    miracle_wifi = shutil.which("miracle-wifid")
    return {
        "platform": system or "unknown", "browser_capture": True, "webrtc": True,
        "android_native": _android_client(),
        "windows": {"available": system == "windows", "wireless_display": _windows_wireless_display_state()},
        "linux": {
            "available": system == "linux", "gnome_network_displays": bool(native_gnome),
            "gnome_network_displays_path": native_gnome or "",
            "gnome_network_displays_flatpak": _flatpak_network_displays() if system == "linux" else False,
            "miracle_sinkctl": bool(miracle_sink), "miracle_sinkctl_path": miracle_sink or "",
            "miracle_wifid": bool(miracle_wifi), "miracle_wifid_path": miracle_wifi or "",
        },
    }


def _open_windows_uri(uri: str) -> None:
    explorer = shutil.which("explorer.exe") or shutil.which("explorer")
    if not explorer:
        raise RuntimeError("Windows Explorer wurde nicht gefunden")
    subprocess.Popen([explorer, uri], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _launch_linux_sender() -> None:
    executable = shutil.which("gnome-network-displays")
    if executable:
        command = [executable]
    elif _flatpak_network_displays():
        flatpak = shutil.which("flatpak")
        if not flatpak:
            raise RuntimeError("Flatpak wurde nicht gefunden")
        command = [flatpak, "run", "org.gnome.NetworkDisplays"]
    else:
        raise RuntimeError("GNOME Network Displays ist nicht installiert")
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _launch_linux_receiver_control() -> None:
    sinkctl = shutil.which("miracle-sinkctl")
    if not sinkctl:
        raise RuntimeError("MiracleCast (miracle-sinkctl) ist nicht installiert")
    terminals = (("x-terminal-emulator", ["-e"]), ("kgx", ["--"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]), ("xterm", ["-e"]))
    for name, prefix in terminals:
        terminal = shutil.which(name)
        if terminal:
            subprocess.Popen([terminal, *prefix, sinkctl, "--uibc"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
    raise RuntimeError("Kein Terminal zum Starten von miracle-sinkctl gefunden")


def _perform_action(action: str) -> str:
    system = _system()
    if action == "windows-send" and system == "windows":
        _open_windows_uri("ms-settings-connectabledevices:devicediscovery")
        return "Windows-Suche nach drahtlosen Anzeigen wurde geöffnet."
    if action == "windows-receive" and system == "windows":
        _open_windows_uri("ms-settings:project")
        return "Windows-Einstellungen für 'Projizieren auf diesen PC' wurden geöffnet."
    if action == "linux-send" and system == "linux":
        _launch_linux_sender()
        return "GNOME Network Displays wurde gestartet."
    if action == "linux-receive" and system == "linux":
        _launch_linux_receiver_control()
        return "MiracleCast Receiver-Steuerung wurde geöffnet."
    raise ValueError("Diese Bildschirm-Aktion ist auf diesem System nicht verfügbar")


def _actor_id() -> str:
    user = getattr(g, "user", None)
    if user is None:
        return ""
    for key in ("id", "username"):
        try:
            value = user[key]
        except (KeyError, TypeError):
            continue
        if value is not None:
            return str(value)
    return ""


def _purge_sessions() -> None:
    now = time.time()
    for key in [key for key, value in _sessions.items() if value.get("closed") or now - float(value["updated_at"]) > _SESSION_TTL_SECONDS]:
        _sessions.pop(key, None)


def _new_join_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(50):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if all(value.get("join_code") != code for value in _sessions.values()):
            return code
    raise RuntimeError("Keine freie Session-Kennung verfügbar")


def _snapshot(session_id: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"session_id": session_id, "join_code": value["join_code"], "created_at": value["created_at"], "updated_at": value["updated_at"], "connected": bool(value.get("connected")), "closed": bool(value.get("closed"))}


@bp.get("")
@login_required
def index():
    return render_template("screen/index.html", screen_status=platform_status())


@bp.get("/status")
@login_required
def status():
    return jsonify(platform_status())


@bp.post("/action/<action>")
@login_required
def action(action: str):
    if action not in {"windows-send", "windows-receive", "linux-send", "linux-receive"}:
        abort(404)
    try:
        message = _perform_action(action)
    except (OSError, RuntimeError, ValueError) as exc:
        audit("screen_platform_action", "screen", action, outcome="failure", detail={"platform": _system(), "error_type": type(exc).__name__})
        flash(str(exc))
    else:
        audit("screen_platform_action", "screen", action, detail={"platform": _system()})
        flash(message)
    return redirect(url_for("screen.index"))


@bp.post("/api/sessions")
@login_required
def create_session():
    now = time.time()
    with _sessions_lock:
        _purge_sessions()
        owner = _actor_id()
        if len(_sessions) >= _MAX_SESSIONS or sum(value["owner"] == owner for value in _sessions.values()) >= _MAX_SESSIONS_PER_USER:
            return jsonify(error="Zu viele Bildschirm-Sessions. Nicht benötigte Freigaben beenden.", code="session_limit"), 429
        session_id = secrets.token_urlsafe(18)
        _sessions[session_id] = {"owner": owner, "join_code": _new_join_code(), "created_at": now, "updated_at": now, "connected": False, "closed": False, "sequence": 0, "messages": [], "signal_bytes": 0}
        snapshot = _snapshot(session_id, _sessions[session_id])
    audit("screen_session_created", "screen_session", session_id)
    return jsonify({"schema": 1, "session": snapshot}), 201


@bp.get("/api/sessions/<session_id>/connect-qr.svg")
@login_required
def connection_qr(session_id: str):
    with _sessions_lock:
        _purge_sessions()
        value = _sessions.get(session_id)
        if value is None or value.get("closed"):
            abort(404)
        if value["owner"] != _actor_id():
            abort(403)
        payload = "simpleoffice://screen/" + value["join_code"]
    return Response(
        render_qr_svg(payload),
        content_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@bp.post("/api/join")
@login_required
def resolve_session():
    body, _ = _signal_body()
    code = body.get("code")
    if not isinstance(code, str) or not 1 <= len(code.strip()) <= 16:
        abort(400)
    normalized = code.strip().upper()
    with _sessions_lock:
        _purge_sessions()
        for session_id, value in _sessions.items():
            if value["join_code"] == normalized and not value.get("closed"):
                return jsonify({"schema": 1, "session": _snapshot(session_id, value)})
    abort(404)


def _authorized(value: dict[str, Any], role: str, code: str) -> bool:
    return (role == "sender" and value["owner"] == _actor_id()) or (role == "receiver" and code == value["join_code"])


@bp.get("/api/sessions/<session_id>/signals")
@login_required
def signals(session_id: str):
    role = str(request.args.get("role") or "").strip().casefold()
    code = request.headers.get("X-Screen-Code", "").strip().upper()
    try:
        after = max(0, int(request.args.get("after") or 0))
    except ValueError:
        abort(400)
    if role not in {"sender", "receiver"}:
        abort(400)
    with _sessions_lock:
        _purge_sessions()
        value = _sessions.get(session_id)
        if value is None or value.get("closed"):
            abort(404)
        if not _authorized(value, role, code):
            abort(403)
        messages = [item for item in value["messages"] if int(item["seq"]) > after and item["from"] != role]
        value["updated_at"] = time.time()
        return jsonify({"schema": 1, "messages": messages, "last_sequence": int(value["sequence"]), "connected": bool(value.get("connected"))})


def _signal_body():
    if not request.is_json:
        abort(415)
    raw = request.stream.read(_MAX_SIGNAL_BYTES + 1)
    if len(raw) > _MAX_SIGNAL_BYTES:
        abort(413)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        abort(400)
    if not isinstance(body, dict):
        abort(400)
    return body, len(raw)


@bp.post("/api/sessions/<session_id>/signals")
@login_required
def post_signal(session_id: str):
    body, payload_bytes = _signal_body()
    role = str(body.get("role") or "").strip().casefold()
    kind = str(body.get("type") or "").strip().casefold()
    code = str(body.get("code") or "").strip().upper()
    payload = body.get("payload")
    if role not in {"sender", "receiver"} or kind not in {"offer", "answer", "ice", "ready", "bye"}:
        abort(400)
    if kind in {"offer", "answer"}:
        if role != ("sender" if kind == "offer" else "receiver"):
            abort(400)
        if not isinstance(payload, dict) or payload.get("type") != kind or not isinstance(payload.get("sdp"), str) or not payload["sdp"].strip():
            abort(400)
    elif kind == "ice":
        if not isinstance(payload, dict) or not isinstance(payload.get("candidate"), str):
            abort(400)
        if payload.get("sdpMid") is not None and not isinstance(payload["sdpMid"], str):
            abort(400)
        if payload.get("sdpMLineIndex") is not None and (type(payload["sdpMLineIndex"]) is not int or payload["sdpMLineIndex"] < 0):
            abort(400)
    elif payload is not None:
        abort(400)
    with _sessions_lock:
        _purge_sessions()
        value = _sessions.get(session_id)
        if value is None or value.get("closed"):
            abort(404)
        if not _authorized(value, role, code):
            abort(403)
        if kind != "bye" and (len(value["messages"]) >= _MAX_SIGNAL_MESSAGES or value["signal_bytes"] + payload_bytes > _MAX_SESSION_SIGNAL_BYTES):
            return jsonify(error="Signaling-Limit erreicht. Freigabe neu starten.", code="signal_limit"), 409
        value["sequence"] += 1
        value["updated_at"] = time.time()
        if kind in {"answer", "ready"}:
            value["connected"] = True
        if kind == "bye":
            value["closed"] = True
            value["messages"].clear()
            value["signal_bytes"] = 0
        value["messages"].append({"seq": value["sequence"], "from": role, "type": kind, "payload": payload})
        value["signal_bytes"] += payload_bytes
        sequence = value["sequence"]
    return jsonify({"schema": 1, "accepted": True, "sequence": sequence})


@bp.delete("/api/sessions/<session_id>")
@login_required
def close_session(session_id: str):
    code = request.headers.get("X-Screen-Code", "").strip().upper()
    with _sessions_lock:
        value = _sessions.get(session_id)
        if value is None:
            return jsonify({"schema": 1, "closed": True})
        if value["owner"] != _actor_id() and code != value["join_code"]:
            abort(403)
        value["closed"] = True
        value["updated_at"] = time.time()
    audit("screen_session_closed", "screen_session", session_id)
    return jsonify({"schema": 1, "closed": True})
