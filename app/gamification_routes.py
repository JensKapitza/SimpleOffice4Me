"""Web entry point and management UI for data-quality gamification."""
from __future__ import annotations

import json
from dataclasses import replace

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .db import get_db
from .federation_store import FederationStore
from .gamification_adapters import (
    apply_contact_proposal,
    contact_candidates,
    contact_proposal_can_apply,
    document_candidates,
    image_candidates,
    image_preview_path,
)
from .gamification_engine import eligible_challenges, roulette
from .gamification_federation import peer_allows
from .gamification_organization import (
    add_member,
    bind_participant,
    create_organization,
    organizations_for_user,
)
from .gamification_policy import GamePolicy
from .gamification_providers import Challenge, get_provider
from .gamification_rewards import award_accepted_proposal, profile
from .gamification_store import GamificationStore

bp = Blueprint("gamification", __name__, url_prefix="/gamification")
PROVIDER_SET = frozenset({"contacts", "images", "documents"})
RESOURCE_CLASSES = {"contacts": "contact", "images": "photo", "documents": "released_file"}
COLLECTIONS = {"contacts": "contacts", "images": "images", "documents": "files"}


def _policy() -> GamePolicy:
    return GamePolicy(
        scope="local",
        providers=PROVIDER_SET,
        collections=frozenset(COLLECTIONS.values()),
        preview_allowed=True,
        original_allowed=False,
        submit_proposals=True,
        auto_accept_consensus=False,
    )


def _store() -> GamificationStore:
    return GamificationStore(current_app.config["DOCUMENT_ROOT"])


def _local_candidates(actor: str):
    root = current_app.config["DOCUMENT_ROOT"]
    return [*contact_candidates(root, actor), *image_candidates(root, actor), *document_candidates(root, actor)]


def _policy_snapshot(policy: GamePolicy, **extra) -> dict[str, object]:
    result = {
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
    result.update(extra)
    return result


def _pending_proposal(actor: str) -> dict[str, object] | None:
    proposal_id = str(request.args.get("proposal", "")).strip()
    if not proposal_id or len(proposal_id) > 80:
        return None
    proposal = _proposal_any_scope(proposal_id, actor)
    if proposal is None:
        return None
    contact_apply = proposal.get("provider") == "contacts"
    return {
        "id": proposal_id,
        "field_name": str(proposal["field_name"]),
        "value": str(proposal["value"])[:500],
        "manual_apply_supported": contact_apply,
        "can_apply": bool(contact_apply and contact_proposal_can_apply(current_app.config["DOCUMENT_ROOT"], actor, proposal)),
        "can_confirm": not bool(proposal.get("accepted_by")),
        "accepted": bool(proposal.get("accepted_by")),
    }


def _proposal_any_scope(proposal_id: str, actor: str):
    store = _store()
    for scope in ("local", "organization", "federation"):
        proposal = store.get_proposal_for_actor(proposal_id, actor, scope=scope)
        if proposal is not None:
            return proposal
    return None


def _populate_session(store: GamificationStore, session_id: str, actor: str, policy: GamePolicy, *, organization: bool = False) -> int:
    candidates = _local_candidates(actor)
    if organization:
        candidates = [replace(candidate, organization_member=True) for candidate in candidates]
    challenges = eligible_challenges(policy, actor, candidates)[:100]
    for generated in challenges:
        item_id = store.add_item(session_id, generated.provider, generated.object_ref, RESOURCE_CLASSES[generated.provider])
        store.add_challenge(item_id, generated.kind, generated.answer_type, generated.prompt, generated.payload)
    return len(challenges)


def _sessions(actor: str):
    store = _store()
    with store._db() as db:
        rows = db.execute(
            "SELECT id,scope,title,status,policy_json,created_at FROM game_session WHERE created_by=? ORDER BY created_at DESC LIMIT 50",
            (actor,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["policy"] = json.loads(item.pop("policy_json") or "{}")
            item["participants"] = [dict(value) for value in db.execute(
                "SELECT participant,role FROM game_participant WHERE session_id=? ORDER BY participant", (item["id"],)
            ).fetchall()]
            item["open_challenges"] = db.execute(
                "SELECT COUNT(*) FROM game_challenge c JOIN game_item i ON i.id=c.item_id WHERE i.session_id=? AND c.status='open'",
                (item["id"],),
            ).fetchone()[0]
            result.append(item)
    return result


def _review_proposals(actor: str):
    store = _store()
    with store._db() as db:
        rows = db.execute(
            "SELECT p.id,p.field_name,p.value_json,p.proposed_by,p.source,i.provider,s.scope,s.created_by "
            "FROM annotation_proposal p JOIN game_item i ON i.id=p.item_id JOIN game_session s ON s.id=i.session_id "
            "LEFT JOIN annotation_acceptance a ON a.proposal_id=p.id "
            "WHERE a.proposal_id IS NULL AND s.status='active' AND (s.created_by=? OR EXISTS(" 
            "SELECT 1 FROM game_participant gp WHERE gp.session_id=s.id AND gp.participant=?)) "
            "ORDER BY p.created_at DESC LIMIT 50",
            (actor, actor),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["value"] = json.loads(item.pop("value_json"))
        except json.JSONDecodeError:
            item["value"] = ""
        item["consensus"] = store.consensus(item["id"])
        result.append(item)
    return result


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
        item_id = store.add_item(session_id, generated.provider, generated.object_ref, RESOURCE_CLASSES[generated.provider])
        challenge_id = store.add_challenge(item_id, generated.kind, generated.answer_type, generated.prompt, generated.payload)
        challenge = {"id": challenge_id, "provider": generated.provider, "kind": generated.kind, "prompt": generated.prompt, "answer_type": generated.answer_type, "payload": generated.payload}
    return render_template(
        "gamification/index.html", challenge=challenge, pending_proposal=_pending_proposal(actor),
        reward_profile=profile(_store(), actor), message=request.args.get("message", "")[:200],
    )


@bp.get("/manage")
@login_required
def manage():
    actor = str(g.user["username"])
    return render_template(
        "gamification/manage.html", sessions=_sessions(actor), proposals=_review_proposals(actor),
        peers=FederationStore(current_app.config["DOCUMENT_ROOT"]).list_peers(),
        organizations=organizations_for_user(get_db(), actor), reward_profile=profile(_store(), actor),
        message=request.args.get("message", "")[:240], is_admin=bool(g.user["is_admin"]),
    )


@bp.post("/manage/sessions")
@login_required
def create_session():
    actor = str(g.user["username"])
    scope = str(request.form.get("scope", "local")).strip().casefold()
    title = str(request.form.get("title", "")).strip()[:160] or "Daten-Roulette"
    providers = frozenset(value for value in request.form.getlist("providers") if value in PROVIDER_SET)
    if scope not in {"local", "federation", "organization"} or not providers:
        return redirect(url_for("gamification.manage", message="Ungültige Runde oder keine Provider gewählt."))
    policy = GamePolicy(
        scope=scope, providers=providers,
        collections=frozenset(COLLECTIONS[value] for value in providers),
        preview_allowed="images" in providers, original_allowed=False, submit_proposals=True,
    )
    store = _store()
    extra = {}
    peer_id = str(request.form.get("peer_id", "")).strip()
    org_id = str(request.form.get("org_id", "")).strip().casefold()
    if scope == "federation":
        peer = FederationStore(current_app.config["DOCUMENT_ROOT"]).get_peer(peer_id)
        if peer is None or any(not peer_allows(peer, "receive_challenges", provider=value) for value in providers):
            return redirect(url_for("gamification.manage", message="Peer ist für diese Spiel-Provider nicht freigegeben."))
        extra["peer_id"] = peer_id
    elif scope == "organization":
        if not any(item["org_id"] == org_id for item in organizations_for_user(get_db(), actor)):
            return redirect(url_for("gamification.manage", message="Keine Mitgliedschaft in dieser Organisation."))
        extra["org_id"] = org_id
    session_id = store.create_session(title, scope, actor, _policy_snapshot(policy, **extra))
    try:
        if scope == "federation":
            store.add_participant(session_id, f"peer:{peer_id}", role="answer", actor=actor)
        elif scope == "organization":
            participants = [value.strip() for value in str(request.form.get("participants", "")).split(",") if value.strip()]
            for participant in participants:
                bind_participant(store, get_db(), session_id, participant, org_id=org_id, actor=actor, role="answer")
        count = _populate_session(store, session_id, actor, policy, organization=scope == "organization")
    except ValueError:
        return redirect(url_for("gamification.manage", message="Teilnehmer oder Freigabe konnte nicht sicher gebunden werden."))
    return redirect(url_for("gamification.manage", message=f"Runde erstellt: {count} sichere Aufgaben vorbereitet."))


@bp.post("/manage/organizations")
@login_required
def create_org():
    actor = str(g.user["username"])
    try:
        create_organization(get_db(), request.form.get("org_id", ""), request.form.get("name", ""), actor)
    except ValueError:
        return redirect(url_for("gamification.manage", message="Organisation konnte nicht erstellt werden."))
    return redirect(url_for("gamification.manage", message="Organisation erstellt."))


@bp.post("/manage/organizations/<org_id>/members")
@login_required
def organization_member(org_id: str):
    actor = str(g.user["username"])
    try:
        add_member(get_db(), org_id, request.form.get("username", ""), request.form.get("role", "member"), actor)
    except ValueError:
        return redirect(url_for("gamification.manage", message="Mitglied konnte nicht hinzugefügt werden."))
    return redirect(url_for("gamification.manage", message="Organisationsmitglied aktualisiert."))


@bp.post("/vote")
@login_required
def vote():
    actor = str(g.user["username"])
    proposal_id = str(request.form.get("proposal_id", "")).strip()
    proposal = _proposal_any_scope(proposal_id, actor)
    if proposal is None:
        abort(403)
    _store().vote(proposal_id, actor, str(request.form.get("approve", "1")) == "1", source="human")
    return redirect(url_for("gamification.manage", message="Stimme gespeichert."))


@bp.post("/confirm")
@login_required
def confirm():
    actor = str(g.user["username"])
    proposal_id = str(request.form.get("proposal_id", "")).strip()
    proposal = _proposal_any_scope(proposal_id, actor)
    if proposal is None or str(proposal.get("created_by")) != actor:
        abort(403)
    store = _store()
    consensus = store.consensus(proposal_id)
    if not consensus["reached"]:
        return redirect(url_for("gamification.manage", message="Konsensschwelle ist noch nicht erreicht."))
    if proposal.get("provider") == "contacts":
        try:
            apply_contact_proposal(current_app.config["DOCUMENT_ROOT"], actor, proposal)
        except ValueError:
            return redirect(url_for("gamification.manage", message="Kontakt kann wegen ACL oder geändertem Datenstand nicht übernommen werden."))
    store.accept(proposal_id, actor, mode="consensus")
    award_accepted_proposal(store, proposal_id)
    return redirect(url_for("gamification.manage", message="Konsens bestätigt und protokolliert."))


@bp.get("/preview/<challenge_id>")
@login_required
def preview(challenge_id: str):
    actor = str(g.user["username"])
    if not challenge_id or len(challenge_id) > 80:
        abort(404)
    persisted = _store().get_challenge_for_actor(challenge_id, actor)
    if persisted is None or persisted.get("provider") != "images" or persisted.get("resource_class") != "photo":
        abort(404)
    path = image_preview_path(current_app.config["DOCUMENT_ROOT"], actor, str(persisted.get("object_ref", "")))
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
        return render_template("gamification/index.html", challenge=None, pending_proposal=None, reward_profile=profile(_store(), actor), message="Ungültige Spielrunde."), 400
    store = _store()
    persisted = store.get_challenge_for_actor(challenge_id, actor)
    if persisted is None:
        return render_template("gamification/index.html", challenge=None, pending_proposal=None, reward_profile=profile(store, actor), message="Diese Spielrunde ist nicht mehr verfügbar."), 409
    action = str(request.form.get("action", "answer")).strip().casefold()
    if action in {"unknown", "skip"}:
        store.skip_challenge(challenge_id, actor, disposition=action)
        return redirect(url_for("gamification.index", message="Übersprungen – dafür gibt es keine Ratepunkte."))
    if action != "answer":
        return render_template("gamification/index.html", challenge=None, pending_proposal=None, reward_profile=profile(store, actor), message="Ungültige Spielaktion."), 400
    value = str(request.form.get("answer", "")).strip()
    challenge = Challenge(provider=str(persisted["provider"]), object_ref=str(persisted["object_ref"]), kind=str(persisted["kind"]), prompt=str(persisted["prompt"]), answer_type=str(persisted["answer_type"]), payload=dict(persisted.get("payload", {})))
    if not get_provider(challenge.provider).validate_answer(challenge, value):
        return render_template("gamification/index.html", challenge={"id": challenge_id, "provider": challenge.provider, "kind": challenge.kind, "prompt": challenge.prompt, "answer_type": challenge.answer_type, "payload": challenge.payload}, pending_proposal=None, reward_profile=profile(store, actor), message="Bitte eine gültige Antwort eingeben oder ‚Weiß ich nicht‘ wählen."), 400
    proposal_id = store.answer_challenge(challenge_id, actor, value)
    return redirect(url_for("gamification.index", proposal=proposal_id, message="Vorschlag gespeichert. Originaldaten wurden nicht automatisch geändert."))


@bp.post("/accept")
@login_required
def accept():
    actor = str(g.user["username"])
    proposal_id = str(request.form.get("proposal_id", "")).strip()
    proposal = _proposal_any_scope(proposal_id, actor)
    if proposal is None:
        return redirect(url_for("gamification.index", message="Vorschlag ist nicht verfügbar."))
    if proposal.get("accepted_by"):
        return redirect(url_for("gamification.index", message="Vorschlag wurde bereits bestätigt."))
    store = _store()
    if proposal.get("provider") == "contacts":
        try:
            apply_contact_proposal(current_app.config["DOCUMENT_ROOT"], actor, proposal)
        except ValueError:
            return redirect(url_for("gamification.index", proposal=proposal_id, message="Vorschlag konnte nicht übernommen werden. Schreibrecht oder Datenstand haben sich geändert."))
        mode = "manual"
        message = "Kontaktvorschlag übernommen und protokolliert."
    else:
        mode = "confirmed_metadata"
        message = "Metadaten-Vorschlag bestätigt und protokolliert; Originaldatei blieb unverändert."
    store.accept(proposal_id, actor, mode=mode)
    award_accepted_proposal(store, proposal_id)
    return redirect(url_for("gamification.index", message=message))
