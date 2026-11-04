"""OCR and ML adapters for inventory photo capture.

Keep optional inference plumbing separate from the capture workflow. Public
errors intentionally use stable messages so temporary paths and provider
diagnostics never reach the browser.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from flask import current_app, has_app_context


_OCR_UPSCALE_MAX_DIM = 900
_OCR_UPSCALE_FACTOR = 2


def upscale_small_capture(png: bytes) -> bytes:
    """Double very small capture images for OCR; fail soft to input.

    Measured on a real 306x466 book cover: 2x LANCZOS raised RapidOCR
    confidence 0.9543 -> 0.9853 and recovered a garbled small-print line,
    while the title stayed identical. Only images below
    ``_OCR_UPSCALE_MAX_DIM`` are touched, so regular phone photos keep
    their pixels and OCR latency; any decoding problem returns the input.
    """
    try:
        from PIL import Image
    except ImportError:
        return png
    try:
        import io as _io

        with Image.open(_io.BytesIO(png)) as frame:
            width, height = frame.size
            if max(width, height) >= _OCR_UPSCALE_MAX_DIM:
                return png
            if min(width, height) <= 0:
                return png
            upscaled = frame.convert("RGB").resize(
                (width * _OCR_UPSCALE_FACTOR,
                 height * _OCR_UPSCALE_FACTOR),
                Image.LANCZOS,
            )
        buffer = _io.BytesIO()
        upscaled.save(buffer, format="PNG")
        return buffer.getvalue()
    except (OSError, ValueError):
        return png


def capture_ocr(png: bytes) -> dict[str, Any]:
    """Run local OCR on optimized bytes; never raises, reports honestly."""
    from .object_vision import analyze_ocr

    try:
        with tempfile.TemporaryDirectory(prefix="capture-ocr-") as directory:
            path = Path(directory) / "capture.png"
            path.write_bytes(upscale_small_capture(png))
            result = analyze_ocr(path)
    except (OSError, ValueError) as exc:
        if has_app_context():
            current_app.logger.info(
                "inventory_capture_ocr_failed exception_type=%s",
                type(exc).__name__,
            )
        return {
            "text": "", "engine": "", "status": "error",
            "characters": 0, "confidence": None, "blocks": [],
            "error": "Lokale OCR-Auswertung fehlgeschlagen.",
        }
    blocks: list[dict[str, Any]] = []
    for block in result.get("blocks", []) or []:
        if not isinstance(block, dict) or len(blocks) >= 50:
            continue
        box = block.get("box") or []
        points: list[list[float]] = []
        for point in box[:8]:
            try:
                points.append([float(point[0]), float(point[1])])
            except (TypeError, ValueError, IndexError):
                continue
        try:
            confidence = float(block.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        blocks.append({
            "text": str(block.get("text", "") or "")[:200],
            "confidence": confidence,
            "box": points,
        })
    return {
        "text": str(result.get("text", "") or "")[:20000],
        "engine": str(result.get("engine", "") or ""),
        "status": str(result.get("status", "") or ""),
        "characters": int(result.get("characters", 0) or 0),
        "confidence": result.get("confidence"),
        "blocks": blocks,
        "error": (
            "Lokale OCR-Auswertung fehlgeschlagen."
            if result.get("error") else ""
        ),
    }


_ML_ERROR_TEXT_DE = {
    "too_large": "Bild für ML-Analyse zu groß.",
    "disabled": "ML-Analyse ist deaktiviert (Mini-Services → Vision/ML).",
    "no_demo_root": "Meta-Cloud-Analyse ist nur auf einer verifizierten synthetischen Demo-Umgebung verfügbar.",
    "not_configured": "Meta-Analyse ist nicht konfiguriert. Mini-Services → Vision/ML: Anbieter wählen, Key eintragen und Einwilligung setzen.",
    "no_consent": "Meta-Analyse braucht die Einwilligung: Mini-Services → Vision/ML und pro Foto zustimmen.",
    "no_key": "Meta-Analyse braucht einen Model-API-Key: Mini-Services → Vision/ML.",
    "unavailable": "ML-Analyse ist vorübergehend nicht verfügbar.",
}


def _ml_error_code(exc: BaseException) -> str:
    """Map backend failures to stable ML error codes (no details leak)."""
    if isinstance(exc, PermissionError):
        detail = exc.args[0] if len(exc.args) == 1 and isinstance(exc.args[0], str) else ""
        if detail == "Meta inference is limited to a synthetic demo root":
            return "no_demo_root"
        if "explicit consent" in detail:
            return "no_consent"
        if "Model API key" in detail:
            return "no_key"
        if "not configured" in detail:
            return "not_configured"
    return "unavailable"


def capture_ml_label(
    png: bytes,
    isbn: str,
    settings: dict[str, Any] | None = None,
    *,
    consent_meta: bool = False,
    root: object = None,
) -> dict[str, Any]:
    """Ask a real ML backend for an object label (mock never answers).

    Failures carry a stable ``error_code`` (``too_large``, ``disabled``,
    ``no_demo_root``, ``not_configured``, ``no_consent``, ``no_key``,
    ``unavailable``) plus a German default text; request routes localize
    the code via ``translate()`` before answering the browser.
    """
    from .contextflow.vision import (
        MAX_IMAGE_BYTES,
        VisionError,
        suggest_inventory_object,
    )
    from .vision_config import effective_settings

    base: dict[str, Any] = {
        "available": False, "label": "", "item_type": "", "isbn": "",
        "note": "", "subtitle": "", "authors": [], "tags": [],
        "provider": "", "provider_label": "", "model": "", "error": "",
        "error_code": "",
    }
    if len(png) > MAX_IMAGE_BYTES:
        base["error"] = "Bild für ML-Analyse zu groß."
        base["error_code"] = "too_large"
        return base
    try:
        if settings is None:
            config = current_app.config if has_app_context() else {}
            settings = effective_settings(config)
        if settings.get("enabled") is False:
            base["error"] = "ML-Analyse ist deaktiviert (Mini-Services → Vision/ML)."
            base["error_code"] = "disabled"
            return base
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
        base["error_code"] = _ml_error_code(exc)
        base["error"] = _ML_ERROR_TEXT_DE[base["error_code"]]
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
