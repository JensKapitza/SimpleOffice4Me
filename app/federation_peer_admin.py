"""Admin routes for federation discovery and directed trust."""
from flask import Blueprint, Response, current_app, flash, g, redirect, render_template, request, url_for

from .federation_admin import admin_required
from .federation_attestations import FederationAttestationStore
from .federation_discovery_lan import discover_lan, scan_ports
from .federation_discovery_publish import publish
from .federation_discovery_service import discover_country, discover_direct, discover_email
from .federation_local_profile import local_profile
from .federation_qr import decode_peer, encode_peer
from .federation_qr_image import render_qr_svg
from .federation_store import FederationStore
from .federation_trust_eval import recommendations
from .federation_trust_exchange import sync_claims
from .federation_trust_store import FederationTrustStore

bp = Blueprint("federation_peer_admin", __name__, url_prefix="/admin/federation/peer-discovery")


def _root():
    return current_app.config["DOCUMENT_ROOT"]


def _unavailable_qr_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240" '
        'role="img" aria-label="Federation QR nicht verfügbar">'
        '<rect width="240" height="240" fill="white"/>'
        '<rect x="1" y="1" width="238" height="238" fill="none" stroke="#adb5bd" stroke-width="2"/>'
        '<text x="120" y="112" text-anchor="middle" font-family="sans-serif" font-size="14" fill="#495057">'
        'QR nicht verfügbar</text>'
        '<text x="120" y="134" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#6c757d">'
        'Public-URL konfigurieren</text>'
        '</svg>'
    )


@bp.get("")
@admin_required
def dashboard():
    trust = FederationTrustStore(_root())
    peers = []
    for identity in trust.list_identities():
        peer = trust.store.get_peer(identity["peer_id"]) or {}
        peers.append({
            **identity,
            "label": peer.get("label") or identity["peer_id"],
            "base_url": peer.get("base_url") or "",
            "enabled": bool(peer.get("enabled", False)),
            "has_token": bool(peer.get("has_token", False)),
            "trust": trust.get_trust(identity["peer_id"]),
            "recommendations": recommendations(_root(), identity["peer_id"]),
        })
    try:
        own = local_profile(_root())
        own_qr = encode_peer(own)
    except ValueError:
        own = {}
        own_qr = ""
    return render_template("admin/federation_peer_discovery.html", peers=peers, own=own, own_qr=own_qr)


@bp.get("/qr.svg")
@admin_required
def qr_svg():
    try:
        payload = encode_peer(local_profile(_root()))
        svg = render_qr_svg(payload)
    except ValueError:
        svg = _unavailable_qr_svg()
    return Response(svg, content_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@bp.post("/discover/lan")
@admin_required
def lan():
    try:
        ports = scan_ports(request.form.get("port", ""))
        result = discover_lan(_root(), ports=ports)
        networks = ", ".join(result["networks"])
        flash(
            f"WLAN-Scan abgeschlossen: {len(result['peers'])} SimpleOffice-Gerät(e) gefunden. "
            f"Netz: {networks}; Ports: {', '.join(str(port) for port in result['ports'])}. "
            "Gefundene Geräte bleiben bekannt/nicht geprüft und deaktiviert."
        )
    except Exception as exc:
        flash(f"WLAN-Scan fehlgeschlagen: {exc}")
    return redirect(url_for("federation_peer_admin.dashboard"))


@bp.post("/discover/direct")
@admin_required
def direct():
    try:
        peer = discover_direct(_root(), request.form.get("endpoint", ""))
        flash(f"Peer {peer['peer_id']} gefunden und als bekannt/nicht geprüft gespeichert.")
    except Exception as exc:
        flash(f"Direkte Peer-Suche fehlgeschlagen: {exc}")
    return redirect(url_for("federation_peer_admin.dashboard"))


@bp.post("/discover/country")
@admin_required
def country():
    try:
        result = discover_country(_root(), request.form.get("country", ""))
        flash(f"Land-Discovery: {len(result['peers'])} Peer(s) gefunden, {len(result['errors'])} Fehler.")
    except Exception as exc:
        flash(f"Land-Discovery fehlgeschlagen: {exc}")
    return redirect(url_for("federation_peer_admin.dashboard"))


@bp.post("/discover/email")
@admin_required
def email():
    try:
        result = discover_email(_root(), request.form.get("email", ""))
        flash(f"E-Mail-Rendezvous: {len(result['peers'])} Peer(s) gefunden.")
    except Exception as exc:
        flash(f"E-Mail-Rendezvous fehlgeschlagen: {exc}")
    return redirect(url_for("federation_peer_admin.dashboard"))


@bp.post("/publish")
@admin_required
def publish_local():
    try:
        result = publish(_root(), request.form.get("email", ""))
        flash(f"Peer-Profil an {len(result['published'])} Directory-Knoten veröffentlicht.")
    except Exception as exc:
        flash(f"Peer-Profil konnte nicht veröffentlicht werden: {exc}")
    return redirect(url_for("federation_peer_admin.dashboard"))


@bp.post("/qr/import")
@admin_required
def import_qr():
    try:
        profile = decode_peer(request.form.get("payload", ""))
        trust = FederationTrustStore(_root())
        trust.remember(
            profile["peer_id"], profile["country"], profile["fingerprint"], "qr",
            profile.get("public_key", ""),
        )
        store = FederationStore(_root())
        existing = store.get_peer(profile["peer_id"])
        store.save_peer(
            profile["peer_id"], profile["label"], profile["base_url"], "",
            (existing or {}).get("policy") or {}, bool((existing or {}).get("enabled", False)),
        )
        personally_verified = request.form.get("verify_in_person") == "1"
        if personally_verified:
            trust.set_trust(
                profile["peer_id"], "NONE", "VERIFIED_IN_PERSON", "DIRECT_ONLY", 0,
                metadata={"actor": str(g.user["username"]), "method": "qr_in_person"},
            )
            FederationAttestationStore(_root()).replace_signed(
                profile["peer_id"], "VERIFIED_IN_PERSON", profile["fingerprint"], "DIRECT_ONLY", 0,
            )
            flash(f"Peer {profile['peer_id']} per QR persönlich geprüft; Vertrauen bleibt NONE.")
        else:
            flash(f"Peer {profile['peer_id']} per QR als bekannt/nicht geprüft gespeichert.")
    except Exception as exc:
        flash(f"QR-Peer konnte nicht übernommen werden: {exc}")
    return redirect(url_for("federation_peer_admin.dashboard"))


@bp.post("/trust/<peer_id>")
@admin_required
def set_trust(peer_id):
    try:
        trust = FederationTrustStore(_root())
        verification = request.form.get("verification", "KNOWN_UNVERIFIED")
        propagation = request.form.get("propagation", "DIRECT_ONLY")
        max_hops = request.form.get("max_hops", 0)
        trust.set_trust(
            peer_id,
            request.form.get("trust_level", "NONE"),
            verification,
            propagation,
            max_hops,
            metadata={"actor": str(g.user["username"])},
        )
        identity = trust.identity(peer_id) or {}
        FederationAttestationStore(_root()).replace_signed(
            peer_id, verification, identity.get("fingerprint", ""), propagation, max_hops,
        )
        flash("Vertrauensbeziehung und Verifikationsnachweis gespeichert.")
    except Exception as exc:
        flash(f"Vertrauen konnte nicht gespeichert werden: {exc}")
    return redirect(url_for("federation_peer_admin.dashboard"))


@bp.post("/trust/<peer_id>/sync")
@admin_required
def sync_trust(peer_id):
    try:
        result = sync_claims(_root(), peer_id)
        flash(
            f"{result['imported']} Trust-Hinweis(e), {result['verified_attestations']} signierte Nachweise übernommen; "
            f"lokales Vertrauen blieb unverändert."
        )
    except Exception as exc:
        flash(f"Trust-Hinweise konnten nicht geladen werden: {exc}")
    return redirect(url_for("federation_peer_admin.dashboard"))
