"""Ortstermin capture, network maps, finding chains and customer reports."""
from __future__ import annotations

import os
from datetime import date
from io import BytesIO
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request, send_file, url_for
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import createBarcodeDrawing

from .auth import login_required
from .site_visit_report import build_visit_report
from .site_visit_scan import scan_authorized_private_network
from .site_visit_store import EVIDENCE_CLASSES, FINDING_STATES, RELATIONS, SiteVisitStore
from .todo_store import TodoStore

bp = Blueprint("site_visits", __name__, url_prefix="/site-visits")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _store() -> SiteVisitStore:
    return SiteVisitStore(current_app.config["DOCUMENT_ROOT"])


def _actor() -> str:
    return str(g.user["username"])


def _visit(visit_id: str) -> dict:
    try:
        return _store().get(visit_id, _actor())
    except ValueError:
        abort(404)


def _redirect(visit_id: str):
    return redirect(url_for("site_visits.detail", visit_id=visit_id))


def _form_values() -> dict:
    values = request.form.to_dict()
    custom = {}
    for key, value in request.form.items():
        if key.startswith("custom_"):
            custom[key[7:]] = value
    if custom:
        values["custom_values"] = custom
    return values


def _selected_report_ids(record: dict, raw_ids: list[str], *, defaults: bool = True) -> list[str]:
    available = {row["finding_id"]: row for row in record.get("findings", [])}
    if not raw_ids:
        if defaults:
            raw_ids = [row["finding_id"] for row in record.get("findings", []) if row.get("include_in_report")]
        else:
            return []
    if any(finding_id not in available for finding_id in raw_ids):
        raise ValueError("Ein ausgewählter Befund gehört nicht zu diesem Ortstermin")
    selected = []
    for finding_id in raw_ids:
        row = available[finding_id]
        if row.get("status") in {"confirmed", "corrected"} and finding_id not in selected:
            selected.append(finding_id)
    return selected


@bp.get("")
@login_required
def index():
    return render_template("site_visits/index.html", visits=_store().list(_actor()), today=date.today().isoformat())


@bp.post("")
@login_required
def create():
    values = request.form.to_dict()
    title = str(values.get("title", "")).strip()
    if not title:
        flash("Bitte einen Titel für den Ortstermin eingeben.", "warning")
        return redirect(url_for("site_visits.index"))
    actor = _actor()
    task = None
    try:
        todo_store = TodoStore(current_app.config["DOCUMENT_ROOT"])
        task = todo_store.add(
            "Ortstermin: " + title[:220], actor,
            {"list_id": todo_store.default_list_id(actor), "due": str(values.get("visit_date", ""))[:40], "description": str(values.get("notes", ""))[:2000], "categories": ["Ortstermin"], "status": "needs-action"},
        )
        visit = _store().create(actor, {**values, "todo_id": task["id"]})
    except (OSError, ValueError) as exc:
        if task:
            try:
                todo_store.soft_delete(task["id"], actor)
            except (OSError, ValueError):
                current_app.logger.warning("Unable to roll back linked field visit task")
        flash(str(exc), "danger")
        return redirect(url_for("site_visits.index"))
    flash("Ortstermin und verknüpfte Aufgabe wurden angelegt.", "success")
    return redirect(url_for("site_visits.detail", visit_id=visit["visit_id"]))


@bp.get("/<visit_id>")
@login_required
def detail(visit_id: str):
    record = _visit(visit_id)
    assets = record.get("assets", [])
    rooms = record.get("rooms", [])
    assets_by_room: dict[str, list[dict]] = {}
    for asset in assets:
        assets_by_room.setdefault(asset.get("room_id", ""), []).append(asset)
    floorplan_url = ""
    attachment_id = record.get("floorplan_attachment_id", "")
    if attachment_id:
        floorplan_url = url_for("site_visits.floorplan", visit_id=visit_id, attachment_id=attachment_id)
    selected = request.args.get("asset", "")
    return render_template(
        "site_visits/detail.html", visit=record, rooms=rooms, assets=assets,
        assets_by_room=assets_by_room, selected_asset=selected, floorplan_url=floorplan_url,
        statuses=FINDING_STATES, evidence_classes=EVIDENCE_CLASSES, relations=RELATIONS,
        power_totals=SiteVisitStore.power_totals(record),
    )


@bp.post("/<visit_id>/update")
@login_required
def update(visit_id: str):
    _visit(visit_id)
    try:
        _store().update(visit_id, _actor(), _form_values())
        flash("Ortstermin gespeichert.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.post("/<visit_id>/rooms")
@login_required
def add_room(visit_id: str):
    _visit(visit_id)
    try:
        _store().add_room(visit_id, _actor(), _form_values())
        flash("Raum ergänzt.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.post("/<visit_id>/assets")
@login_required
def add_asset(visit_id: str):
    _visit(visit_id)
    try:
        _store().add_asset(visit_id, _actor(), _form_values())
        flash("Gerät ergänzt.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.post("/<visit_id>/assets/<asset_id>")
@login_required
def update_asset(visit_id: str, asset_id: str):
    _visit(visit_id)
    try:
        _store().update_asset(visit_id, _actor(), asset_id, _form_values())
        flash("Gerätedaten gespeichert.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.post("/<visit_id>/assets/<asset_id>/credentials")
@login_required
def save_credentials(visit_id: str, asset_id: str):
    _visit(visit_id)
    try:
        _store().set_credentials(visit_id, _actor(), asset_id, request.form.get("username", ""), request.form.get("password", ""), request.form.get("note", ""))
        flash("Zugangsdaten wurden verschlüsselt gespeichert. Sie erscheinen nicht auf Labels oder im Bericht.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.post("/<visit_id>/assets/<asset_id>/credentials/reveal")
@login_required
def reveal_credentials(visit_id: str, asset_id: str):
    record = _visit(visit_id)
    try:
        credentials = _store().credentials(visit_id, _actor(), asset_id)
        _store().record_event(visit_id, _actor(), "credentials_viewed", "asset", asset_id)
    except ValueError:
        abort(404)
    asset = next((row for row in record["assets"] if row["asset_id"] == asset_id), None)
    response = make_response(render_template("site_visits/credentials.html", visit=record, credentials=credentials, asset=asset))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/<visit_id>/findings")
@login_required
def add_finding(visit_id: str):
    _visit(visit_id)
    try:
        _store().add_finding(visit_id, _actor(), _form_values())
        flash("Befund aufgenommen. Er bleibt zunächst als ungeprüft markiert.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.post("/<visit_id>/findings/<finding_id>")
@login_required
def update_finding(visit_id: str, finding_id: str):
    _visit(visit_id)
    try:
        _store().update_finding(visit_id, _actor(), finding_id, _form_values())
        flash("Befundbewertung gespeichert.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.post("/<visit_id>/finding-links")
@login_required
def add_finding_link(visit_id: str):
    _visit(visit_id)
    try:
        _store().add_edge(visit_id, _actor(), request.form.get("source_id", ""), request.form.get("target_id", ""), request.form.get("relation", ""), request.form.get("note", ""))
        flash("Befundkette verknüpft.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.post("/<visit_id>/connections")
@login_required
def add_connection(visit_id: str):
    _visit(visit_id)
    try:
        _store().add_connection(visit_id, _actor(), _form_values())
        flash("Verbindung eingetragen.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.post("/<visit_id>/custom-fields")
@login_required
def add_custom_field(visit_id: str):
    _visit(visit_id)
    choices = [value.strip() for value in request.form.get("choices", "").split(",") if value.strip()]
    try:
        _store().add_custom_field(visit_id, _actor(), request.form.get("label", ""), request.form.get("field_type", "text"), choices)
        flash("Freies Befundfeld ergänzt.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.post("/<visit_id>/attachments")
@login_required
def add_attachment(visit_id: str):
    _visit(visit_id)
    if request.content_length and request.content_length > 17 * 1024 * 1024:
        flash("Anhänge dürfen einschließlich Formular höchstens 16 MiB groß sein.", "danger")
        return _redirect(visit_id)
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Bitte eine Datei auswählen.", "warning")
        return _redirect(visit_id)
    target_type = request.form.get("target_type", "visit")
    target_id = request.form.get("target_id", visit_id)
    purpose = request.form.get("purpose", "evidence")
    try:
        data = upload.stream.read(16 * 1024 * 1024 + 1)
        _store().add_attachment(visit_id, _actor(), upload.filename, upload.mimetype or "", data, target_type, target_id, purpose)
        flash("Datei wurde sicher dem Ortstermin zugeordnet.", "success")
    except (OSError, ValueError) as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.get("/<visit_id>/attachments/<attachment_id>")
@login_required
def attachment(visit_id: str, attachment_id: str):
    try:
        path, metadata = _store().attachment_path(visit_id, _actor(), attachment_id)
    except ValueError:
        abort(404)
    response = send_file(path, mimetype=metadata["content_type"], as_attachment=True, download_name=metadata["filename"], max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("/<visit_id>/floorplan/<attachment_id>")
@login_required
def floorplan(visit_id: str, attachment_id: str):
    try:
        path, metadata = _store().attachment_path(visit_id, _actor(), attachment_id)
    except ValueError:
        abort(404)
    if metadata.get("purpose") != "floorplan" or metadata.get("content_type") not in ALLOWED_IMAGE_TYPES:
        abort(404)
    response = send_file(path, mimetype=metadata["content_type"], as_attachment=False, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/<visit_id>/snapshots")
@login_required
def create_snapshot(visit_id: str):
    _visit(visit_id)
    try:
        snapshot = _store().add_snapshot(visit_id, _actor(), request.form.get("name", ""))
        flash(f"Bestand '{snapshot['name']}' gespeichert.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.get("/<visit_id>/snapshot-diff")
@login_required
def snapshot_diff(visit_id: str):
    record = _visit(visit_id)
    older, newer = request.args.get("older", ""), request.args.get("newer", "")
    diff = None
    if older and newer:
        try:
            diff = _store().snapshot_diff(visit_id, _actor(), older, newer)
        except ValueError as exc:
            flash(str(exc), "danger")
    return render_template("site_visits/snapshot_diff.html", visit=record, diff=diff, older=older, newer=newer)


@bp.post("/<visit_id>/network-scan")
@login_required
def network_scan(visit_id: str):
    _visit(visit_id)
    try:
        scan = scan_authorized_private_network(request.form.get("cidr", ""), request.form.get("ports", ""), request.form.get("approved") == "1", profiles=request.form.getlist("profiles"))
        _store().apply_scan(visit_id, _actor(), {**scan, "device_count": len(scan["devices"])})
        flash(f"Scan abgeschlossen: {len(scan['devices'])} Geräte mit mindestens einem offenen TCP-Port erkannt.", "success")
    except (OSError, ValueError) as exc:
        flash(str(exc), "danger")
    return _redirect(visit_id)


@bp.get("/<visit_id>/labels/<asset_id>")
@login_required
def label_page(visit_id: str, asset_id: str):
    record = _visit(visit_id)
    asset = next((row for row in record.get("assets", []) if row.get("asset_id") == asset_id), None)
    if not asset:
        abort(404)
    return render_template("site_visits/label.html", visit=record, asset=asset)


@bp.get("/<visit_id>/labels/<asset_id>.svg")
@login_required
def label_svg(visit_id: str, asset_id: str):
    record = _visit(visit_id)
    asset = next((row for row in record.get("assets", []) if row.get("asset_id") == asset_id), None)
    if not asset:
        abort(404)
    public_base = os.environ.get("SIMPLEOFFICE_SERVER_PUBLIC_URL", "").strip() or os.environ.get("SIMPLEOFFICE_FEDERATION_PUBLIC_URL", "").strip()
    if not public_base:
        return "SIMPLEOFFICE_SERVER_PUBLIC_URL muss für QR-Labels gesetzt sein.", 503, {"Cache-Control": "no-store"}
    parsed = urlsplit(public_base)
    try:
        parsed.port
    except ValueError:
        return "Die konfigurierte Server-URL ist ungültig.", 503, {"Cache-Control": "no-store"}
    local_host = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and local_host)) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return "Die konfigurierte Server-URL ist ungültig. Öffentlich erreichbare QR-Labels benötigen HTTPS.", 503, {"Cache-Control": "no-store"}
    link = public_base.rstrip("/") + url_for("site_visits.detail", visit_id=visit_id) + "?asset=" + asset_id
    try:
        barcode = createBarcodeDrawing("QR", value=link, barLevel="M", width=110, height=110)
        svg = renderSVG.drawToString(barcode)
        if isinstance(svg, bytes):
            svg = svg.decode("utf-8")
    except Exception as exc:
        current_app.logger.warning("QR label generation failed: %s", type(exc).__name__)
        return "QR-Label konnte nicht erzeugt werden.", 500, {"Cache-Control": "no-store"}
    response = current_app.response_class(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("/<visit_id>/report")
@login_required
def report_preview(visit_id: str):
    record = _visit(visit_id)
    try:
        selected = _selected_report_ids(record, request.args.getlist("finding_id"))
    except ValueError:
        abort(400)
    return render_template("site_visits/report.html", visit=record, selected_ids=selected)


@bp.post("/<visit_id>/report.pdf")
@login_required
def report_pdf(visit_id: str):
    record = _visit(visit_id)
    try:
        selected = _selected_report_ids(record, request.form.getlist("finding_id"), defaults=False)
        payload, digest, finding_ids = build_visit_report(record, selected)
        _store().record_report(visit_id, _actor(), {"finding_ids": finding_ids, "sha256": digest})
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("site_visits.report_preview", visit_id=visit_id))
    name = "Ortsterminbericht-" + record["visit_id"][:8] + ".pdf"
    response = send_file(BytesIO(payload), mimetype="application/pdf", as_attachment=True, download_name=name, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
