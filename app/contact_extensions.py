"""CRM contact extensions and rich EML document preview helpers."""

from __future__ import annotations

import csv
import io
import json
import secrets
import sqlite3
import uuid
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from flask import Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .auth import login_required
from .contact_store import ContactStore
from .contact_management import ContactManagement
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .mail_reader import _header, _message_text
from .osm_address import LocalAddressIndex, field_suggestions, search_address, unique_candidate
from .settings_store import translate


CRM_FILE = "contact-crm.json"
EXTERNAL_SCALAR_FIELDS = (
    "first_name", "last_name", "display_name", "nickname", "company",
    "department", "title", "role", "email", "phone", "birthday", "website", "note",
)
EXTERNAL_TYPED_FIELDS = {
    "email_private": ("EMAIL", "HOME"),
    "email_business": ("EMAIL", "WORK"),
    "mobile": ("TEL", "CELL"),
    "phone_private": ("TEL", "HOME"),
    "phone_business": ("TEL", "WORK"),
    "fax": ("TEL", "FAX"),
}
EXTERNAL_ADDRESS_FIELDS = ("address_city", "address_postal", "address_street", "address_state", "address_country")
EXTERNAL_UPDATE_FIELDS = (*EXTERNAL_SCALAR_FIELDS, *EXTERNAL_TYPED_FIELDS, *EXTERNAL_ADDRESS_FIELDS, "tags")


def _vcard_types(line: str) -> set[str]:
    header = line.partition(":")[0]
    result: set[str] = set()
    for parameter in header.split(";")[1:]:
        value = parameter.split("=", 1)[1] if "=" in parameter else parameter
        result.update(item.strip().upper() for item in value.split(",") if item.strip())
    return result


def _vcard_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _external_update_values(contact: dict[str, Any]) -> dict[str, str]:
    fields = contact.get("fields", {})
    values = {key: str(fields.get(key, "")) for key in EXTERNAL_SCALAR_FIELDS}
    values.update({key: "" for key in EXTERNAL_TYPED_FIELDS})
    values.update({key: "" for key in EXTERNAL_ADDRESS_FIELDS})
    values["tags"] = ", ".join(contact.get("tags", []))
    for key, raw in fields.items():
        if not key.startswith("vcard_") or not ContactStore._safe_raw_vcard_line(raw):
            continue
        name = ContactStore._vcard_property_name(raw)
        types = _vcard_types(raw)
        raw_value = ContactStore._unescape_vcard_text(raw.partition(":")[2])
        for target, (property_name, type_name) in EXTERNAL_TYPED_FIELDS.items():
            if name == property_name and type_name in types and not values[target]:
                values[target] = raw_value
        if name == "ADR" and not values["address_street"]:
            parts = ContactStore._split_vcard_components(raw.partition(":")[2])
            values["address_street"] = parts[2] if len(parts) > 2 else ""
            values["address_city"] = parts[3] if len(parts) > 3 else ""
            values["address_state"] = parts[4] if len(parts) > 4 else ""
            values["address_postal"] = parts[5] if len(parts) > 5 else ""
            values["address_country"] = parts[6] if len(parts) > 6 else ""
    return values


def _raw_field_changes(fields: dict[str, str], accepted: set[str], proposed: dict[str, str]) -> dict[str, str]:
    changes: dict[str, str] = {}
    for target, (property_name, type_name) in EXTERNAL_TYPED_FIELDS.items():
        if target not in accepted:
            continue
        for key, raw in fields.items():
            if key.startswith("vcard_") and ContactStore._vcard_property_name(raw) == property_name and type_name in _vcard_types(raw):
                changes[key] = ""
        value = proposed.get(target, "").strip()
        if value:
            changes[f"vcard_external_{target}_{uuid.uuid4().hex[:8]}"] = f"{property_name};TYPE={type_name}:{_vcard_escape(value)}"
    if accepted.intersection(EXTERNAL_ADDRESS_FIELDS):
        current = _external_update_values({"fields": fields})
        address = {key: proposed.get(key, current.get(key, "")) if key in accepted else current.get(key, "") for key in EXTERNAL_ADDRESS_FIELDS}
        replaced = False
        for key, raw in fields.items():
            if key.startswith("vcard_") and ContactStore._vcard_property_name(raw) == "ADR" and not replaced:
                changes[key] = ""; replaced = True
        if any(address.values()):
            components = ("", "", address["address_street"], address["address_city"], address["address_state"], address["address_postal"], address["address_country"])
            changes[f"vcard_external_address_{uuid.uuid4().hex[:8]}"] = "ADR;TYPE=HOME:" + ";".join(_vcard_escape(value) for value in components)
    return changes


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
        with exclusive_file_lock(self.lock_path):
            data = self._read(); token = secrets.token_urlsafe(32)
            data["tokens"].append({"token": token, "contact_id": contact_id, "created_at": utc_now(), "created_by": actor, "used": False})
            self._write(data)
        return token

    def token(self, token: str) -> dict[str, Any] | None:
        return next((item for item in self._read()["tokens"] if item.get("token") == token and not item.get("used")), None)

    def submit_proposal(self, token: str, values: dict[str, str], remote: str) -> str:
        with exclusive_file_lock(self.lock_path):
            data = self._read(); token_row = next((item for item in data["tokens"] if item.get("token") == token and not item.get("used")), None)
            if token_row is None: raise ValueError("update link is invalid or already used")
            proposal_id = uuid.uuid4().hex
            clean_values = {key: str(value).strip() for key, value in values.items() if key in EXTERNAL_UPDATE_FIELDS}
            if not clean_values: raise ValueError("update proposal contains no changes")
            data["proposals"].append({"proposal_id": proposal_id, "contact_id": token_row["contact_id"], "values": clean_values, "submitted_at": utc_now(), "remote": remote[:120], "status": "pending"})
            token_row["used"] = True; self._write(data)
        return proposal_id

    def proposals(self) -> list[dict[str, Any]]:
        return sorted(self._read()["proposals"], key=lambda item: item.get("submitted_at", ""), reverse=True)

    def resolve_proposal(self, proposal_id: str, action: str, accepted_fields: list[str], actor: str) -> dict[str, Any]:
        with exclusive_file_lock(self.lock_path):
            data = self._read(); proposal = next((item for item in data["proposals"] if item.get("proposal_id") == proposal_id), None)
            if proposal is None or proposal.get("status") != "pending": raise ValueError("proposal is unavailable")
            contact_store = ContactStore(self.root)
            if not contact_store.can_manage(proposal["contact_id"], actor): raise ValueError("contact is not shared with this user")
            if action == "reject": proposal["status"] = "rejected"
            elif action == "accept":
                store = contact_store; contact = store.get(proposal["contact_id"], actor); proposed = proposal.get("values", {})
                accepted = {key for key in accepted_fields if key in EXTERNAL_UPDATE_FIELDS and key in proposed}
                field_changes = {key: proposed[key] for key in accepted.intersection(EXTERNAL_SCALAR_FIELDS)}
                field_changes.update(_raw_field_changes(contact.get("fields", {}), accepted, proposed))
                if field_changes:
                    store.patch_fields(proposal["contact_id"], field_changes, actor)
                if "tags" in accepted:
                    current = store.get(proposal["contact_id"], actor)
                    ContactManagement(self.root).update_metadata(
                        proposal["contact_id"], actor,
                        proposed.get("tags", "").split(","), current.get("groups", []),
                    )
                proposal["status"] = "accepted"; proposal["accepted_fields"] = sorted(accepted)
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


def _parse_address_rows(text: str) -> list[dict[str, str]]:
    result = []
    for line in str(text or "").splitlines():
        if not line.strip(): continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) == 5: parts.insert(4, "")  # legacy row without STATE
        parts += [""] * max(0, 6 - len(parts))
        result.append(dict(zip(("type", "street", "postal", "city", "state", "country"), parts[:6])))
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
        header_keys = ("name", "company", "email", "phone", "status", "roles", "customer_number", "supplier_number", "latest_activity", "activities")
        headers = tuple(translate(g.language, f"crm.csv.{key}") for key in header_keys)
        output = io.StringIO(); writer = csv.writer(output, delimiter=";"); writer.writerow(headers)
        for row in rows:
            fields = row["contact"].get("fields", {}); crm = row["crm"]
            writer.writerow((fields.get("display_name", ""), fields.get("company", ""), fields.get("email", ""), fields.get("phone", ""), crm.get("status", "active"), ", ".join(crm.get("roles", [])), crm.get("customer_number", ""), crm.get("supplier_number", ""), row["last_activity"], row["activity_count"]))
        return Response("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=crm-kontakte.csv"})

    @bp.get("/documents/contacts/address-search.json", endpoint="crm_address_search")
    @login_required
    def crm_address_search():
        query = request.args.get("q", "").strip(); country = request.args.get("country", "de").strip() or "de"; field = request.args.get("field", "").strip()
        root = current_app.config["DOCUMENT_ROOT"]; index_status = LocalAddressIndex(root).status()
        if len(query) < 3: return jsonify({"candidates": [], "suggestions": [], "unique": None, "shown": 0, "index_count": index_status["count"], "source": "local_osm", "ready": index_status["ready"], "attribution": "© OpenStreetMap contributors"})
        try: candidates = search_address(query, root=root, country_code=country, limit=8)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            current_app.logger.warning("Local OSM address lookup failed: %s", exc)
            return jsonify({"error": "local_address_index_unavailable", "candidates": [], "unique": None, "source": "local_osm", "attribution": "© OpenStreetMap contributors"}), 503
        return jsonify({"candidates": candidates, "suggestions": field_suggestions(candidates, field), "unique": unique_candidate(candidates), "shown": len(candidates), "index_count": index_status["count"], "source": "local_osm", "ready": index_status["ready"], "attribution": "© OpenStreetMap contributors"})

    @bp.route("/documents/contacts/<contact_id>/crm", methods=("GET", "POST"), endpoint="crm_contact")
    @login_required
    def crm_contact(contact_id: str):
        actor = str(g.user["username"]); contacts = ContactStore(current_app.config["DOCUMENT_ROOT"]); contact = contacts.get(contact_id, actor)
        if not contacts.can_manage(contact_id, actor): abort(403)
        store = ContactCRMStore(current_app.config["DOCUMENT_ROOT"])
        if request.method == "POST":
            values = {"roles": request.form.getlist("roles"), "status": request.form.get("status", "active"), "customer_number": request.form.get("customer_number", ""), "supplier_number": request.form.get("supplier_number", ""), "discount": request.form.get("discount", ""), "payment_terms": request.form.get("payment_terms", ""), "payment_days": request.form.get("payment_days", ""), "currency": request.form.get("currency", "EUR"), "tax_number": request.form.get("tax_number", ""), "vat_id": request.form.get("vat_id", ""), "notes": request.form.get("notes", ""), "addresses": _parse_address_rows(request.form.get("addresses", "")), "communications": [dict(zip(("type", "value", "preferred"), row)) for row in _parse_rows(request.form.get("communications", ""), 3)], "bank_accounts": [dict(zip(("holder", "iban", "bic", "bank"), row)) for row in _parse_rows(request.form.get("bank_accounts", ""), 4)], "relations": [dict(zip(("type", "contact_id"), row)) for row in _parse_rows(request.form.get("relations", ""), 2)]}
            store.save(contact_id, values, actor); flash("CRM-Daten gespeichert. CardDAV-Änderungen können diese Daten nicht löschen."); return redirect(url_for("contact_audit.crm_contact", contact_id=contact_id))
        index = LocalAddressIndex(current_app.config["DOCUMENT_ROOT"])
        return render_template("documents/contact_crm.html", contact=contact, crm=store.record(contact_id), timeline=store.timeline(contact), all_contacts=contacts.contacts(actor), osm_status=index.status())

    @bp.post("/documents/contacts/<contact_id>/crm/activity", endpoint="crm_add_activity")
    @login_required
    def crm_add_activity(contact_id: str):
        actor = str(g.user["username"]); contacts = ContactStore(current_app.config["DOCUMENT_ROOT"])
        if not contacts.can_manage(contact_id, actor): abort(403)
        try:
            ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).add_activity(contact_id, request.form, actor); flash(translate(g.language, "crm.activity.saved"))
        except ValueError as exc:
            message_keys = {
                "unknown CRM activity type": "crm.activity.error.type",
                "unknown CRM activity direction": "crm.activity.error.direction",
                "subject or note is required": "crm.activity.error.content_required",
            }
            flash(translate(g.language, message_keys.get(str(exc), "crm.activity.error.default")))
        return redirect(url_for("contact_audit.crm_contact", contact_id=contact_id) + "#crm-timeline")

    @bp.post("/documents/contacts/<contact_id>/crm/update-link", endpoint="crm_update_link")
    @login_required
    def crm_update_link(contact_id: str):
        actor = str(g.user["username"]); contacts = ContactStore(current_app.config["DOCUMENT_ROOT"])
        if not contacts.can_manage(contact_id, actor): abort(403)
        token = ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).create_update_token(contact_id, actor); flash(translate(g.language, "contact_update.link").format(url=url_for("contact_audit.crm_public_update", token=token, _external=True))); return redirect(url_for("contact_audit.crm_contact", contact_id=contact_id))

    @bp.route("/contact-update/<token>", methods=("GET", "POST"), endpoint="crm_public_update")
    def crm_public_update(token: str):
        store = ContactCRMStore(current_app.config["DOCUMENT_ROOT"]); row = store.token(token)
        if row is None: abort(404)
        contact = ContactStore(current_app.config["DOCUMENT_ROOT"]).get(row["contact_id"])
        public_values = _external_update_values(contact)
        if request.method == "POST":
            submitted_values = {key: request.form.get(key, "").strip() for key in EXTERNAL_UPDATE_FIELDS}
            changed = {key: value for key, value in submitted_values.items() if value != public_values.get(key, "")}
            try:
                store.submit_proposal(token, changed, request.remote_addr or "")
            except ValueError as exc:
                return render_template("documents/contact_update_public.html", contact=contact, values=submitted_values, submitted=False, error=str(exc)), 400
            return render_template("documents/contact_update_public.html", contact=contact, values=submitted_values, submitted=True, error="")
        return render_template("documents/contact_update_public.html", contact=contact, values=public_values, submitted=False, error="")

    @bp.get("/contact-update/<token>/address-search.json", endpoint="crm_public_address_search")
    def crm_public_address_search(token: str):
        if ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).token(token) is None:
            abort(404)
        query = request.args.get("q", "").strip(); field = request.args.get("field", "").strip()
        index = LocalAddressIndex(current_app.config["DOCUMENT_ROOT"])
        if len(query) < 3:
            return jsonify({"candidates": [], "suggestions": [], "unique": None, "source": "local_osm", "ready": index.status()["ready"], "attribution": "© OpenStreetMap contributors"})
        try:
            candidates = search_address(query, root=current_app.config["DOCUMENT_ROOT"], country_code=request.args.get("country", "de"), limit=8)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return jsonify({"error": "local_address_index_unavailable", "candidates": [], "unique": None, "source": "local_osm", "attribution": "© OpenStreetMap contributors"}), 503
        return jsonify({"candidates": candidates, "suggestions": field_suggestions(candidates, field), "unique": unique_candidate(candidates), "source": "local_osm", "ready": index.status()["ready"], "attribution": "© OpenStreetMap contributors"})

    @bp.get("/documents/contacts/proposals", endpoint="crm_proposals")
    @login_required
    def crm_proposals():
        actor = str(g.user["username"]); contacts = ContactStore(current_app.config["DOCUMENT_ROOT"]); rows = []
        for proposal in ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).proposals():
            if not contacts.can_manage(proposal.get("contact_id", ""), actor):
                continue
            contact = contacts.get(proposal["contact_id"], actor)
            rows.append({**proposal, "contact": contact, "current_values": _external_update_values(contact)})
        return render_template("documents/contact_proposals.html", proposals=rows)

    @bp.post("/documents/contacts/proposals/<proposal_id>", endpoint="crm_resolve_proposal")
    @login_required
    def crm_resolve_proposal(proposal_id: str):
        try: ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).resolve_proposal(proposal_id, request.form.get("action", "reject"), request.form.getlist("fields"), str(g.user["username"])); flash(translate(g.language, "contact_update.resolved"))
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
