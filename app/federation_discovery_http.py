"""HTTP endpoints for federation discovery and rendezvous."""
import hmac
import os

from flask import Blueprint, current_app, jsonify, request

from .federation_attestations import FederationAttestationStore
from .federation_directory import directory_profiles
from .federation_directory_store import FederationDirectoryStore
from .federation_http import _authorized
from .federation_local_profile import local_peer_id, local_profile
from .federation_peer_auth import authenticate as authenticate_peer
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


def _directory_authorized(public=False):
    if public and _public_directory():
        return True
    expected = os.environ.get("SIMPLEOFFICE_FEDERATION_DIRECTORY_TOKEN", "").strip()
    supplied = request.headers.get("Authorization", "")
    supplied = supplied[7:].strip() if supplied.startswith("Bearer ") else ""
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return True
    return _authorized()


def _log_rejected(action, exc):
    current_app.logger.warning("Federation discovery %s rejected (%s)", action, type(exc).__name__)


@bp.get("/.well-known/simpleoffice-federation")
def well_known():
    try:
        # A direct request already proves which local address/port was reached.
        # This fallback enables ad-hoc LAN discovery without requiring a public
        # Internet URL. Published QR/directory profiles still require their
        # configured public URL because they call local_profile without it.
        return jsonify(local_profile(_root(), fallback_base_url=request.host_url))
    except ValueError as exc:
        _log_rejected("profile", exc)
        return jsonify({"error": "federation_profile_unavailable"}), 503


@bp.get("/federation/v1/discovery/peers")
def peers():
    if not _directory_authorized(public=True):
        return jsonify({"error": "authentication_required"}), 401
    country = request.args.get("country", "").strip().upper()[:2]
    return jsonify({"peers": directory_profiles(_root(), country)})


@bp.post("/federation/v1/discovery/register")
def register():
    if not _directory_authorized():
        return jsonify({"error": "authentication_required"}), 401
    body = request.get_json(silent=True) or {}
    try:
        profile = peer_profile(body.get("profile"))
        trust = FederationTrustStore(_root())
        trust.remember(
            profile["peer_id"], profile["country"], profile["fingerprint"],
            "directory-register", profile.get("public_key", ""),
        )
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
        _log_rejected("registration", exc)
        return jsonify({"error": "invalid_registration"}), 400


@bp.get("/federation/v1/discovery/resolve")
def resolve():
    if not _directory_authorized():
        return jsonify({"error": "authentication_required"}), 401
    lookup = request.args.get("lookup", "").strip().casefold()
    if not lookup:
        return jsonify({"error": "lookup_required"}), 400
    return jsonify({"peers": FederationRendezvousStore(_root()).resolve(lookup)})


@bp.post("/federation/v1/discovery/signal")
def send_signal():
    try:
        sender_peer = authenticate_peer(_root(), request)
        body = request.get_json(silent=True) or {}
        result = FederationRendezvousMessages(_root()).send(
            sender_peer, body.get("recipient_peer", ""), body.get("kind", "signal"),
            body.get("payload") or {}, body.get("ttl_seconds", 600),
        )
        return jsonify(result), 201
    except (TypeError, ValueError) as exc:
        _log_rejected("signal send", exc)
        return jsonify({"error": "invalid_signal_request"}), 401


@bp.get("/federation/v1/discovery/signal")
def receive_signal():
    try:
        recipient_peer = authenticate_peer(_root(), request)
        return jsonify({"messages": FederationRendezvousMessages(_root()).receive(recipient_peer)})
    except ValueError as exc:
        _log_rejected("signal receive", exc)
        return jsonify({"error": "invalid_signal_request"}), 401


@bp.get("/federation/v1/discovery/trust-claims")
def trust_claims():
    if not _authorized():
        return jsonify({"error": "authentication_required"}), 401
    verifier = local_peer_id()
    claims = [{**claim, "source_peer": verifier} for claim in FederationTrustStore(_root()).shareable_claims()]
    return jsonify({
        "claims": claims,
        "attestations": FederationAttestationStore(_root()).export_shareable(),
    })
