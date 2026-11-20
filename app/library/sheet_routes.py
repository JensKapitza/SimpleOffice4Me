"""HTTP workflow for printable location label sheets (media or A4)."""
from __future__ import annotations

import io

from flask import jsonify, request, send_file

from ..auth import login_required
from .label_pdf import book_labels_pdf
from .printer import PrinterError
from .routes import _label_render_settings, _store, bp

SHEET_STYLES = {"with_text", "barcode_only", "qr_with_text", "qr_only"}
SHEET_LAYOUTS = {"media", "a4_3x7"}
MAX_SHEET_LABELS = 200


@bp.post("/locations/labels.pdf")
@login_required
def export_location_labels_pdf():
    """Download selected location labels as PDF, optionally an A4 sheet."""
    identifiers = request.form.getlist("location_id")
    style = request.form.get("barcode_style", "with_text")
    layout = request.form.get("label_layout", "media")
    if style not in SHEET_STYLES:
        return jsonify({"ok": False, "error": "Bitte eine gültige Etikettenart auswählen."}), 400
    if layout not in SHEET_LAYOUTS:
        return jsonify({"ok": False, "error": "Bitte ein gültiges Etikettenlayout auswählen."}), 400
    if not 1 <= len(identifiers) <= MAX_SHEET_LABELS:
        return jsonify({"ok": False, "error": "Bitte 1 bis 200 Standorte auswählen."}), 400
    library = _store()
    labels = []
    for identifier in dict.fromkeys(identifiers):
        try:
            location = library.location(identifier)
        except ValueError:
            return jsonify({"ok": False, "error": "Unbekannter Standort in der Auswahl."}), 400
        path = library.location_path(location["location_id"]) or location["name"]
        labels.append((str(location["code"]), path))
    try:
        payload = book_labels_pdf(labels, _label_render_settings(), style=style, layout=layout)
    except (OSError, PrinterError, ValueError):
        return jsonify({"ok": False, "error": "PDF-Etiketten konnten mit der gewählten Größe nicht erstellt werden."}), 400
    return send_file(
        io.BytesIO(payload),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="standort-etiketten.pdf",
        max_age=0,
    )
