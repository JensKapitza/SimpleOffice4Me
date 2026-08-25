"""CRM contact extensions and rich EML document preview helpers."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from flask import abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .auth import login_required
from .contact_store import ContactStore
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .mail_reader import _header, _message_text


CRM_FILE = "contact-crm.json"


class ContactCRMStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / CONTROL_DIR / CRM_FILE

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
        data = self._read()
        return dict(data["records"].get(contact_id, {}))

    def save(self, contact_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
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
            "updated_at": utc_now(),
            "updated_by": actor,
        }
        data["records"][contact_id] = record
        self._write(data)
        DocumentStore(self.root).history.record(
            "contact_crm_updated", actor, "contact-crm", contact_id,
            {"before": old, "after": record},
        )
        return record

    def create_update_token(self, contact_id: str, actor: str) -> str:
        data = self._read()
        token = secrets.token_urlsafe(32)
        data["tokens"].append({
            "token": token, "contact_id": contact_id, "created_at": utc_now(),
            "created_by": actor, "used": False,
        })
        self._write(data)
        return token

    def token(self, token: str) -> dict[str, Any] | None:
        return next((item for item in self._read()["tokens"] if item.get("token") == token and not item.get("used")), None)

    def submit_proposal(self, token: str, values: dict[str, str], remote: str) -> str:
        data = self._read()
        token_row = next((item for item in data["tokens"] if item.get("token") == token and not item.get("used")), None)
        if token_row is None:
            raise ValueError("update link is invalid or already used")
        proposal_id = uuid.uuid4().hex
        proposal = {
            "proposal_id": proposal_id,
            "contact_id": token_row["contact_id"],
            "values": {key: str(value).strip() for key, value in values.items() if str(value).strip()},
            "submitted_at": utc_now(), "remote": remote[:120], "status": "pending",
        }
        data["proposals"].append(proposal)
        token_row["used"] = True
        self._write(data)
        return proposal_id

    def proposals(self) -> list[dict[str, Any]]:
        return sorted(self._read()["proposals"], key=lambda item: item.get("submitted_at", ""), reverse=True)

    def resolve_proposal(self, proposal_id: str, action: str, accepted_fields: list[str], actor: str) -> dict[str, Any]:
        data = self._read()
        proposal = next((item for item in data["proposals"] if item.get("proposal_id") == proposal_id), None)
        if proposal is None or proposal.get("status") != "pending":
            raise ValueError("proposal is unavailable")
        if action == "reject":
            proposal["status"] = "rejected"
        elif action == "accept":
            store = ContactStore(self.root)
            contact = store.get(proposal["contact_id"], actor)
            merged = dict(contact.get("fields", {}))
            for key in accepted_fields:
                if key in proposal.get("values", {}):
                    merged[key] = proposal["values"][key]
            store.upsert({**merged, **{f"custom_{key}": value for key, value in merged.items() if key not in store.schema().get("aliases", {})}}, actor, proposal["contact_id"])
            proposal["status"] = "accepted"
            proposal["accepted_fields"] = sorted(set(accepted_fields))
        else:
            raise ValueError("unknown proposal action")
        proposal["resolved_at"] = utc_now(); proposal["resolved_by"] = actor
        self._write(data)
        DocumentStore(self.root).history.record(
            "contact_external_update_resolved", actor, "contact-crm", proposal["contact_id"], proposal,
        )
        return proposal


def _parse_rows(text: str, columns: int) -> list[list[str]]:
    result: list[list[str]] = []
    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        parts += [""] * max(0, columns - len(parts))
        result.append(parts[:columns])
    return result


def _eml_preview(root: Path, document_id: str) -> dict[str, Any]:
    store = DocumentStore(root)
    document = store.get_document(document_id)
    path = root / str(document.get("last_path", ""))
    if path.suffix.casefold() != ".eml" or not path.is_file() or path.is_symlink():
        raise ValueError("document is not a regular EML file")
    raw = path.read_bytes()
    message = BytesParser(policy=policy.default).parsebytes(raw)
    attachments: list[dict[str, Any]] = []
    for index, part in enumerate(message.walk()):
        if part.get_content_disposition() != "attachment" and not part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append({
            "part": index,
            "name": _header(part.get_filename()) or f"Anhang-{index}",
            "type": part.get_content_type(),
            "size": len(payload),
        })
    return {
        "subject": _header(message.get("Subject")) or "(ohne Betreff)",
        "from": _header(message.get("From")), "to": _header(message.get("To")),
        "cc": _header(message.get("Cc")), "date": _header(message.get("Date")),
        "message_id": _header(message.get("Message-ID")), "text": _message_text(message),
        "attachments": attachments,
    }


def register(bp) -> None:
    @bp.route("/documents/contacts/<contact_id>/crm", methods=("GET", "POST"), endpoint="crm_contact")
    @login_required
    def crm_contact(contact_id: str):
        actor = str(g.user["username"])
        contacts = ContactStore(current_app.config["DOCUMENT_ROOT"])
        contact = contacts.get(contact_id, actor)
        if not contacts.can_manage(contact_id, actor):
            abort(403)
        store = ContactCRMStore(current_app.config["DOCUMENT_ROOT"])
        if request.method == "POST":
            values = {
                "roles": request.form.getlist("roles"), "status": request.form.get("status", "active"),
                "customer_number": request.form.get("customer_number", ""), "supplier_number": request.form.get("supplier_number", ""),
                "discount": request.form.get("discount", ""), "payment_terms": request.form.get("payment_terms", ""),
                "payment_days": request.form.get("payment_days", ""), "currency": request.form.get("currency", "EUR"),
                "tax_number": request.form.get("tax_number", ""), "vat_id": request.form.get("vat_id", ""),
                "notes": request.form.get("notes", ""),
                "addresses": [dict(zip(("type", "street", "postal", "city", "country"), row)) for row in _parse_rows(request.form.get("addresses", ""), 5)],
                "communications": [dict(zip(("type", "value", "preferred"), row)) for row in _parse_rows(request.form.get("communications", ""), 3)],
                "bank_accounts": [dict(zip(("holder", "iban", "bic", "bank"), row)) for row in _parse_rows(request.form.get("bank_accounts", ""), 4)],
                "relations": [dict(zip(("type", "contact_id"), row)) for row in _parse_rows(request.form.get("relations", ""), 2)],
            }
            store.save(contact_id, values, actor)
            flash("CRM-Daten gespeichert. CardDAV-Änderungen können diese Daten nicht löschen.")
            return redirect(url_for("contact_audit.crm_contact", contact_id=contact_id))
        return render_template("documents/contact_crm.html", contact=contact, crm=store.record(contact_id), all_contacts=contacts.contacts(actor))

    @bp.post("/documents/contacts/<contact_id>/crm/update-link", endpoint="crm_update_link")
    @login_required
    def crm_update_link(contact_id: str):
        actor = str(g.user["username"])
        contacts = ContactStore(current_app.config["DOCUMENT_ROOT"])
        if not contacts.can_manage(contact_id, actor):
            abort(403)
        token = ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).create_update_token(contact_id, actor)
        flash("Externer Aktualisierungslink: " + url_for("contact_audit.crm_public_update", token=token, _external=True))
        return redirect(url_for("contact_audit.crm_contact", contact_id=contact_id))

    @bp.route("/contact-update/<token>", methods=("GET", "POST"), endpoint="crm_public_update")
    def crm_public_update(token: str):
        store = ContactCRMStore(current_app.config["DOCUMENT_ROOT"])
        row = store.token(token)
        if row is None:
            abort(404)
        contact = ContactStore(current_app.config["DOCUMENT_ROOT"]).get(row["contact_id"])
        if request.method == "POST":
            store.submit_proposal(token, {
                "display_name": request.form.get("display_name", ""), "first_name": request.form.get("first_name", ""),
                "last_name": request.form.get("last_name", ""), "email": request.form.get("email", ""),
                "phone": request.form.get("phone", ""), "company": request.form.get("company", ""),
                "note": request.form.get("note", ""),
            }, request.remote_addr or "")
            return render_template("documents/contact_update_public.html", contact=contact, submitted=True)
        return render_template("documents/contact_update_public.html", contact=contact, submitted=False)

    @bp.get("/documents/contacts/proposals", endpoint="crm_proposals")
    @login_required
    def crm_proposals():
        return render_template("documents/contact_proposals.html", proposals=ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).proposals())

    @bp.post("/documents/contacts/proposals/<proposal_id>", endpoint="crm_resolve_proposal")
    @login_required
    def crm_resolve_proposal(proposal_id: str):
        try:
            ContactCRMStore(current_app.config["DOCUMENT_ROOT"]).resolve_proposal(
                proposal_id, request.form.get("action", "reject"), request.form.getlist("fields"), str(g.user["username"]),
            )
            flash("Externe Kontaktänderung verarbeitet.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("contact_audit.crm_proposals"))

    @bp.get("/documents/<document_id>/eml-preview", endpoint="eml_preview")
    @login_required
    def eml_preview(document_id: str):
        try:
            preview = _eml_preview(Path(current_app.config["DOCUMENT_ROOT"]), document_id)
        except (OSError, ValueError):
            abort(404)
        return render_template("documents/eml_document_preview.html", preview=preview, document_id=document_id)

    @bp.get("/documents/<document_id>/eml-metadata.json", endpoint="eml_metadata")
    @login_required
    def eml_metadata(document_id: str):
        try:
            preview = _eml_preview(Path(current_app.config["DOCUMENT_ROOT"]), document_id)
        except (OSError, ValueError):
            abort(404)
        return jsonify({key: preview[key] for key in ("subject", "from", "to", "cc", "date", "message_id")})
