"""Federated audio test tone (render locally, play best-effort)."""
from __future__ import annotations

import hmac
import time
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

from . import federation_local_identity as _identity
from .audio_output_engine import play_file, render_preset
from .federation_moderation_auth import legacy_peer_allowed
from .federation_store import FederationStore

bp = Blueprint("federation_audio_http", __name__, url_prefix="/federation/v1/audio")


def _authorized() -> bool:
    if not legacy_peer_allowed(current_app.config["DOCUMENT_ROOT"], request):
        return False
    expected = _identity.shared_token()
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.startswith("Bearer ") else ""
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return True
    # Keep the existing test-client contract: test requests without configured
    # federation credentials are authorized before production peer proofs.
    if current_app.testing and not expected:
        return True
    if request.endpoint == "federation_audio_http.test_tone":
        try:
            from .federation_peer_auth import authenticate
            root = current_app.config["DOCUMENT_ROOT"]
            peer_id = authenticate(root, request)
            return peer_id not in FederationStore(root).banned_peer_ids()
        except (TypeError, ValueError):
            return False
    return False


@bp.before_request
def authenticate_audio():
    if not _authorized():
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"},
        )
    return None


@bp.post("/test-tone")
def test_tone():
    """Render a short preset tone and try to play it on this peer.

    Reports both stages honestly: rendering always works, playback needs
    a local audio player. Intended for the admin peer diagnostics.
    """
    started = time.monotonic()
    root = Path(current_app.config["DOCUMENT_ROOT"])
    try:
        path = render_preset("gong", root / ".simpleoffice-cache" / "federation-audio-test")
        rendered = True
    except (OSError, RuntimeError, ValueError) as exc:
        return jsonify({"ok": False, "stage": "render", "error": str(exc)[:200]}), 500
    try:
        play_file(path)
        played, player_error = True, ""
    except Exception as exc:
        played, player_error = False, f"{type(exc).__name__}: {str(exc)[:160]}"
    FederationStore(root).record_event(
        "federation_audio_test_tone", peer_id="",
        detail={"rendered": rendered, "played": played,
                "latency_ms": int((time.monotonic() - started) * 1000)},
    )
    response = jsonify({"ok": True, "rendered": rendered, "played": played,
                        "player_error": player_error,
                        "latency_ms": int((time.monotonic() - started) * 1000)})
    response.headers["Cache-Control"] = "no-store"
    return response
