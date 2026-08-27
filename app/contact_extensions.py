"""CRM contact extensions and rich EML document preview helpers."""

from __future__ import annotations

import csv
import io
import json
import secrets
import sqlite3
import subprocess
import uuid
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from flask import Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .contact_store import ContactStore
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .mail_reader import _header, _message_text
from .osm_address import GEOFABRIK_REGIONS, LocalAddressIndex, search_address, unique_candidate
from tools.launcher import start_osm_index_worker


CRM_FILE = "contact-crm.json"


class ContactCRMStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / CONTROL_DIR / CRM_FILE
        self.lock_path = self.root / CONTROL_DIR / ".contact-crm-write.lock"

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                value.setdefault("records", {})
                value.setdefault("proposals", [])
                value.setdefault("tokens", [])
                return value
        except (OSError, json.JSONDecodeError):
            pass
        return {"records": {}, "proposals": [], "tokens": []}

    def _write(self, value: dict[str, Any]) -> None:
        atomic_json_write(self.path, value)

    def record(self, contact_id: str) -> dict[str, Any]:
        return dict(self._read()["records"].get(contact_id, {}))

    def save(self, contact_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        with exclusive_file_lock(self.lock_path):
            data = self._read()
            old = dict(data["records"].get(contact_id, {}))
            record = {
                "roles": sorted(set(values.get("roles", []))),
                "status": str(values.get("status", "active")),
                "customer_number": str(values.get("customer_number", "")).strip(),
                "supplier_number": str(values.get("supplier_number", "")).strip(),
                "discount": str(values.get("discount", "")).strip(),
                "payment_terms": str(values.get("payment_terms", "")).strip(),
                "payment_days": str(values.get("payment_days", "")).strip(),
                "currency": str(values.get("currency", "EUR")).strip() or "EUR",
                "tax_number": str(values.get("tax_number", "")).strip(),
                "vat_id": str(values.get("vat_id", "")).strip(),
                "bank_accounts": values.get("bank_accounts", []),
                "addresses": values.get("addresses", []),
                "communications": values.get("communications", []),
                "relations": values.get("relations", []),
                "notes": str(values.get("notes", "")).strip(),
                "activities": list(old.get("activities", []))[-500:],
                "history": list(old.get("history", []))[-200:],
                "updated_at": utc_now(), "updated_by": actor,
            }
            ignored = {"updated_at", "updated_by", "history", "activities", "bank_accounts"}
            changed = [key for key in record if key not in ignored and old.get(key) != record.get(key)]
            if changed:
                record["history"].append({"type": "crm_change", "at": record["updated_at"], "actor": actor, "fields": changed})
            data["records"][contact_id] = record
            self._write(data)
        DocumentStore(self.root).history.record("contact_crm_updated", actor, "contact-crm", contact_id, {"before": old, "after": record})
        return record

    def add_activity(self, contact_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        kind = str(values.get("kind", "note")).strip().casefold()
        direction = str(values.get("direction", "internal")).strip().casefold()
        if kind not in {"email", "phone", "meeting", "letter", "note"}: raise ValueError("unknown CRM activity type")
        if direction not in {"incoming", "outgoing", "internal"}: raise ValueError("unknown CRM activity direction")
        subject = " ".join(str(values.get("subject", "")).strip().split())[:300]
        note = str(values.get("note", "")).strip()[:8000]
        if not subject and not note: raise ValueError("subject or note is required")
        activity = {"activity_id": uuid.uuid4().hex, "type": "communication", "kind": kind, "direction": direction, "subject": subject, "note": note, "at": str(values.get("at", "")).strip()[:40] or utc_now(), "actor": actor}
        with exclusive_file_lock(self.lock_path):
            data = self._read(); record = dict(data["records"].get(contact_id, {})); activities = list(record.get("activities", [])); activities.append(activity)
            record["activities"] = activities[-500:]; record["updated_at"] = utc_now(); record["updated_by"] = actor
            data["records"][contact_id] = record; self._write(data)
        DocumentStore(self.root).history.record("contact_crm_activity_added", actor, "contact-crm", contact_id, activity)
        return activity

    def timeline(self, contact: dict[str, Any]) -> list[dict[str, Any]]:
        crm = self.record(str(contact.get("contact_id", "")))
        entries = [dict(item) for item in crm.get("activities", [])]
        entries.extend(dict(item) for item in crm.get("history", []))
        entries.extend({"type": "contact_change", **dict(item)} for item in contact.get("changes", []))
        return sorted(entries, key=lambda item: str(item.get("at", "")), reverse=True)

    def overview(self, contacts: list[dict[str, Any]], query: str = "", status: str = "", role: str = "", sort: str = "name", without_activity: bool = False) -> list[dict[str, Any]]:
        needle = query.strip().casefold(); records = self._read()["records"]; rows: list[dict[str, Any]] = []
        for contact in contacts:
            crm = dict(records.get(contact.get("contact_id", ""), {}))
            if status and crm.get("status", "active") != status: continue
            if role and role not in crm.get("roles", []): continue
            searchable = [*contact.get("fields", {}).values(), *contact.get("tags", []), *contact.get("groups", []), crm.get("customer_number", ""), crm.get("supplier_number", ""), crm.get("notes", "")]
            searchable.extend(value for item in crm.get("communications", []) for value in item.values())
            searchable.extend(value for item in crm.get("addresses", []) for value in item.values())
            searchable.extend(value for item in crm.get("activities", []) for value in (item.get("subject", ""), item.get("note", "")))
            if needle and needle not in " ".join(str(value) for value in searchable).casefold(): continue
            activities = crm.get("activities", [])
            if without_activity and activities: continue
            rows.append({"contact": contact, "crm": crm, "last_activity": max((str(item.get("at", "")) for item in activities), default=""), "activity_count": len(activities)})
        if sort == "recent":
            return sorted(rows, key=lambda row: (row["last_activity"], str(row["contact"].get("contact_id", ""))), reverse=True)
        return sorted(rows, key=lambda row: (str(row["contact"].get("fields", {}).get("display_name", "")).casefold(), str(row["contact"].get("contact_id", ""))))

    def create_update_token(self, contact_id: str, actor: str) -> str:
        data = self._read(); token = secrets.token_urlsafe(32)
        data["tokens"].append({"token": token, "contact_id": contact_id, "created_at": utc_now(), "created_by": actor, "used": False})
        self._write(data); return token

    def token(self, token: str) -> dict[str, Any] | None:
        return next((item for item in self._read()["tokens"] if item.get("token") == token and not item.get("used")), None)

    def submit_proposal(self, token: str, values: dict[str, str], remote: str) -> str:
        data = self._read(); token_row = next((item for item in data["tokens"] if item.get("token") == token and not item.get("used")), None)
        if token_row is None: raise ValueError("update link is invalid or already used")
        proposal_id = uuid.uuid4().hex
        data["proposals"].append({"proposal_id": proposal_id, "contact_id": token_row["contact_id"], "values": {key: str(value).strip() for key, value in values.items() if str(value).strip()}, "submitted_at": utc_now(), "remote": remote[:120], "status": "pending"})
        token_row["used"] = True; self._write(data); return proposal_id

    def proposals(self) -> list[dict[str, Any]]:
        return sorted(self._read()["proposals"], key=lambda item: item.get("submitted_at", ""), reverse=True)

    def resolve_proposal(self, proposal_id: str, action: str, accepted_fields: list[str], actor: str) -> dict[str, Any]:
        data = self._read(); proposal = next((item for item in data["proposals"] if item.get("proposal_id") == proposal_id), None)
        if proposal is None or proposal.get("status") != "pending": raise ValueError("proposal is unavailable")
        if action == "reject": proposal["status"] = "rejected"
        elif action == "accept":
            store = ContactStore(self.root); contact = store.get(proposal["contact_id"], actor); merged = dict(contact.get("fields", {}))
            for key in accepted_fields:
                if key in proposal.get("values", {}): merged[key] = proposal["values"][key]
            store.upsert({**merged, **{f"custom_{key}": value for key, value in merged.items() if key not in store.schema().get("aliases", {})}}, actor, proposal["contact_id"])
            proposal["status"] = "accepted"; proposal["accepted_fields"] = sorted(set(accepted_fields))
        else: raise ValueError("unknown proposal action")
        proposal["resolved_at"] = utc_now(); proposal["resolved_by"] = actor; self._write(data)
        DocumentStore(self.root).history.record("contact_external_update_resolved", actor, "contact-crm", proposal["contact_id"], proposal)
        return proposal


def _parse_rows(text: str, columns: int) -> list[list[str]]:
    result: list[list[str]] = []
    for line in str(text or "").splitlines():
        if not line.strip(): continue
        parts = [part.strip() for part in line.split("|")]; parts += [""] * max(0, columns - len(parts)); result.append(parts[:columns])
    return result


def _eml_preview(root: Path, document_id: str) -> dict[str, Any]:
    store = DocumentStore(root); document = store.get_document(document_id); path = root / str(document.get("last_path", ""))
    if path.suffix.casefold() != ".eml" or not path.is_file() or path.is_symlink(): raise ValueError("document is not a regular EML file")
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes()); attachments: list[dict[str, Any]] = []
    for index, part in enumerate(message.walk()):
        if part.get_content_disposition() != "attachment" and not part.get_filename(): continue
        payload = part.get_payload(decode=True) or b""
        attachments.append({"part": index, "name": _header(part.get_filename()) or f"Anhang-{index}", "type": part.get_content_type(), "size": len(payload)})
    return {"subject": _header(message.get("Subject")) or "(ohne Betreff)", "from": _header(message.get("From")), "to": _header(message.get("To")), "cc": _header(message.get("Cc")), "date": _header(message.get("Date")), "message_id": _header(message.get("Message-ID")), "text": _message_text(message), "attachments": attachments}


def register(bp) -> None:
    @bp.get("/documents/contacts/crm", endpoint="crm_overview")
    @login_required
    def crm_overview():
        actor = str(g.user["username"]); contacts_store = ContactStore(current_app.config["DOCUMENT_ROOT"]); contacts = [contact for contact in contacts_store.contacts(actor) if contacts_store.can_manage(contact["contact_id"], actor)]; store = ContactCRMStore(current_app.config["DOCUMENT_ROOT"])
        query = request.args.get("q", "").strip(); status = request.args.get("status", "").strip(); role = request.args.get("role", "").strip(); sort = request.args.get("sort", "name").strip(); without_activity = request.args.get("without_activity") == "1"
        rows = store.overview(contacts, query, status, role, sort, without_activity)
        stats = {"total": len(rows), "active": sum(row["crm"].get("status", "active") == "active" for row in rows), "prospect": sum(row["crm"].get("status") == "prospect" for row in rows), "without_activity": sum(not row["activity_count"] for row in rows)}
        return render_template("documents/contact_crm_overview.html", rows=rows, stats=stats, query=query, selected_status=status, selected_role=role, selected_sort=sort, without_activity=without_activity)

    @bp.get("/documents/contacts/crm.csv", endpoint="crm_export")
    @login_required
    def crm_export():
        actor = str(g.user["username"]); contacts_store = ContactStore(current_app.config["DOCUMENT_ROOT"]); contacts = [contact for contact in contacts_store.contacts(actor) if contacts_store.can_manage(contact["contact_id"], actor)]
        store = ContactCRMStore(current_app.config["DOCUMENT_ROOT"]); rows = store.overview(contacts, request.args.get("q", ""), request.args.get("status", ""), request.args.get("role", ""), request.args.get("sort", "name"), request.args.get("without_activity") == "1")
        headers = ("Name", "Company", "Email", "Phone", "Status", "Roles", "Customer number", "Supplier number", "Latest activity", "Activities") if g.language == "en" else ("Name", "Firma", "E-Mail", "Telefon", "Status", "Rollen", "Kundennummer", "Lieferantennummer", "Letzte Aktivität", "Aktivitäten")
        output = io.StringIO(); writer = csv.writer(output, delimiter=";"); writer.writerow(headers)
        for row in rows:
            fields = row["contact"].get("fields", {}); crm = row["crm"]
            writer.writerow((fields.get("display_name", ""), fields.get("company", ""), fields.get("email", ""), fields.get("phone", ""), crm.get("status", "active"), ", ".join(crm.get("roles", [])), crm.get("customer_number", ""), crm.get("supplier_number", ""), row["last_activity"], row["activity_count"]))
        return Response("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=crm-kontakte.csv"})

    @bp.get("/documents/contacts/address-search.json", endpoint="crm_address_search")
    @login_required
    def crm_address_search():
        query = request.args.get("q", "").strip(); country = request.args.get("country", "de").strip() or "de"
        root = current_app.config["DOCUMENT_ROOT"]
        if len(query) < 3: return jsonify({"candidates": [], "unique": None, "source": "local_osm", "ready": LocalAddressIndex(root).status()["ready"], "attribution": "© OpenStreetMap contributors"})
        try: candidates = search_address(query, root=root, country_code=country, limit=8)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            current_app.logger.warning("Local OSM address lookup failed: %s", exc)
            return jsonify({"error": "local_address_index_unavailable", "candidates": [], "unique": None, "source": "local_osm", "attribution": "© OpenStreetMap contributors"}), 503
        index_status = LocalAddressIndex(root).status()
        return jsonify({"candidates": candidates, "unique": unique_candidate(candidates), "source": "local_osm", "ready": index_status["ready"], "attribution": "© OpenStreetMap contributors"})

    @bp.get("/documents/contacts/osm-index/region-info.json", endpoint="crm_osm_region_info")
    @login_required
    def crm_osm_region_info():
        if not is_admin(g.user): abort(403)
        region = request.args.get("region", "").strip()
        index = LocalAddressIndex(current_app.config["DOCUMENT_ROOT"])
        try:
            return jsonify({"region": index.region_info(region), "status": index.status()})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @bp.post("/documents/contacts/osm-index/build", endpoint="crm_osm_build")
    @login_required
    def crm_osm_build():
        actor = str(g.user["username"])
        if not is_admin(g.user): abort(403)
        region = request.form.get("region", "").strip(); action = request.form.get("action", "download").strip(); index = LocalAddressIndex(current_app.config["DOCUMENT_ROOT"])
        try:
            if action == "reindex":
                if index.downloaded_source() is None:
                    raise ValueError("Kein bereits heruntergeladener OSM-Auszug vorhanden")
            else:
                index.download_region(region)
            start_osm_index_worker(current_app.config["DOCUMENT_ROOT"], force=True)
            flash("Neuaufbau des lokalen OSM-Adressindex wurde im Hintergrund gestartet.")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            current_app.logger.exception("OSM address index build failed")
            flash(f"OSM-Adressindex konnte nicht aufgebaut werden: {exc}")
        return redirect(request.referrer or url_for("documents.contacts"))

    @bp.route("/documents/contacts/<contact_id>/crm", methods=("GET", "POST"), endpoint="crm_contact")
    @login_required
    def crm_contact(contact_id: str):
        actor = str(g.user["username"]); contacts = ContactStore(current_app.config["DOCUMENT_ROOT"]); contact = contacts.get(contact_id, actor)
        if not contacts.can_manage(contact_id, actor): abort(403)
        store = ContactCRMStore(current_app.config["DOCUMENT_ROOT"])
        if request.method == "POST":
            values = {"roles": request.form.getlist("roles"), "status": request.form.get("status", "active"), "customer_number": request.form.get("customer_number", ""), "supplier_number": request.form.get("supplier_number", ""), "discount": request.form.get("discount", ""), "payment_terms": request.form.get("payment_terms", ""), "payment_days": request.form.get("payment_days", ""), "currency": request.form.get("currency", "EUR"), "tax_number": request.form.get("tax_number", ""), "vat_id": request.form.get("vat_id", ""), "notes": request.form.get("notes", ""), "addresses": [dict(zip(("type", "street", "postal", "city", "country"), row)) for row in _parse_rows(request.form.get("addresses", ""), 5)], "communications": [dict(zip(("type", "value", "preferred"), row)) for row in _parse_rows(request.form.get("communications", ""), 3)], "bank_accounts": [dict(zip(("holder", "iban", "bic", "bank"), row)) for row in _parse_rows(request.form.get("bank_accounts", ""), 4)], "relations": [dict(zip(("type", "contact_id"), row)) for row in _parse_rows(request.form.get("relations", ""), 2)]}
            store.save(contact_id, values, actor); flash("CRM-Daten gespeichert. CardDAV-Änderungen können diese Daten nicht löschen."); return redirect(url_for("contact_audit.crm_contact", contact_id=contact_id))
        index = LocalAddressIndex(current_app.config["DOCUMENT_ROOT"])
        return render_template("documents/contact_crm.html", contact=contact, crm=store.record(contact_id), timeline=store.timeline(contact), all_contacts=contacts.contacts(actor), osm_status=index.status(), osm_regions=GEOFABRIK_REGIONS, osm_admin=is_admin(g.user))

    @bp.post("/documents/contacts/<contact_id>/crm/activity", endpoint="crm_add_activity")
    @login_required
    def crm_add_activity(contact_id: str):
        actor = str(g.user["username"]); contacts = ContactStore(current_app.config["DOCUMENT_ROOT"])
        if not contacts.can_manage(contact_id, actor): abort(403)
        try:
            ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).add_activity(contact_id, request.form, actor); flash("CRM activity saved." if g.language == "en" else "CRM-Aktivität gespeichert.")
        except ValueError as exc:
            messages = {
                "unknown CRM activity type": ("Unbekannte CRM-Aktivitätsart.", "Unknown CRM activity type."),
                "unknown CRM activity direction": ("Unbekannte Richtung der CRM-Aktivität.", "Unknown CRM activity direction."),
                "subject or note is required": ("Betreff oder Notiz ist erforderlich.", "Subject or note is required."),
            }
            german, english = messages.get(str(exc), ("CRM-Aktivität konnte nicht gespeichert werden.", "CRM activity could not be saved."))
            flash(english if g.language == "en" else german)
        return redirect(url_for("contact_audit.crm_contact", contact_id=contact_id) + "#crm-timeline")

    @bp.post("/documents/contacts/<contact_id>/crm/update-link", endpoint="crm_update_link")
    @login_required
    def crm_update_link(contact_id: str):
        actor = str(g.user["username"]); contacts = ContactStore(current_app.config["DOCUMENT_ROOT"])
        if not contacts.can_manage(contact_id, actor): abort(403)
        token = ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).create_update_token(contact_id, actor); flash("Externer Aktualisierungslink: " + url_for("contact_audit.crm_public_update", token=token, _external=True)); return redirect(url_for("contact_audit.crm_contact", contact_id=contact_id))

    @bp.route("/contact-update/<token>", methods=("GET", "POST"), endpoint="crm_public_update")
    def crm_public_update(token: str):
        store = ContactCRMStore(current_app.config["DOCUMENT_ROOT"]); row = store.token(token)
        if row is None: abort(404)
        contact = ContactStore(current_app.config["DOCUMENT_ROOT"]).get(row["contact_id"])
        if request.method == "POST":
            store.submit_proposal(token, {"display_name": request.form.get("display_name", ""), "first_name": request.form.get("first_name", ""), "last_name": request.form.get("last_name", ""), "email": request.form.get("email", ""), "phone": request.form.get("phone", ""), "company": request.form.get("company", ""), "note": request.form.get("note", "")}, request.remote_addr or "")
            return render_template("documents/contact_update_public.html", contact=contact, submitted=True)
        return render_template("documents/contact_update_public.html", contact=contact, submitted=False)

    @bp.get("/documents/contacts/proposals", endpoint="crm_proposals")
    @login_required
    def crm_proposals(): return render_template("documents/contact_proposals.html", proposals=ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).proposals())

    @bp.post("/documents/contacts/proposals/<proposal_id>", endpoint="crm_resolve_proposal")
    @login_required
    def crm_resolve_proposal(proposal_id: str):
        try: ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).resolve_proposal(proposal_id, request.form.get("action", "reject"), request.form.getlist("fields"), str(g.user["username"])); flash("Externe Kontaktänderung verarbeitet.")
        except ValueError as exc: flash(str(exc))
        return redirect(url_for("contact_audit.crm_proposals"))

    @bp.get("/documents/<document_id>/eml-preview", endpoint="eml_preview")
    @login_required
    def eml_preview(document_id: str):
        try: preview = _eml_preview(Path(current_app.config["DOCUMENT_ROOT"]), document_id)
        except (OSError, ValueError): abort(404)
        return render_template("documents/eml_document_preview.html", preview=preview, document_id=document_id)

    @bp.get("/documents/<document_id>/eml-metadata.json", endpoint="eml_metadata")
    @login_required
    def eml_metadata(document_id: str):
        try: preview = _eml_preview(Path(current_app.config["DOCUMENT_ROOT"]), document_id)
        except (OSError, ValueError): abort(404)
        return jsonify({key: preview[key] for key in ("subject", "from", "to", "cc", "date", "message_id")})
