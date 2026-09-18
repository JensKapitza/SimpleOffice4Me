"""Local image text recognition shared by object workflows.

Desktop installations prefer RapidOCR backed by ONNX Runtime.  The ML models
run locally and return text blocks with confidence and coordinates.  Tesseract
is retained as a secondary engine when ML OCR is unavailable or recognizes no
text.  Imports are lazy so Android can keep its platform-specific dependency
set while using the same object-care code.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from .document_store_core import ocr_subprocess_environment


MAX_OCR_TEXT = 20_000
MAX_OCR_BLOCKS = 250
_RAPID_OCR_ENGINE: Any | None = None
_RAPID_OCR_LOCK = threading.RLock()


def _line(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _rapidocr_engine() -> Any:
    global _RAPID_OCR_ENGINE
    with _RAPID_OCR_LOCK:
        if _RAPID_OCR_ENGINE is None:
            from rapidocr import RapidOCR

            _RAPID_OCR_ENGINE = RapidOCR()
        return _RAPID_OCR_ENGINE


def _score(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(number, 1.0)), 4)


def _box_points(value: Any) -> list[list[float]]:
    if value is None:
        return []
    points: list[list[float]] = []
    try:
        for point in value:
            if len(point) < 2:
                continue
            points.append([round(float(point[0]), 1), round(float(point[1]), 1)])
    except (TypeError, ValueError, IndexError):
        return []
    return points


def _rapidocr_output(result: Any) -> dict[str, Any]:
    raw_texts = getattr(result, "txts", None)
    raw_scores = getattr(result, "scores", None)
    raw_boxes = getattr(result, "boxes", None)
    texts = list(raw_texts) if raw_texts is not None else []
    scores = list(raw_scores) if raw_scores is not None else []
    boxes = list(raw_boxes) if raw_boxes is not None else []
    blocks: list[dict[str, Any]] = []
    confidence_values: list[float] = []
    clean_texts: list[str] = []

    for index, value in enumerate(texts[:MAX_OCR_BLOCKS]):
        text = str(value or "").strip()
        if not text:
            continue
        confidence = _score(scores[index]) if index < len(scores) else None
        if confidence is not None:
            confidence_values.append(confidence)
        block = {
            "text": text,
            "confidence": confidence,
            "box": _box_points(boxes[index]) if index < len(boxes) else [],
        }
        blocks.append(block)
        clean_texts.append(text)

    text = "\n".join(clean_texts).strip()[:MAX_OCR_TEXT]
    confidence = round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else None
    return {
        "engine": "rapidocr",
        "status": "completed",
        "text": text,
        "characters": len(text),
        "confidence": confidence,
        "blocks": blocks,
    }


def _rapidocr_failure(exc: Exception, *, unavailable: bool = False) -> dict[str, Any]:
    return {
        "engine": "rapidocr",
        "status": "unavailable" if unavailable else "failed",
        "text": "",
        "characters": 0,
        "confidence": None,
        "blocks": [],
        "error": _line(exc) or ("RapidOCR ist nicht installiert" if unavailable else "RapidOCR konnte nicht initialisiert werden"),
    }


def _run_rapidocr(path: Path) -> dict[str, Any]:
    try:
        engine = _rapidocr_engine()
    except (ImportError, ModuleNotFoundError) as exc:
        return _rapidocr_failure(exc, unavailable=True)
    except Exception as exc:
        # RapidOCR may resolve model assets while the engine is initialized.
        # Network, cache or read-only filesystem failures must not bypass the
        # local Tesseract fallback and break the whole object-photo workflow.
        return _rapidocr_failure(exc)
    try:
        with _RAPID_OCR_LOCK:
            result = engine(str(path))
        return _rapidocr_output(result)
    except Exception as exc:
        return {
            "engine": "rapidocr",
            "status": "failed",
            "text": "",
            "characters": 0,
            "confidence": None,
            "blocks": [],
            "error": _line(exc) or "RapidOCR fehlgeschlagen",
        }


def _run_tesseract(path: Path) -> dict[str, Any]:
    executable = shutil.which("tesseract")
    if not executable:
        return {
            "engine": "tesseract",
            "status": "unavailable",
            "text": "",
            "characters": 0,
            "confidence": None,
            "blocks": [],
            "error": "Tesseract OCR ist nicht installiert",
        }
    environment = ocr_subprocess_environment()
    command = [executable, str(path), "stdout", "-l", "deu+eng"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False, env=environment)
        if result.returncode != 0 and "deu" in result.stderr.casefold():
            command[-1] = "eng"
            result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False, env=environment)
    except subprocess.TimeoutExpired:
        return {
            "engine": "tesseract",
            "status": "failed",
            "text": "",
            "characters": 0,
            "confidence": None,
            "blocks": [],
            "error": "OCR-Zeitlimit von 90 Sekunden überschritten",
        }
    if result.returncode != 0:
        return {
            "engine": "tesseract",
            "status": "failed",
            "text": "",
            "characters": 0,
            "confidence": None,
            "blocks": [],
            "error": _line(result.stderr or "Tesseract OCR fehlgeschlagen"),
        }
    text = "\n".join(line.rstrip() for line in result.stdout.splitlines()).strip()[:MAX_OCR_TEXT]
    return {
        "engine": "tesseract",
        "status": "completed",
        "text": text,
        "characters": len(text),
        "confidence": None,
        "blocks": [],
    }


def analyze_ocr(path: Path) -> dict[str, Any]:
    """Run local OCR with ML first and Tesseract only as a fallback."""
    ml_result = _run_rapidocr(path)
    if ml_result["status"] == "completed" and str(ml_result.get("text", "")).strip():
        return ml_result

    fallback = _run_tesseract(path)
    if fallback["status"] == "completed" and str(fallback.get("text", "")).strip():
        fallback["fallback_from"] = "rapidocr"
        fallback["fallback_reason"] = (
            str(ml_result.get("error", ""))
            if ml_result["status"] != "completed"
            else "ML-OCR erkannte keinen Text"
        )
        return fallback

    if ml_result["status"] == "completed":
        ml_result["fallback_engine"] = "tesseract"
        ml_result["fallback_status"] = fallback["status"]
        if fallback.get("error"):
            ml_result["fallback_error"] = fallback["error"]
        return ml_result

    errors = [
        f"RapidOCR: {ml_result.get('error', ml_result['status'])}",
        f"Tesseract: {fallback.get('error', fallback['status'])}",
    ]
    return {
        "engine": "rapidocr",
        "status": "failed" if "failed" in {ml_result["status"], fallback["status"]} else "unavailable",
        "text": "",
        "characters": 0,
        "confidence": None,
        "blocks": [],
        "error": "; ".join(errors),
        "fallback_engine": "tesseract",
        "fallback_status": fallback["status"],
    }
