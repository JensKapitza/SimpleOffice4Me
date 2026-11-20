"""HTTP workflow for barcode and QR location-label previews."""
from __future__ import annotations

from flask import Response, jsonify, request

from ..auth import login_required
from .printer import PrinterError, preview_png, render_barcode, render_qr_code
from .routes import _label_render_settings, _store, bp

LABEL_STYLES = {"barcode", "qr"}


@bp.post("/locations/<location_id>/labels.preview")
@login_required
def preview_location_label(location_id: str):
    """Render one location label as PNG without requiring a printer."""
    style = str(request.form.get("label_style", "barcode")).strip()
    if style not in LABEL_STYLES:
        return jsonify({"ok": False, "error": "Ungültige Etikettenart."}), 400
    try:
        location = _store().location(location_id)
    except ValueError:
        return jsonify({"ok": False, "error": "Standort wurde nicht gefunden."}), 404
    try:
        image = (render_qr_code if style == "qr" else render_barcode)(
            str(location["code"]), _label_render_settings(),
            caption=_store().location_path(location_id) or location["name"],
        )
        data = preview_png(image)
    except (OSError, PrinterError, ValueError):
        return jsonify({"ok": False, "error": "Etikettenvorschau konnte nicht erstellt werden."}), 400
    return Response(data, mimetype="image/png", headers={"Cache-Control": "no-store"})
