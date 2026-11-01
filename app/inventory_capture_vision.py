"""OCR and ML adapters for inventory photo capture.

Keep optional inference plumbing separate from the capture workflow. Public
errors intentionally use stable messages so temporary paths and provider
diagnostics never reach the browser.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from flask import current_app, has_app_context


def capture_ocr(png: bytes) -> dict[str, Any]:
    """Run local OCR on optimized bytes; never raises, reports honestly."""
    from .object_vision import analyze_ocr

    try:
        with tempfile.TemporaryDirectory(prefix="capture-ocr-") as directory:
            path = Path(directory) / "capture.png"
            path.write_bytes(png)
            result = analyze_ocr(path)
    except (OSError, ValueError) as exc:
        if has_app_context():
            current_app.logger.info(
                "inventory_capture_ocr_failed exception_type=%s",
                type(exc).__name__,
            )
        return {
            "text": "", "engine": "", "status": "error",
            "characters": 0, "confidence": None,
            "error": "Lokale OCR-Auswertung fehlgeschlagen.",
        }
    return {
        "text": str(result.get("text", "") or "")[:20000],
        "engine": str(result.get("engine", "") or ""),
        "status": str(result.get("status", "") or ""),
        "characters": int(result.get("characters", 0) or 0),
        "confidence": result.get("confidence"),
        "error": (
            "Lokale OCR-Auswertung fehlgeschlagen."
            if result.get("error") else ""
        ),
    }


def capture_ml_label(
    png: bytes,
    isbn: str,
    settings: dict[str, Any] | None = None,
    *,
    consent_meta: bool = False,
    root: object = None,
) -> dict[str, Any]:
    """Ask a real Meta backend for an object label (mock never answers)."""
    from .contextflow.vision import (
        MAX_IMAGE_BYTES,
        VisionError,
        suggest_inventory_object,
        vision_settings_from_mapping,
    )

    base: dict[str, Any] = {
        "available": False, "label": "", "item_type": "", "isbn": "",
        "note": "", "subtitle": "", "authors": [], "tags": [],
        "provider": "", "provider_label": "", "model": "", "error": "",
    }
    if len(png) > MAX_IMAGE_BYTES:
        base["error"] = "Bild für ML-Analyse zu groß."
        return base
    try:
        if settings is None:
            config = current_app.config if has_app_context() else {}
            settings = vision_settings_from_mapping(config, os.environ)
        answer = suggest_inventory_object(
            png, "image/png", {"isbn": isbn}, settings,
            consent_meta is True, root,
        )
    except (VisionError, PermissionError) as exc:
        if has_app_context():
            current_app.logger.info(
                "inventory_capture_ml_failed exception_type=%s",
                type(exc).__name__,
            )
        if (
            isinstance(exc, PermissionError)
            and exc.args == ("Meta inference is limited to a synthetic demo root",)
        ):
            base["error"] = (
                "Meta-Cloud-Analyse ist nur auf einer verifizierten synthetischen Demo-Umgebung verfügbar."
            )
        else:
            base["error"] = "ML-Analyse ist vorübergehend nicht verfügbar."
        return base
    suggestion = answer.get("suggestion", {})
    raw_tags = suggestion.get("tags", [])
    tags = [tag for tag in raw_tags if isinstance(tag, str)] \
        if isinstance(raw_tags, list) else []
    base.update({
        "available": True,
        "label": str(suggestion.get("object_name", "") or ""),
        "item_type": str(suggestion.get("item_type", "") or ""),
        "isbn": str(suggestion.get("isbn", "") or ""),
        "note": str(suggestion.get("note", "") or ""),
        "subtitle": str(suggestion.get("subtitle", "") or ""),
        "authors": suggestion.get("authors", []) if isinstance(
            suggestion.get("authors", []), list) else [],
        "tags": tags,
        "provider": str(answer.get("provider", "") or ""),
        "provider_label": str(answer.get("provider_label", "") or ""),
        "model": str(answer.get("model", "") or ""),
    })
    return base
