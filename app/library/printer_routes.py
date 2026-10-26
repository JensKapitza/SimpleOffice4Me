"""Brother printer discovery, preview and direct label-print routes."""
from __future__ import annotations

import re

from flask import Response, abort, current_app, flash, g, jsonify, redirect, request, url_for

from ..access_control import is_admin
from ..auth import login_required
from .printer import (
    PrinterError,
    cut_feed,
    discover_printers,
    preview_png,
    render_barcode,
    render_image,
    render_text,
)
from .routes import _actor, _label_printer_configuration, _label_render_settings, _send_label_image, _store, bp


@bp.get("/printer/discover")
@login_required
def printer_discover():
    if not is_admin(g.user):
        abort(403)
    return jsonify({"printers": discover_printers()})


def _printer_copy_count(kind: str) -> int:
    if kind != "barcode":
        return 1
    raw = request.form.get("copies", "1").strip()
    if not re.fullmatch(r"(?:[1-9]\d?|100)", raw):
        raise PrinterError("Anzahl muss zwischen 1 und 100 liegen")
    return int(raw)


def _render_requested(kind: str, settings: dict | None = None):
    if settings is None:
        settings = _label_render_settings()
    if kind == "text":
        return render_text(request.form.get("text", ""), settings)
    if kind == "barcode":
        return render_barcode(
            request.form.get("value", ""),
            settings,
            caption=request.form.get("caption", ""),
            show_text=request.form.get("barcode_style", "with_text") == "with_text",
            scan_optimized=True,
        )
    if kind == "image":
        upload = request.files.get("image")
        return render_image(upload.read(12 * 1024 * 1024 + 1) if upload else b"", settings)
    raise PrinterError("Unbekannter Drucktyp")


@bp.post("/printer/preview/<kind>")
@login_required
def printer_preview(kind: str):
    try:
        data = preview_png(_render_requested(kind))
    except (OSError, PrinterError, ValueError):
        return jsonify({"ok": False, "error": "library_request_rejected"}), 400
    return Response(data, mimetype="image/png", headers={"Cache-Control": "no-store"})


@bp.post("/printer/print/<kind>")
@login_required
def printer_print(kind: str):
    library = _store()
    try:
        copies = _printer_copy_count(kind)
    except PrinterError as exc:
        library.record_print(_actor(), kind=kind, status="failed", summary=kind, error="Ungültige Anzahl von Etiketten.")
        flash("Anzahl muss zwischen 1 und 100 liegen.")
        return redirect(url_for("library.index") + "#printer")

    try:
        share, printer, settings = _label_printer_configuration()
    except ValueError as exc:
        library.record_print(_actor(), kind=kind, status="failed", summary=kind, error="Etikettendrucker nicht verfügbar oder durch Richtlinie gesperrt.")
        current_app.logger.warning("Printer selection failed (%s)", type(exc).__name__)
        flash("Etikettendrucker nicht verfügbar oder durch Administrator festgelegt.")
        return redirect(url_for("library.index") + "#printer")
    try:
        image = _render_requested(kind, settings)
    except (PrinterError, ValueError) as exc:
        library.record_print(_actor(), kind=kind, status="failed", summary=kind, error="Etikett konnte nicht erstellt werden.")
        current_app.logger.warning("Label rendering failed (%s)", type(exc).__name__)
        flash("Etikett konnte nicht erstellt werden. Bitte Eingaben prüfen.")
        return redirect(url_for("library.index") + "#printer")

    printed = 0
    try:
        summary = (
            request.form.get("text", "")
            if kind == "text"
            else "Barcode-Etikett"
            if kind == "barcode"
            else str(getattr(request.files.get("image"), "filename", "Bild"))
        )
        for index in range(copies):
            byte_count = _send_label_image(image, share, printer, settings)
            printed += 1
            library.record_print(
                _actor(),
                kind=kind,
                status="printed",
                summary=summary if copies == 1 else f"{summary} (Kopie {index + 1}/{copies})",
                byte_count=byte_count,
            )
        if kind == "barcode" and copies > 1:
            flash(f"{printed} Barcode-Etiketten wurden an {printer['label']} gesendet.")
        else:
            flash(f"Etikett wurde an {printer['label']} gesendet.")
    except (OSError, PrinterError, RuntimeError, ValueError) as exc:
        library.record_print(
            _actor(), kind=kind, status="failed", summary=kind,
            error=f"Druck fehlgeschlagen (bereits gesendet: {printed}/{copies})",
        )
        if printed:
            flash(f"Druck teilweise fehlgeschlagen: {printed} von {copies} Etiketten wurden bereits gesendet. Bitte Verlauf prüfen.")
        else:
            flash("Drucken fehlgeschlagen. Bitte Drucker und Eingaben prüfen.")
    finally:
        close = getattr(image, "close", None)
        if callable(close):
            try:
                close()
            except OSError as exc:
                current_app.logger.warning(
                    "Unable to release label-render image: %s", type(exc).__name__
                )
    return redirect(url_for("library.index") + "#printer")


@bp.post("/printer/cut")
@login_required
def printer_cut():
    library = _store()
    try:
        _share, printer, settings = _label_printer_configuration()
        if printer.get("backend") != "brother":
            raise PrinterError("Vorschub und Schnitt werden vom gewählten Etikettendrucker nicht unterstützt")
        byte_count = cut_feed(settings)
        library.record_print(_actor(), kind="cut", status="printed", summary="Vorschub und Schnitt", byte_count=byte_count)
        flash("Vorschub/Schnitt wurde an den Drucker gesendet.")
    except (OSError, PrinterError, ValueError) as exc:
        library.record_print(_actor(), kind="cut", status="failed", summary="Vorschub und Schnitt", error="Vorschub/Schnitt fehlgeschlagen.")
        current_app.logger.warning("Printer cut failed (%s)", type(exc).__name__)
        flash("Vorschub/Schnitt fehlgeschlagen. Bitte Drucker prüfen.")
    return redirect(url_for("library.index") + "#printer")
