"""Contact photo/QR preview and QR export routes."""
from __future__ import annotations

from .documents_core import *  # noqa: F401,F403
from .contact_capture import MAX_CONTACT_IMAGE_BYTES, analyze_contact_image, parse_qr_payload
from .contact_qr import contact_qr_svg as render_contact_qr_svg

@bp.get("/contacts/<contact_id>/qr.svg")
@login_required
def contact_qr_svg(contact_id: str):
    actor = str(g.user["username"])
    store = _contacts()
    try:
        store.get(contact_id, actor)
    except ValueError:
        abort(404)
    try:
        svg = render_contact_qr_svg(store, contact_id, actor, request.args.getlist("field"))
    except ValueError:
        return Response(
            "QR-Code konnte nicht erstellt werden. Bitte Feldauswahl reduzieren oder prüfen.",
            status=400,
            mimetype="text/plain",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    disposition = "attachment" if request.args.get("download") == "1" else "inline"
    return Response(
        svg,
        mimetype="image/svg+xml",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'{disposition}; filename="contact-{contact_id}-qr.svg"',
        },
    )


@bp.post("/contacts/import/photo-preview")
@login_required
def preview_contact_photo():
    uploaded = request.files.get("contact_photo")
    if uploaded is None or not uploaded.filename:
        return jsonify({"ok": False, "error": "Bitte ein Foto auswählen."}), 400
    try:
        data = uploaded.read(MAX_CONTACT_IMAGE_BYTES + 1)
        preview = analyze_contact_image(data, uploaded.filename)
        return jsonify({"ok": True, **preview})
    except ValueError:
        return jsonify({
            "ok": False,
            "error": "Kontaktfoto konnte nicht verarbeitet werden. Bitte JPEG, PNG oder WebP bis 12 MiB verwenden.",
        }), 400


@bp.post("/contacts/import/qr-preview")
@login_required
def preview_contact_qr():
    try:
        fields = parse_qr_payload(request.form.get("qr_payload", ""))
        return jsonify({"ok": True, "fields": fields})
    except ValueError:
        return jsonify({
            "ok": False,
            "error": "QR-Code enthält keine unterstützten oder gültigen Kontaktdaten.",
        }), 400


