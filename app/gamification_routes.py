"""Web entry point for local data-quality roulette.

Only server-created opaque challenge/proposal ids are accepted from the browser.
Object references, providers and fields stay server-side and are bound to the
current actor through GamificationStore.
"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .gamification_adapters import (
    apply_contact_proposal,
    contact_candidates,
    contact_proposal_can_apply,
    document_candidates,
    image_candidates,
    image_preview_path,
)
from .gamification_engine import roulette
from .gamification_policy import GamePolicy
from .gamification_providers import Challenge, get_provider
from .gamification_store import GamificationStore

bp = Blueprint("gamification", __name__, url_prefix="/gamification")


def _policy() -> GamePolicy:
    # Local rollout: contacts, protected photo thumbnails and explicitly released
    # general files. Originals stay disabled and document contents are never sent
    # to the game.
    return GamePolicy(
        scope="local",
        providers=frozenset({"contacts", "images", "documents"}),
        collections=frozenset({"contacts", "images", "files"}),
        preview_allowed=True,
        original_allowed=False,
        submit_proposals=True,
        auto_accept_consensus=False,
    )


def _store() -> GamificationStore:
    return GamificationStore(current_app.config["DOCUMENT_ROOT"])


def _local_candidates(actor: str):
    root = current_app.config["DOCUMENT_ROOT"]
    return [
        *contact_candidates(root, actor),
        *image_candidates(root, actor),
        *document_candidates(root, actor),
    ]


def _policy_snapshot(policy: GamePolicy) -> dict[str, object]:
    return {
        "scope": policy.scope,
        "providers": sorted(policy.providers),
        "collections": sorted(policy.collections),
        "fields": sorted(policy.fields),
        "preview_allowed": policy.preview_allowed,
        "original_allowed": policy.original_allowed,
        "submit_proposals": policy.submit_proposals,
        "auto_accept_consensus": policy.auto_accept_consensus,
        "min_votes": policy.min_votes,
        "consensus_ratio": policy.consensus_ratio,
    }


def _pending_proposal(actor: str) -> dict[str, object] | None:
    proposal_id = str(request.args.get("proposal", "")).strip()
    if not proposal_id or len(proposal_id) > 80:
        return None
    proposal = _store().get_proposal_for_actor(proposal_id, actor)
    if proposal is None:
        return None
    manual_apply_supported = proposal.get("provider") == "contacts"
    return {
        "id": proposal_id,
        "field_name": str(proposal["field_name"]),
        "value": str(proposal["value"])[:500],
        "manual_apply_supported": manual_apply_supported,
        "can_apply": bool(manual_apply_supported and contact_proposal_can_apply(
            current_app.config["DOCUMENT_ROOT"], actor, proposal,
        )),
        "accepted": bool(proposal.get("accepted_by")),
    }


@bp.get("/")
@login_required
def index():
    actor = str(g.user["username"])
    policy = _policy()
    generated = roulette(policy, actor, _local_candidates(actor))
    challenge = None
    if generated is not None:
        store = _store()
        session_id = store.create_session("Lokale Daten-Roulette-Runde", "local", actor, _policy_snapshot(policy))
        resource_class = {
            "contacts": "contact",
            "images": "photo",
            "documents": "released_file",
        }.get(generated.provider)
        if resource_class is None:
            abort(404)
        item_id = store.add_item(session_id, generated.provider, generated.object_ref, resource_class)
        challenge_id = store.add_challenge(
            item_id, generated.kind, generated.answer_type, generated.prompt, generated.payload,
        )
        challenge = {
            "id": challenge_id,
            "provider": generated.provider,
            "kind": generated.kind,
            "prompt": generated.prompt,
            "answer_type": generated.answer_type,
            "payload": generated.payload,
        }
    return render_template(
        "gamification/index.html",
        challenge=challenge,
        pending_proposal=_pending_proposal(actor),
        message=request.args.get("message", "")[:200],
    )


@bp.get("/preview/<challenge_id>")
@login_required
def preview(challenge_id: str):
    """Serve only the cached thumbnail belonging to an actor-bound open challenge."""
    actor = str(g.user["username"])
    if not challenge_id or len(challenge_id) > 80:
        abort(404)
    persisted = _store().get_challenge_for_actor(challenge_id, actor)
    if persisted is None or persisted.get("provider") != "images" or persisted.get("resource_class") != "photo":
        abort(404)
    path = image_preview_path(
        current_app.config["DOCUMENT_ROOT"], actor, str(persisted.get("object_ref", "")),
    )
    if path is None:
        abort(404)
    response = send_file(path, conditional=True, etag=True, max_age=300)
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


@bp.post("/answer")
@login_required
def answer():
    actor = str(g.user["username"])
    challenge_id = str(request.form.get("challenge_id", "")).strip()
    if not challenge_id or len(challenge_id) > 80:
        return render_template("gamification/index.html", challenge=None, pending_proposal=None, message="Ungültige Spielrunde."), 400

    store = _store()
    persisted = store.get_challenge_for_actor(challenge_id, actor)
    if persisted is None:
        return render_template("gamification/index.html", challenge=None, pending_proposal=None, message="Diese Spielrunde ist nicht mehr verfügbar."), 409

    action = str(request.form.get("action", "answer")).strip().casefold()
    if action in {"unknown", "skip"}:
        store.skip_challenge(challenge_id, actor, disposition=action)
        return redirect(url_for("gamification.index", message="Übersprungen – dafür gibt es keine Ratepunkte."))
    if action != "answer":
        return render_template("gamification/index.html", challenge=None, pending_proposal=None, message="Ungültige Spielaktion."), 400

    value = str(request.form.get("answer", "")).strip()
    challenge = Challenge(
        provider=str(persisted["provider"]),
        object_ref=str(persisted["object_ref"]),
        kind=str(persisted["kind"]),
        prompt=str(persisted["prompt"]),
        answer_type=str(persisted["answer_type"]),
        payload=dict(persisted.get("payload", {})),
    )
    provider = get_provider(challenge.provider)
    if not provider.validate_answer(challenge, value):
        return render_template(
            "gamification/index.html",
            challenge={
                "id": challenge_id,
                "provider": challenge.provider,
                "kind": challenge.kind,
                "prompt": challenge.prompt,
                "answer_type": challenge.answer_type,
                "payload": challenge.payload,
            },
            pending_proposal=None,
            message="Bitte eine gültige Antwort eingeben oder ‚Weiß ich nicht‘ wählen.",
        ), 400

    proposal_id = store.answer_challenge(challenge_id, actor, value)
    return redirect(url_for(
        "gamification.index",
        proposal=proposal_id,
        message="Vorschlag gespeichert. Originaldaten wurden nicht automatisch geändert.",
    ))


@bp.post("/accept")
@login_required
def accept():
    """Explicitly apply one actor-bound contact proposal after fresh ACL checks."""
    actor = str(g.user["username"])
    proposal_id = str(request.form.get("proposal_id", "")).strip()
    if not proposal_id or len(proposal_id) > 80:
        return redirect(url_for("gamification.index", message="Ungültiger Vorschlag."))

    store = _store()
    proposal = store.get_proposal_for_actor(proposal_id, actor)
    if proposal is None:
        return redirect(url_for("gamification.index", message="Vorschlag ist nicht verfügbar."))
    if proposal.get("accepted_by"):
        return redirect(url_for("gamification.index", message="Vorschlag wurde bereits übernommen."))
    if proposal.get("provider") != "contacts":
        return redirect(url_for(
            "gamification.index", proposal=proposal_id,
            message="Dieser Vorschlag kann derzeit nur bestätigt, aber nicht automatisch übernommen werden.",
        ))

    try:
        apply_contact_proposal(current_app.config["DOCUMENT_ROOT"], actor, proposal)
    except ValueError as exc:
        current_app.logger.info(
            "gamification proposal refused actor=%s proposal=%s reason=%s",
            actor, proposal_id, str(exc),
        )
        return redirect(url_for(
            "gamification.index",
            proposal=proposal_id,
            message="Vorschlag konnte nicht übernommen werden. Schreibrecht oder Datenstand haben sich geändert.",
        ))

    store.accept(proposal_id, actor, mode="manual")
    return redirect(url_for("gamification.index", message="Kontaktvorschlag übernommen und protokolliert."))
