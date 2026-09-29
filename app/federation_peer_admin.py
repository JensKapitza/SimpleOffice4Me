"""Admin routes for federation discovery and directed trust."""
import ipaddress
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, Response, current_app, flash, g, redirect, render_template, request, url_for

from .federation_admin import admin_required
from .federation_attestations import FederationAttestationStore
from .federation_discovery_lan import discover_lan, local_lan_addresses, scan_ports
from .federation_discovery_publish import publish
from .federation_discovery_service import discover_country, discover_direct, discover_email
from .federation_lan_receive_state import LanReceiveState
from .federation_local_profile import local_profile
from .federation_qr import decode_peer, encode_peer
from .federation_qr_image import render_qr_svg
from .federation_store import FederationStore
from .federation_trust_eval import recommendations
from .federation_trust_exchange import sync_claims
from .federation_trust_store import FederationTrustStore
from .v2.adapters.audit import RevisionHistoryAuditAdapter
from .v2.authorization import AuthorizationStore
from .v2.contracts import AuditEvent
from .v2.federation_policy import FederationPolicyStore, SCOPES
from .v2.jobs import FederationJobService, PersistentJobStore

bp = Blueprint("federation_peer_admin", __name__, url_prefix="/admin/federation/peer-discovery")


def _root():
    return current_app.config["DOCUMENT_ROOT"]


def _receive_state():
    return LanReceiveState(_root())


def _policy_store():
    return FederationPolicyStore(_root())


def _actor():
    user = getattr(g, "user", None) or {}
    return str(user.get("username") or "system")[:160]


def _audit_policy(operation, peer_id, changes):
    try:
        result = RevisionHistoryAuditAdapter(_root()).append(
            AuditEvent(
                actor=_actor(),
                operation=str(operation)[:200],
                object_id=f"federation-peer-policy:{peer_id}",
                occurred_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                source="federation-peer-admin",
                changes=dict(changes or {}),
            )
        )
        return bool(result.ok)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _authorization_store_if_present():
    path = Path(_root()).expanduser().resolve() / ".simpleoffice-v2" / "authorization.sqlite3"
    return AuthorizationStore(_root()) if path.is_file() else None


def _job_service_if_present():
    path = Path(_root()).expanduser().resolve() / ".simpleoffice-v2" / "jobs.sqlite3"
    return FederationJobService(PersistentJobStore(_root())) if path.is_file() else None


def _known_peer(peer_id):
    trust = FederationTrustStore(_root())
    identity = trust.identity(peer_id)
    if not identity:
        raise ValueError("Unbekannter Federation-Peer")
    stored = trust.store.get_peer(peer_id) or {}
    return trust, {
        **identity,
        "label": stored.get("label") or identity["peer_id"],
        "base_url": stored.get("base_url") or "",
        "enabled": bool(stored.get("enabled", False)),
        "trust": trust.get_trust(peer_id),
    }


def _csv_values(value):
    return tuple(
        item
        for item in (part.strip() for part in str(value or "").split(","))
        if item
    )


def _policy_redirect(peer_id):
    return redirect(url_for("federation_peer_admin.policy", peer_id=peer_id))


def _connect_addresses():
    """Return device IPv4 addresses for QR connect, without RFC1918 filtering."""
    values = []
    raw = os.environ.get("SIMPLEOFFICE_FEDERATION_LAN_ADDRESS", "")
    for item in raw.split(","):
        text = item.strip().split("%", 1)[0]
        if not text:
            continue
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            continue
        if address.version != 4 or address.is_loopback or address.is_unspecified:
            continue
        canonical = address.compressed
        if canonical not in values:
            values.append(canonical)
    return values or local_lan_addresses()


def _lan_connect_profiles():
    port = scan_ports()[0]
    result = []
    for address in _connect_addresses():
        endpoint = f"http://{address}:{port}"
        try:
            profile = local_profile(_root(), endpoint, prefer_fallback=True)
        except ValueError:
            continue
        result.append({"address": address, "port": port, "endpoint": endpoint, "payload": encode_peer(profile)})
    return result


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
    return render_template(
        "admin/federation_peer_discovery.html",
        peers=peers,
        own=own,
        own_qr=own_qr,
        lan_connect_profiles=_lan_connect_profiles(),
        lan_receive=_receive_state().status(),
    )


@bp.post("/receive/start")
@admin_required
def start_receive():
    try:
        state = _receive_state().start()
        flash(f"WLAN-Empfang für 15 Minuten vorgemerkt. Ablauf: {state['expires_at']}.")
    except Exception:
        flash("WLAN-Empfang konnte nicht aktiviert werden. Lokalen Speicher und App-Daten prüfen.")
    return redirect(url_for("federation_peer_admin.dashboard"))


@bp.post("/receive/stop")
@admin_required
def stop_receive():
    try:
        _receive_state().stop()
        flash("WLAN-Empfang beendet.")
    except Exception:
        flash("WLAN-Empfang konnte nicht beendet werden. Lokalen Speicher und App-Daten prüfen.")
    return redirect(url_for("federation_peer_admin.dashboard"))


@bp.get("/qr.svg")
@admin_required
def qr_svg():
    try:
        payload = encode_peer(local_profile(_root()))
        svg = render_qr_svg(payload)
    except ValueError:
        svg = _unavailable_qr_svg()
    return Response(svg, content_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@bp.get("/qr/lan/<int:index>.svg")
@admin_required
def lan_qr_svg(index):
    profiles = _lan_connect_profiles()
    if index < 0 or index >= len(profiles):
        return Response(_unavailable_qr_svg(), status=404, content_type="image/svg+xml", headers={"Cache-Control": "no-store"})
    return Response(render_qr_svg(profiles[index]["payload"]), content_type="image/svg+xml", headers={"Cache-Control": "no-store"})


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
        flash(
            f"Direkte Peer-Suche fehlgeschlagen: {exc}. "
            "Prüfe, ob der Dienst auf dieser IP und diesem Port läuft und ob eine Firewall die Verbindung blockiert."
        )
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


@bp.get("/policy/<peer_id>")
@admin_required
def policy(peer_id):
    try:
        _trust, peer = _known_peer(peer_id)
        policy_store = _policy_store()
        blocks = [
            {**row, "object_ref": ""}
            for row in policy_store.active_blocks()
            if row["peer_id"] == peer_id
        ]
        blocks.extend(
            policy_store.active_object_blocks(peer_id=peer_id)
        )
        blocks.sort(key=lambda row: (str(row["scope"]), str(row.get("object_ref") or "")))
        route_constraints = {}
        trust_requirements = {}
        for scope in sorted(SCOPES):
            for constraint in policy_store.route_constraints(peer_id, scope=scope):
                route_constraints[constraint.scope] = constraint
            for requirement in policy_store.trust_requirements(peer_id, scope=scope):
                trust_requirements[requirement.scope] = requirement
        known_peers = [
            row for row in _trust.list_identities()
            if row["peer_id"] != peer_id
        ]
        try:
            source_peer = str(local_profile(_root()).get("peer_id") or "")
        except ValueError:
            source_peer = ""
        return render_template(
            "admin/federation_peer_policy.html",
            peer=peer,
            blocks=blocks,
            route_constraints=sorted(route_constraints.values(), key=lambda row: row.scope),
            trust_requirements=sorted(trust_requirements.values(), key=lambda row: row.scope),
            policy_scopes=["all"] + sorted(scope for scope in SCOPES if scope != "all"),
            known_peers=known_peers,
            default_route=", ".join(item for item in (source_peer, peer_id) if item),
        )
    except (RuntimeError, ValueError):
        flash("Peer-Policy konnte nicht geladen werden.")
        return redirect(url_for("federation_peer_admin.dashboard"))


@bp.post("/policy/<peer_id>/block")
@admin_required
def block_peer(peer_id):
    try:
        _known_peer(peer_id)
        scope = request.form.get("scope", "all")
        reason = request.form.get("reason", "")
        object_refs = _csv_values(request.form.get("object_refs", ""))
        expiry_hours = str(request.form.get("expiry_hours", "") or "").strip()
        expires_at = None
        if expiry_hours:
            hours = int(expiry_hours)
            if hours < 1 or hours > 8760:
                raise ValueError("Ablauf muss zwischen 1 und 8760 Stunden liegen")
            expires_at = int(time.time()) + hours * 3600
        policy_store = _policy_store()
        if object_refs:
            policy_store.block_objects(
                peer_id,
                object_refs,
                scope=scope,
                reason=reason,
                created_by=_actor(),
                expires_at=expires_at,
            )
        else:
            policy_store.block(
                peer_id,
                scope=scope,
                reason=reason,
                created_by=_actor(),
                expires_at=expires_at,
            )
        authorization = _authorization_store_if_present()
        if authorization is None:
            revoked = 0
        elif object_refs:
            revoked = authorization.revoke_for_peer_objects(peer_id, object_refs)
        elif scope == "all":
            revoked = authorization.revoke_for_peer(peer_id)
        else:
            revoked = 0
        service = _job_service_if_present()
        stopped = (
            service.stop_blocked_jobs(
                policy_store=policy_store,
                authorization_store=authorization,
            )
            if service is not None
            else 0
        )
        audited = _audit_policy(
            "federation_peer_blocked",
            peer_id,
            {
                "scope": scope,
                "object_refs": list(object_refs),
                "reason": str(reason)[:500],
                "expires_at": expires_at,
                "jobs_stopped": stopped,
                "capabilities_revoked": revoked,
            },
        )
        if object_refs:
            message = (
                f"Objektsperre gesetzt ({scope}, {len(object_refs)} Objekt(e)). "
                f"{stopped} aktive Job(s) gestoppt"
            )
        else:
            message = f"Peer gesperrt ({scope}). {stopped} aktive Job(s) gestoppt"
        flash(
            message
            + (f", {revoked} Capability(s) widerrufen." if revoked else ".")
            + ("" if audited else " Audit konnte nicht gespeichert werden.")
        )
    except (RuntimeError, TypeError, ValueError):
        flash("Peer-Sperre konnte nicht gespeichert werden. Eingaben prüfen.")
    return _policy_redirect(peer_id)


@bp.post("/policy/<peer_id>/unblock")
@admin_required
def unblock_peer(peer_id):
    try:
        _known_peer(peer_id)
        scope = request.form.get("scope", "all")
        object_ref = str(request.form.get("object_ref", "") or "").strip()
        policy_store = _policy_store()
        if object_ref:
            policy_store.unblock_objects(
                peer_id,
                scope=scope,
                object_refs=(object_ref,),
            )
        else:
            policy_store.unblock(peer_id, scope=scope)
        audited = _audit_policy(
            "federation_peer_unblocked",
            peer_id,
            {"scope": scope, "object_ref": object_ref},
        )
        label = f"Objektsperre {object_ref}" if object_ref else f"Peer-Sperre für {scope}"
        flash(
            f"{label} aufgehoben."
            + ("" if audited else " Audit konnte nicht gespeichert werden.")
        )
    except (RuntimeError, ValueError):
        flash("Peer-Sperre konnte nicht aufgehoben werden.")
    return _policy_redirect(peer_id)


@bp.post("/policy/<peer_id>/route")
@admin_required
def set_route_policy(peer_id):
    try:
        _known_peer(peer_id)
        scope = request.form.get("scope", "all")
        direct_only = request.form.get("direct_only") == "1"
        max_hops_text = str(request.form.get("max_hops", "") or "").strip()
        max_hops = int(max_hops_text) if max_hops_text else None
        allowed_relays = _csv_values(request.form.get("allowed_relays", ""))
        denied_relays = _csv_values(request.form.get("denied_relays", ""))
        constraint = _policy_store().set_route_constraint(
            peer_id,
            scope=scope,
            direct_only=direct_only,
            max_hops=max_hops,
            allowed_relays=allowed_relays if allowed_relays else None,
            denied_relays=denied_relays if denied_relays else None,
            created_by=_actor(),
        )
        audited = _audit_policy(
            "federation_route_constraint_set",
            peer_id,
            {
                "scope": constraint.scope,
                "direct_only": constraint.direct_only,
                "max_hops": constraint.max_hops,
                "allowed_relays": list(constraint.allowed_relays or ()),
                "denied_relays": list(constraint.denied_relays or ()),
            },
        )
        flash(
            "Routenregel gespeichert."
            + ("" if audited else " Audit konnte nicht gespeichert werden.")
        )
    except (RuntimeError, TypeError, ValueError):
        flash("Routenregel konnte nicht gespeichert werden. Mindestens eine Einschränkung angeben.")
    return _policy_redirect(peer_id)


@bp.post("/policy/<peer_id>/route/clear")
@admin_required
def clear_route_policy(peer_id):
    try:
        _known_peer(peer_id)
        scope = request.form.get("scope", "all")
        _policy_store().clear_route_constraint(peer_id, scope=scope)
        audited = _audit_policy(
            "federation_route_constraint_cleared",
            peer_id,
            {"scope": scope},
        )
        flash("Routenregel entfernt." + ("" if audited else " Audit konnte nicht gespeichert werden."))
    except (RuntimeError, ValueError):
        flash("Routenregel konnte nicht entfernt werden.")
    return _policy_redirect(peer_id)


@bp.post("/policy/<peer_id>/confirmation")
@admin_required
def set_confirmation_policy(peer_id):
    try:
        _known_peer(peer_id)
        scope = request.form.get("scope", "all")
        verifiers = _csv_values(request.form.get("verifier_peers", ""))
        quorum = int(request.form.get("quorum", "1"))
        requirement = _policy_store().set_trust_requirement(
            peer_id,
            scope=scope,
            verifier_peers=verifiers,
            quorum=quorum,
            created_by=_actor(),
        )
        audited = _audit_policy(
            "federation_confirmation_requirement_set",
            peer_id,
            {
                "scope": requirement.scope,
                "verifier_peers": list(requirement.verifier_peers),
                "quorum": requirement.quorum,
            },
        )
        service = _job_service_if_present()
        stopped = (
            service.stop_blocked_jobs(
                policy_store=_policy_store(),
                authorization_store=_authorization_store_if_present(),
            )
            if service is not None
            else 0
        )
        flash(
            f"Bestätiger-Regel gespeichert; {stopped} aktive Job(s) neu bewertet."
            + ("" if audited else " Audit konnte nicht gespeichert werden.")
        )
    except (RuntimeError, TypeError, ValueError):
        flash("Bestätiger-Regel konnte nicht gespeichert werden. Peers und Quorum prüfen.")
    return _policy_redirect(peer_id)


@bp.post("/policy/<peer_id>/confirmation/clear")
@admin_required
def clear_confirmation_policy(peer_id):
    try:
        _known_peer(peer_id)
        scope = request.form.get("scope", "all")
        _policy_store().clear_trust_requirement(peer_id, scope=scope)
        audited = _audit_policy(
            "federation_confirmation_requirement_cleared",
            peer_id,
            {"scope": scope},
        )
        flash("Bestätiger-Regel entfernt." + ("" if audited else " Audit konnte nicht gespeichert werden."))
    except (RuntimeError, ValueError):
        flash("Bestätiger-Regel konnte nicht entfernt werden.")
    return _policy_redirect(peer_id)


@bp.post("/policy/<peer_id>/preview")
@admin_required
def preview_policy(peer_id):
    try:
        _known_peer(peer_id)
        scope = request.form.get("scope", "relay")
        route = _csv_values(request.form.get("route", ""))
        object_refs = _csv_values(request.form.get("object_refs", ""))
        decision = _policy_store().decision(
            route,
            scope=scope,
            target_peer=peer_id,
            authorization_store=_authorization_store_if_present(),
            object_refs=object_refs or None,
        )
        _audit_policy(
            "federation_policy_preview",
            peer_id,
            {
                "scope": scope,
                "route": list(route),
                "object_refs": list(object_refs),
                "allowed": decision.allowed,
                "reason": decision.reason,
                "blocked_peer": decision.blocked_peer,
            },
        )
        if decision.allowed:
            flash(f"Policy-Vorschau: erlaubt ({decision.reason}).")
        else:
            detail = f", Peer {decision.blocked_peer}" if decision.blocked_peer else ""
            flash(f"Policy-Vorschau: abgelehnt ({decision.reason}{detail}).")
    except (RuntimeError, TypeError, ValueError):
        flash("Policy-Vorschau konnte nicht sicher ausgewertet werden; Ergebnis ist fail-closed.")
    return _policy_redirect(peer_id)


