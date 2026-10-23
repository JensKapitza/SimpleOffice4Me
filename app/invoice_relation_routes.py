"""HTTP handlers for invoice relationship and annotation mutations."""
from __future__ import annotations

import sqlite3

from flask import abort, current_app, flash, redirect, request, url_for

from .auth import login_required
from .invoice_relations import InvoiceRelationStore, invoice_project_ids


def _documents():
    # Resolve the facade only when a request runs, avoiding an import cycle while
    # the business-document blueprint registers these handlers.
    from . import business_documents

    return business_documents


@login_required
def add_invoice_relationship(invoice_id: str):
    documents = _documents()
    root, actor = documents._root(), documents._actor()
    source = documents._managed_invoice(root, invoice_id, actor)
    if source.get("status") in {"draft", "finalizing"}:
        abort(409)
    target_id = request.form.get("related_invoice_id", "").strip()
    target = documents._managed_invoice(root, target_id, actor)
    if target.get("contact_id") != source.get("contact_id"):
        abort(404)
    if target.get("status") in {"draft", "finalizing"}:
        flash("Verknüpfungen sind nur zwischen finalisierten Rechnungen möglich.", "warning")
        return redirect(url_for(".invoice_detail", invoice_id=invoice_id))
    if (
        str(source.get("reference_invoice_id", "")) == target_id
        or str(target.get("reference_invoice_id", "")) == invoice_id
    ):
        flash("Diese Rechnungen sind bereits im Rechnungsdokument aufeinander bezogen.", "warning")
        return redirect(url_for(".invoice_detail", invoice_id=invoice_id))
    relationship_type = request.form.get("relationship_type", "").strip()
    if relationship_type == "project_related" and not (invoice_project_ids(source) & invoice_project_ids(target)):
        flash("Projektbezogene Rechnungen benötigen mindestens ein gemeinsames Projekt.", "warning")
        return redirect(url_for(".invoice_detail", invoice_id=invoice_id))
    try:
        relation = InvoiceRelationStore(root).add_relationship(
            invoice_id, target_id, relationship_type, actor,
        )
    except ValueError as exc:
        messages = {
            "invoice relationship type is invalid": "Bitte einen gültigen Beziehungstyp auswählen.",
            "an invoice cannot be linked to itself": "Eine Rechnung kann nicht mit sich selbst verknüpft werden.",
            "invoice relationship would create a cycle": "Die Verknüpfung würde einen Beziehungskreis erzeugen.",
            "invoice relationship already exists": "Diese Rechnungsverknüpfung besteht bereits.",
            "invoice relationship history limit reached": "Für diese Rechnung sind bereits zu viele Verknüpfungen historisiert.",
        }
        flash(messages.get(str(exc), "Rechnungsverknüpfung konnte nicht gespeichert werden."), "warning")
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("Invoice relationship write failed (%s)", type(exc).__name__)
        flash("Rechnungsverknüpfung konnte nicht sicher gespeichert werden.", "danger")
    else:
        mirrored = documents._mirror_invoice_relation_event(
            root, actor, invoice_id, "invoice_relationship_created",
            {"relation_id": relation["relation_id"], "related_invoice_id": target_id,
             "relationship_type": relationship_type},
        )
        message = "Rechnung wurde verknüpft. Die Rechnung und ihr PDF bleiben unverändert."
        if not mirrored:
            message += " Die separate Beziehungshistorie ist gespeichert; das zentrale Auditprotokoll war nicht verfügbar."
        flash(message, "success" if mirrored else "warning")
    return redirect(url_for(".invoice_detail", invoice_id=invoice_id))


@login_required
def remove_invoice_relationship(invoice_id: str, relation_id: str):
    documents = _documents()
    root, actor = documents._root(), documents._actor()
    documents._managed_invoice(root, invoice_id, actor)
    try:
        service = InvoiceRelationStore(root)
        relation = service.relationship_for(invoice_id, relation_id)
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("Invoice relationship read failed (%s)", type(exc).__name__)
        flash("Rechnungsverknüpfung konnte nicht sicher gelesen werden.", "danger")
        return redirect(url_for(".invoice_detail", invoice_id=invoice_id))
    if relation is None:
        abort(404)
    try:
        removed = service.remove_relationship(invoice_id, relation_id, actor)
    except KeyError:
        abort(404)
    except ValueError:
        abort(400)
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("Invoice relationship removal failed (%s)", type(exc).__name__)
        flash("Rechnungsverknüpfung konnte nicht sicher entfernt werden.", "danger")
        return redirect(url_for(".invoice_detail", invoice_id=invoice_id))
    mirrored = documents._mirror_invoice_relation_event(
        root, actor, invoice_id, "invoice_relationship_removed",
        {"relation_id": relation_id,
         "related_invoice_id": removed["target_invoice_id"] if removed["source_invoice_id"] == invoice_id else removed["source_invoice_id"],
         "relationship_type": removed["relation_type"]},
    )
    message = "Rechnungsverknüpfung wurde entfernt. Die Rechnungsdokumente bleiben unverändert."
    if not mirrored:
        message += " Die separate Beziehungshistorie ist gespeichert; das zentrale Auditprotokoll war nicht verfügbar."
    flash(message, "success" if mirrored else "warning")
    return redirect(url_for(".invoice_detail", invoice_id=invoice_id))


@login_required
def add_invoice_annotation(invoice_id: str):
    documents = _documents()
    root, actor = documents._root(), documents._actor()
    documents._managed_invoice(root, invoice_id, actor)
    try:
        annotation = InvoiceRelationStore(root).add_annotation(
            invoice_id, request.form.get("body", ""), actor,
        )
    except ValueError as exc:
        messages = {
            "an internal annotation is required": "Eine interne Anmerkung eingeben.",
            "annotation is too long or contains unsupported control characters": "Die Anmerkung ist zu lang oder enthält ungültige Steuerzeichen.",
            "invoice annotation limit reached": "Für diese Rechnung sind bereits zu viele interne Anmerkungen vorhanden.",
        }
        flash(messages.get(str(exc), "Interne Anmerkung konnte nicht gespeichert werden."), "warning")
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("Invoice annotation write failed (%s)", type(exc).__name__)
        flash("Interne Anmerkung konnte nicht sicher gespeichert werden.", "danger")
    else:
        mirrored = documents._mirror_invoice_relation_event(
            root, actor, invoice_id, "invoice_annotation_added",
            {"annotation_id": annotation["annotation_id"], "version": annotation["version"]},
        )
        message = "Interne Anmerkung wurde revisionssicher hinzugefügt."
        if not mirrored:
            message += " Die separate Anmerkungshistorie ist gespeichert; das zentrale Auditprotokoll war nicht verfügbar."
        flash(message, "success" if mirrored else "warning")
    return redirect(url_for(".invoice_detail", invoice_id=invoice_id))


@login_required
def revise_invoice_annotation(invoice_id: str, annotation_id: str):
    documents = _documents()
    root, actor = documents._root(), documents._actor()
    documents._managed_invoice(root, invoice_id, actor)
    deleted = request.form.get("action") == "delete"
    try:
        annotation = InvoiceRelationStore(root).revise_annotation(
            invoice_id, annotation_id, request.form.get("body", ""), actor,
            request.form.get("version", ""), deleted=deleted,
        )
    except KeyError:
        abort(404)
    except ValueError as exc:
        messages = {
            "invalid invoice annotation identifier": "Die Anmerkung wurde nicht gefunden.",
            "invoice annotation does not exist": "Die Anmerkung wurde nicht gefunden.",
            "invoice annotation is deleted": "Die Anmerkung wurde bereits entfernt.",
            "invoice annotation changed; reload before editing": "Die Anmerkung wurde zwischenzeitlich geändert. Bitte Seite neu laden.",
            "invoice annotation history limit reached": "Die Änderungshistorie dieser Anmerkung ist ausgeschöpft.",
            "an internal annotation is required": "Eine interne Anmerkung eingeben.",
            "annotation is too long or contains unsupported control characters": "Die Anmerkung ist zu lang oder enthält ungültige Steuerzeichen.",
        }
        flash(messages.get(str(exc), "Interne Anmerkung konnte nicht geändert werden."), "warning")
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("Invoice annotation revision failed (%s)", type(exc).__name__)
        flash("Interne Anmerkung konnte nicht sicher geändert werden.", "danger")
    else:
        mirrored = documents._mirror_invoice_relation_event(
            root, actor, invoice_id,
            "invoice_annotation_deleted" if deleted else "invoice_annotation_revised",
            {"annotation_id": annotation["annotation_id"], "version": annotation["version"]},
        )
        message = "Interne Anmerkung wurde entfernt." if deleted else "Interne Anmerkung wurde versioniert geändert."
        if not mirrored:
            message += " Die separate Anmerkungshistorie ist gespeichert; das zentrale Auditprotokoll war nicht verfügbar."
        flash(message, "success" if mirrored else "warning")
    return redirect(url_for(".invoice_detail", invoice_id=invoice_id))


def register_invoice_relation_routes(blueprint) -> None:
    """Attach the sidecar mutation handlers to the shared business-documents blueprint."""
    blueprint.add_url_rule(
        "/invoices/<invoice_id>/relationships", view_func=add_invoice_relationship,
        methods=("POST",),
    )
    blueprint.add_url_rule(
        "/invoices/<invoice_id>/relationships/<relation_id>/remove",
        view_func=remove_invoice_relationship, methods=("POST",),
    )
    blueprint.add_url_rule(
        "/invoices/<invoice_id>/annotations", view_func=add_invoice_annotation,
        methods=("POST",),
    )
    blueprint.add_url_rule(
        "/invoices/<invoice_id>/annotations/<annotation_id>",
        view_func=revise_invoice_annotation, methods=("POST",),
    )
