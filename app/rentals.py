"""Web UI and federation bridge for auditable rental settlements."""
from __future__ import annotations

import re
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .access_control import is_admin
from .auth import login_required
from .contact_store import ContactStore
from .document_store import DocumentStore, sha256_file
from .federation_core import build_manifest, transfer_id
from .federation_store import FederationStore
from .federation_worker import push_blob_to_peer
from .object_store import ObjectStore
from .rental_billing import ALLOCATION_METHODS, LEDGER_KINDS, METRIC_TYPES, RentalBillingStore

bp = Blueprint("rentals", __name__, url_prefix="/rentals")


def _root() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"])


def _store() -> RentalBillingStore:
    return RentalBillingStore(_root())


def _actor() -> str:
    return str(g.user["username"])


def _objects() -> list[dict]:
    return ObjectStore(_root()).objects()


def _contacts() -> list[dict]:
    return ContactStore(_root()).contacts(_actor())


def _peers() -> list[dict]:
    try:
        return [item for item in FederationStore(_root()).list_peers() if item.get("enabled")]
    except Exception:
        return []


def _require_admin() -> None:
    if not is_admin(g.user):
        abort(403)


def _peer_allows_rental_send(policy: object) -> bool:
    """Fail closed for a sensitive tenant package.

    A rental package is transferred as a document, so an explicit document
    send permission is mandatory.  Deployments that define an additional
    ``rentals`` policy must explicitly enable that resource as well.
    """
    if not isinstance(policy, dict):
        return False
    document_policy = policy.get("documents")
    if not isinstance(document_policy, dict) or document_policy.get("send") is not True:
        return False
    if "rentals" in policy:
        rental_policy = policy.get("rentals")
        if not isinstance(rental_policy, dict) or rental_policy.get("send") is not True:
            return False
    return True


def _back(endpoint: str, **values):
    return redirect(url_for(endpoint, **values))


def _enrich_group(group: dict) -> dict:
    objects = {item["object_id"]: item for item in _objects()}
    contacts = {item["contact_id"]: item for item in _contacts()}
    store = _store()
    units = []
    for unit in group["units"]:
        object_id = unit["object_id"]
        obj = objects.get(object_id, {"name": object_id, "location": ""})
        tenancies = []
        for tenancy in store.tenancies(object_id):
            contact = contacts.get(tenancy["contact_id"], {})
            tenancies.append({**tenancy, "contact_name": contact.get("fields", {}).get("display_name", tenancy["contact_id"])})
        units.append({**unit, "object": obj, "tenancies": tenancies, "metrics": store.metrics(object_id)})
    return {**group, "units": units}


@bp.get("")
@login_required
def index():
    return render_template(
        "rentals/index.html", groups=[_enrich_group(item) for item in _store().groups()],
        settlements=_store().settlements(), objects=_objects(), allocation_methods=sorted(ALLOCATION_METHODS),
    )


@bp.post("/groups")
@login_required
def create_group():
    try:
        group = _store().create_group(request.form.get("name", ""), request.form.get("description", ""), _actor())
        flash("Mietobjektgruppe angelegt.")
        return _back("rentals.group_detail", group_id=group["group_id"])
    except ValueError as exc:
        flash(str(exc)); return _back("rentals.index")


@bp.get("/groups/<group_id>")
@login_required
def group_detail(group_id: str):
    try:
        group = _enrich_group(_store().group(group_id))
    except ValueError:
        abort(404)
    return render_template(
        "rentals/group.html", group=group, objects=_objects(), contacts=_contacts(), peers=_peers(),
        metric_types=sorted(METRIC_TYPES),
    )


@bp.post("/groups/<group_id>/units")
@login_required
def add_unit(group_id: str):
    try:
        _store().add_group_unit(group_id, request.form.get("object_id", ""), request.form.get("label", ""), _actor())
        flash("Objekt zur Mietgruppe hinzugefügt.")
    except ValueError as exc: flash(str(exc))
    return _back("rentals.group_detail", group_id=group_id)


@bp.post("/groups/<group_id>/units/<object_id>/remove")
@login_required
def remove_unit(group_id: str, object_id: str):
    _store().remove_group_unit(group_id, object_id, _actor())
    flash("Objekt aus der Mietgruppe entfernt.")
    return _back("rentals.group_detail", group_id=group_id)


@bp.post("/groups/<group_id>/tenancies")
@login_required
def add_tenancy(group_id: str):
    try:
        _store().add_tenancy(
            request.form.get("object_id", ""), request.form.get("contact_id", ""),
            request.form.get("starts_on", ""), request.form.get("ends_on", ""), _actor(),
            federation_peer_id=request.form.get("federation_peer_id", ""),
            contract_document_id=request.form.get("contract_document_id", ""), note=request.form.get("note", ""),
        )
        flash("Mietverhältnis gespeichert.")
    except ValueError as exc: flash(str(exc))
    return _back("rentals.group_detail", group_id=group_id)


@bp.post("/groups/<group_id>/metrics")
@login_required
def add_metric(group_id: str):
    try:
        _store().add_metric(
            request.form.get("object_id", ""), request.form.get("metric_type", ""), request.form.get("value", ""),
            request.form.get("valid_from", ""), request.form.get("valid_to", ""), _actor(),
            source_kind=request.form.get("source_kind", "manual"), source_note=request.form.get("source_note", ""),
            source_document_id=request.form.get("source_document_id", ""),
        )
        flash("Schlüsselwert gespeichert.")
    except ValueError as exc: flash(str(exc))
    return _back("rentals.group_detail", group_id=group_id)


@bp.post("/settlements")
@login_required
def create_settlement():
    try:
        group_id = request.form.get("group_id", "").strip(); object_id = request.form.get("object_id", "").strip()
        if group_id: object_id = ""
        settlement = _store().create_settlement(
            request.form.get("label", ""), int(request.form.get("year", "0") or 0),
            request.form.get("starts_on", ""), request.form.get("ends_on", ""), _actor(),
            group_id=group_id, object_id=object_id,
        )
        flash("Abrechnung als Entwurf angelegt.")
        return _back("rentals.settlement_detail", settlement_id=settlement["settlement_id"])
    except (ValueError, TypeError) as exc:
        flash(str(exc)); return _back("rentals.index")


@bp.get("/settlements/<settlement_id>")
@login_required
def settlement_detail(settlement_id: str):
    store = _store()
    try:
        settlement = store.settlement(settlement_id)
        units = store._settlement_units(settlement)
        preview_error = ""; calculation = None
        try: calculation = store.calculate(settlement_id)
        except ValueError as exc: preview_error = str(exc)
        cost_rows = []
        for cost in store.costs(settlement_id):
            cost_rows.append({**cost, "manual_weights": store.manual_weights(cost["cost_id"])})
        contacts = {item["contact_id"]: item for item in _contacts()}
        tenant_names = {
            contact_id: contacts.get(contact_id, {}).get("fields", {}).get("display_name", contact_id)
            for contact_id in (calculation or {"tenants": {}})["tenants"]
        }
        files = {key: path.name for key, path in store.approval_files(settlement_id).items() if path.is_file()}
    except ValueError:
        abort(404)
    return render_template(
        "rentals/settlement.html", settlement=settlement, units=units, costs=cost_rows,
        calculation=calculation, preview_error=preview_error, contacts=contacts, tenant_names=tenant_names,
        allocation_methods=sorted(ALLOCATION_METHODS), ledger_kinds=sorted(LEDGER_KINDS), peers=_peers(),
        approval_files=files, exports=store.exports(settlement_id), metric_types=sorted(METRIC_TYPES),
    )


@bp.post("/settlements/<settlement_id>/tenancies")
@login_required
def add_settlement_tenancy(settlement_id: str):
    store = _store()
    try:
        store._require_editable(settlement_id)
        if request.form.get("object_id", "") not in store._settlement_unit_ids(settlement_id):
            raise ValueError("Objekt gehört nicht zur Abrechnung")
        store.add_tenancy(
            request.form.get("object_id", ""), request.form.get("contact_id", ""),
            request.form.get("starts_on", ""), request.form.get("ends_on", ""), _actor(),
            federation_peer_id=request.form.get("federation_peer_id", ""),
            contract_document_id=request.form.get("contract_document_id", ""), note=request.form.get("note", ""),
        )
        flash("Mietverhältnis gespeichert.")
    except ValueError as exc: flash(str(exc))
    return _back("rentals.settlement_detail", settlement_id=settlement_id)


@bp.post("/settlements/<settlement_id>/metrics")
@login_required
def add_settlement_metric(settlement_id: str):
    store = _store()
    try:
        store._require_editable(settlement_id)
        if request.form.get("object_id", "") not in store._settlement_unit_ids(settlement_id):
            raise ValueError("Objekt gehört nicht zur Abrechnung")
        store.add_metric(
            request.form.get("object_id", ""), request.form.get("metric_type", ""), request.form.get("value", ""),
            request.form.get("valid_from", ""), request.form.get("valid_to", ""), _actor(),
            source_kind=request.form.get("source_kind", "manual"), source_note=request.form.get("source_note", ""),
            source_document_id=request.form.get("source_document_id", ""),
        )
        flash("Schlüsselwert gespeichert.")
    except ValueError as exc: flash(str(exc))
    return _back("rentals.settlement_detail", settlement_id=settlement_id)


@bp.post("/settlements/<settlement_id>/costs")
@login_required
def add_cost(settlement_id: str):
    try:
        _store().add_cost(
            settlement_id, request.form.get("cost_group", ""), request.form.get("description", ""), request.form.get("amount", ""),
            request.form.get("starts_on", ""), request.form.get("ends_on", ""), request.form.get("allocation_method", ""), _actor(),
            direct_object_id=request.form.get("direct_object_id", ""), source_kind=request.form.get("source_kind", "manual"),
            source_note=request.form.get("source_note", ""), source_document_id=request.form.get("source_document_id", ""),
            tenant_visible=request.form.get("tenant_visible") == "1",
        )
        flash("Kostenposition gespeichert.")
    except ValueError as exc: flash(str(exc))
    return _back("rentals.settlement_detail", settlement_id=settlement_id)


@bp.post("/settlements/<settlement_id>/costs/<cost_id>/delete")
@login_required
def delete_cost(settlement_id: str, cost_id: str):
    try: _store().delete_cost(settlement_id, cost_id, _actor()); flash("Kostenposition gelöscht.")
    except ValueError as exc: flash(str(exc))
    return _back("rentals.settlement_detail", settlement_id=settlement_id)


@bp.post("/settlements/<settlement_id>/costs/<cost_id>/weights")
@login_required
def set_weight(settlement_id: str, cost_id: str):
    try:
        _store().set_manual_weight(
            settlement_id, cost_id, request.form.get("object_id", ""), request.form.get("weight", ""), _actor(),
            source_kind=request.form.get("source_kind", "manual"), source_note=request.form.get("source_note", ""),
            source_document_id=request.form.get("source_document_id", ""),
        )
        flash("Manueller Verteilungsschlüssel gespeichert.")
    except ValueError as exc: flash(str(exc))
    return _back("rentals.settlement_detail", settlement_id=settlement_id)


@bp.post("/settlements/<settlement_id>/ledger")
@login_required
def add_ledger(settlement_id: str):
    try:
        _store()._require_editable(settlement_id)
        _store().add_ledger_entry(
            request.form.get("object_id", ""), request.form.get("contact_id", ""), request.form.get("booked_on", ""),
            request.form.get("kind", ""), request.form.get("amount", ""), _actor(), note=request.form.get("note", ""),
            document_id=request.form.get("document_id", ""), source_kind=request.form.get("source_kind", "manual"),
        )
        flash("Mieterkonto aktualisiert.")
    except ValueError as exc: flash(str(exc))
    return _back("rentals.settlement_detail", settlement_id=settlement_id)


@bp.post("/settlements/<settlement_id>/review")
@login_required
def mark_review(settlement_id: str):
    try: _store().set_status(settlement_id, "review", _actor()); flash("Abrechnung ist jetzt zur Prüfung markiert.")
    except ValueError as exc: flash(str(exc))
    return _back("rentals.settlement_detail", settlement_id=settlement_id)


@bp.post("/settlements/<settlement_id>/approve")
@login_required
def approve(settlement_id: str):
    _require_admin()
    try:
        result = _store().approve(settlement_id, _actor())
        flash(f"Abrechnung freigegeben. Snapshot SHA-256: {result['settlement']['snapshot_sha256']}")
    except Exception as exc:
        flash(f"Freigabe fehlgeschlagen: {exc}")
    return _back("rentals.settlement_detail", settlement_id=settlement_id)


@bp.post("/settlements/<settlement_id>/correction")
@login_required
def correction(settlement_id: str):
    _require_admin()
    try:
        new = _store().clone_correction(settlement_id, _actor())
        flash("Korrektur als neue, bearbeitbare Version angelegt.")
        return _back("rentals.settlement_detail", settlement_id=new["settlement_id"])
    except ValueError as exc:
        flash(str(exc)); return _back("rentals.settlement_detail", settlement_id=settlement_id)


@bp.get("/settlements/<settlement_id>/files/<path:name>")
@login_required
def approval_file(settlement_id: str, name: str):
    _require_admin()
    store = _store(); settlement = store.settlement(settlement_id)
    if settlement["status"] not in {"approved", "sent", "corrected", "void"}: abort(403)
    directory = store.approval_directory(settlement_id).resolve(); path = (directory / name).resolve()
    if directory not in (path, *path.parents) or not path.is_file(): abort(404)
    if path.suffix.casefold() not in {".pdf", ".json", ".zip"}: abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@bp.get("/settlements/<settlement_id>/tenant/<contact_id>/package")
@login_required
def tenant_package(settlement_id: str, contact_id: str):
    _require_admin()
    try:
        path = _store().tenant_package(settlement_id, contact_id)
        _store().record_export(settlement_id, contact_id, "zip_download", path, _actor())
        return send_file(path, as_attachment=True, download_name=path.name)
    except ValueError as exc:
        flash(str(exc)); return _back("rentals.settlement_detail", settlement_id=settlement_id)


def _default_peer_for_tenant(store: RentalBillingStore, settlement_id: str, contact_id: str) -> str:
    settlement = store.settlement(settlement_id); unit_ids = store._settlement_unit_ids(settlement_id)
    period_start = settlement["starts_on"]; period_end = settlement["ends_on"]
    for object_id in unit_ids:
        for tenancy in store.tenancies(object_id):
            if tenancy["contact_id"] != contact_id or not tenancy.get("federation_peer_id"): continue
            end = tenancy.get("ends_on") or "9999-12-31"
            if tenancy["starts_on"] <= period_end and end >= period_start:
                return tenancy["federation_peer_id"]
    return ""


@bp.post("/settlements/<settlement_id>/tenant/<contact_id>/federate")
@login_required
def federate_tenant_package(settlement_id: str, contact_id: str):
    _require_admin(); store = _store()
    try:
        package = store.tenant_package(settlement_id, contact_id)  # hard approval gate
        peer_id = request.form.get("peer_id", "").strip() or _default_peer_for_tenant(store, settlement_id, contact_id)
        federation = FederationStore(_root()); peer = federation.get_peer(peer_id)
        if not peer or not peer.get("enabled"): raise ValueError("Kein aktiver Federation-Peer für den Mieter")
        if not _peer_allows_rental_send(peer.get("policy")):
            raise ValueError("Peer-Policy erlaubt den Versand von Mietabrechnungen nicht ausdrücklich")

        documents = DocumentStore(_root())
        imported = documents.import_file(package, _actor())
        document = documents.get_document(imported)
        path = (_root() / str(document.get("last_path", ""))).resolve()
        digest = str(document.get("sha256") or "").casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", digest): digest = sha256_file(path)
        manifest = build_manifest(path); job_id = transfer_id()
        federation.create_transfer(
            job_id, direction="outgoing", operation="COPY", blob_hash=digest, target_peer=peer_id,
            total_bytes=manifest["size"], total_chunks=manifest["chunk_count"], manifest=manifest,
        )
        federation.record_event(
            "rental_statement_transfer_created", transfer_id=job_id, peer_id=peer_id,
            detail={"settlement_id": settlement_id, "contact_id": contact_id, "document_id": document["document_id"], "snapshot_sha256": store.settlement(settlement_id)["snapshot_sha256"]},
        )
        if request.form.get("start_now", "1") == "1":
            push_blob_to_peer(_root(), job_id)
        store.record_export(settlement_id, contact_id, "federation", package, _actor(), document_id=document["document_id"], peer_id=peer_id, transfer_id=job_id)
        flash(f"Freigegebenes Mieterpaket als Federation-Transfer {job_id} bereitgestellt.")
    except Exception as exc:
        flash(f"Federation-Versand fehlgeschlagen: {exc}")
    return _back("rentals.settlement_detail", settlement_id=settlement_id)
