"""HTTP endpoints for federation discovery and rendezvous."""
import os

from flask import Blueprint, current_app, jsonify, request

from .federation_attestations import FederationAttestationStore
from .federation_directory import directory_profiles
from .federation_directory_store import FederationDirectoryStore
from .federation_http import _authorized
from .federation_local_profile import local_profile
from .federation_peer_profile import peer_profile
from .federation_rendezvous_messages import FederationRendezvousMessages
from .federation_rendezvous_store import FederationRendezvousStore
from .federation_store import FederationStore
from .federation_trust_store import FederationTrustStore

bp = Blueprint("federation_discovery_http", __name__)


def _root():
    return current_app.config["DOCUMENT_ROOT"]


def _public_directory():
    return os.environ.get("SIMPLEOFFICE_FEDERATION_PUBLIC_DIRECTORY", "0").strip().casefold() in {"1", "true", "yes", "on"}


def _allowed(public=False):
    return (public and _public_directory()) or _authorized()


@bp.get("/.well-known/simpleoffice-federation")
def well_known():
    try:
        return jsonify(local_profile())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 503


@bp.get("/federation/v1/discovery/peers")
def peers():
    if not _allowed(public=True):
        return jsonify({"error": "authentication_required"}), 401
    country = request.args.get("country", "").strip().upper()[:2]
    return jsonify({"peers": directory_profiles(_root(), country)})


@bp.post("/federation/v1/discovery/register")
def register():
    if not _allowed(public=False):
        return jsonify({"error": "authentication_required"}), 401
    body = request.get_json(silent=True) or {}
    try:
        profile = peer_profile(body.get("profile"))
        trust = FederationTrustStore(_root())
        trust.remember(profile["peer_id"], profile["country"], profile["fingerprint"], "directory-register")
        store = FederationStore(_root())
        existing = store.get_peer(profile["peer_id"])
        store.save_peer(
            profile["peer_id"], profile["label"], profile["base_url"], "",
            (existing or {}).get("policy") or {}, bool((existing or {}).get("enabled", False)),
        )
        ttl = body.get("ttl_seconds", 86400)
        FederationDirectoryStore(_root()).publish(profile["peer_id"], ttl)
        lookup = str(body.get("lookup") or "").strip().casefold()
        result = {"peer_id": profile["peer_id"]}
        if lookup:
            result.update(FederationRendezvousStore(_root()).register(lookup, profile, ttl))
        return jsonify(result), 201
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/federation/v1/discovery/resolve")
def resolve():
    if not _allowed(public=False):
        return jsonify({"error": "authentication_required"}), 401
    lookup = request.args.get("lookup", "").strip().casefold()
    if not lookup:
        return jsonify({"error": "lookup_required"}), 400
    return jsonify({"peers": FederationRendezvousStore(_root()).resolve(lookup)})


@bp.post("/federation/v1/discovery/signal")
def send_signal():
    if not _allowed(public=False):
        return jsonify({"error": "authentication_required"}), 401
    body = request.get_json(silent=True) or {}
    try:
        result = FederationRendezvousMessages(_root()).send(
            body.get("sender_peer", ""), body.get("recipient_peer", ""),
            body.get("kind", "signal"), body.get("payload") or {}, body.get("ttl_seconds", 600),
        )
        return jsonify(result), 201
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/federation/v1/discovery/signal")
def receive_signal():
    if not _allowed(public=False):
        return jsonify({"error": "authentication_required"}), 401
    peer_id = request.args.get("peer_id", "").strip()
    if not peer_id:
        return jsonify({"error": "peer_id_required"}), 400
    try:
        return jsonify({"messages": FederationRendezvousMessages(_root()).receive(peer_id)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/federation/v1/discovery/trust-claims")
def trust_claims():
    if not _allowed(public=False):
        return jsonify({"error": "authentication_required"}), 401
    return jsonify({
        "claims": FederationTrustStore(_root()).shareable_claims(),
        "attestations": FederationAttestationStore(_root()).export_shareable(),
    })
