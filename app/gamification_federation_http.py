"""Federation HTTP surface for peer-bound gamification challenges."""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, send_file

from .access_control import has_feature
from .db import get_db
from .federation_store import FederationStore
from .gamification_adapters import (
    contact_candidates,
    document_candidates,
    image_candidates,
    image_preview_path,
)
from .gamification_federation import authenticate_peer, minimal_challenge_envelope, peer_allows
from .gamification_providers import Challenge, get_provider
from .gamification_store import GamificationStore

bp = Blueprint("gamification_federation_http", __name__, url_prefix="/federation/v1/gamification")
MAX_BODY_BYTES = 64 * 1024
FEATURE_BY_PROVIDER = {"contacts": "contacts", "images": "documents", "documents": "documents"}


def _root() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"])


def _game() -> GamificationStore:
    return GamificationStore(_root())


def _federation() -> FederationStore:
    return FederationStore(_root())


def _raw_body() -> bytes:
    length = request.content_length
    if length is not None and length > MAX_BODY_BYTES:
        raise ValueError("gamification federation request is too large")
    body = request.get_data(cache=True, as_text=False)
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("gamification federation request is too large")
    return body


def _actor(peer_id: str) -> str:
    return f"peer:{peer_id}"


def _source_still_allowed(challenge: dict) -> bool:
    owner = str(challenge.get("created_by", "")).strip()
    provider = str(challenge.get("provider", "")).strip()
    object_ref = str(challenge.get("object_ref", "")).strip()
    feature = FEATURE_BY_PROVIDER.get(provider)
    if not owner or not feature or not object_ref:
        return False
    user = get_db().execute("SELECT * FROM user WHERE username=?", (owner,)).fetchone()
    if user is None or not has_feature(user, feature):
        return False
    if provider == "contacts":
        candidates = contact_candidates(_root(), owner)
    elif provider == "images":
        candidates = image_candidates(_root(), owner)
    elif provider == "documents":
        candidates = document_candidates(_root(), owner)
    else:
        return False
    return any(item.object_ref == object_ref for item in candidates)


def _next_challenge(store: GamificationStore, session_id: str, actor: str, peer: dict) -> dict | None:
    with store._db() as db:
        rows = db.execute(
            "SELECT c.id FROM game_challenge c "
            "JOIN game_item i ON i.id=c.item_id "
            "JOIN game_session s ON s.id=i.session_id "
            "WHERE s.id=? AND s.scope='federation' AND s.status='active' AND c.status='open' "
            "AND EXISTS(SELECT 1 FROM game_participant gp WHERE gp.session_id=s.id "
            "AND gp.participant=? AND gp.role IN ('answer','owner')) "
            "ORDER BY c.created_at,c.id LIMIT 100",
            (session_id, actor),
        ).fetchall()
    for row in rows:
        challenge = store.get_challenge_for_actor(str(row["id"]), actor)
        if challenge is None:
            continue
        provider = str(challenge.get("provider", ""))
        if peer_allows(peer, "send_challenges", provider=provider) and _source_still_allowed(challenge):
            return challenge
    return None


def _deny(message: str = "gamification federation access denied") -> Response:
    return jsonify({"error": message}), 403


@bp.get("/sessions/<session_id>/next")
def next_challenge(session_id: str):
    body = b""
    try:
        peer, proof = authenticate_peer(
            _federation(), request.headers, method=request.method, path=request.path,
            body=body, required_permission="send_challenges",
        )
        actor = _actor(proof.peer_id)
        challenge = _next_challenge(_game(), session_id, actor, peer)
        if challenge is None:
            return Response(status=204, headers={"Cache-Control": "no-store"})
        envelope = minimal_challenge_envelope(challenge)
        envelope["session_id"] = session_id[:80]
        envelope["preview_endpoint"] = (
            f"/federation/v1/gamification/challenges/{envelope['challenge_id']}/preview"
            if challenge["provider"] == "images" and challenge.get("payload", {}).get("preview") is True
            else ""
        )
        return jsonify(envelope), 200, {"Cache-Control": "no-store"}
    except ValueError:
        return _deny()


@bp.get("/challenges/<challenge_id>/preview")
def challenge_preview(challenge_id: str):
    body = b""
    try:
        peer, proof = authenticate_peer(
            _federation(), request.headers, method=request.method, path=request.path,
            body=body, required_permission="send_previews", provider="images",
        )
        actor = _actor(proof.peer_id)
        challenge = _game().get_challenge_for_actor(challenge_id, actor)
        if challenge is None or challenge.get("provider") != "images" or not _source_still_allowed(challenge):
            return _deny()
        if not peer_allows(peer, "send_previews", provider="images"):
            return _deny()
        preview = image_preview_path(_root(), str(challenge.get("created_by", "")), str(challenge["object_ref"]))
        if preview is None:
            return Response(status=404, headers={"Cache-Control": "no-store"})
        response = send_file(preview, mimetype="image/webp", conditional=False, max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except ValueError:
        return _deny()


@bp.post("/challenges/<challenge_id>/answer")
def submit_answer(challenge_id: str):
    try:
        body = _raw_body()
        peer, proof = authenticate_peer(
            _federation(), request.headers, method=request.method, path=request.path,
            body=body, required_permission="receive_answers",
        )
        actor = _actor(proof.peer_id)
        store = _game()
        persisted = store.get_challenge_for_actor(challenge_id, actor)
        if persisted is None or not _source_still_allowed(persisted):
            return _deny()
        provider_name = str(persisted["provider"])
        if not peer_allows(peer, "receive_answers", provider=provider_name):
            return _deny()
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return jsonify({"error": "invalid JSON"}), 400
        if not isinstance(payload, dict):
            return jsonify({"error": "invalid JSON object"}), 400
        action = str(payload.get("action", "answer")).strip().casefold()
        if action in {"unknown", "skip"}:
            store.skip_challenge(challenge_id, actor, disposition=action)
            return jsonify({"status": action}), 200, {"Cache-Control": "no-store"}
        if action != "answer":
            return jsonify({"error": "invalid action"}), 400
        value = payload.get("answer")
        challenge = Challenge(
            provider=provider_name,
            object_ref=str(persisted["object_ref"]),
            kind=str(persisted["kind"]),
            prompt=str(persisted["prompt"]),
            answer_type=str(persisted["answer_type"]),
            payload=dict(persisted.get("payload", {})),
        )
        provider = get_provider(provider_name)
        if not provider.validate_answer(challenge, value):
            return jsonify({"error": "invalid answer"}), 400
        proposal_id = store.answer_challenge(challenge_id, actor, value, source="human")
        return jsonify({"status": "proposed", "proposal_id": proposal_id}), 201, {"Cache-Control": "no-store"}
    except ValueError:
        return _deny()
