"""Local image text recognition shared by object workflows.

Desktop installations prefer RapidOCR backed by ONNX Runtime.  The ML models
run locally and return text blocks with confidence and coordinates.  Tesseract
is retained as a secondary engine when ML OCR is unavailable or recognizes no
text.  Imports are lazy so Android can keep its platform-specific dependency
set while using the same object-care code.
"""
from __future__ import annotations

import atexit
import json
import os
import selectors
import shutil
import subprocess
import threading
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from .document_store_core import ocr_subprocess_environment
from .ocr_preprocessing import OCR_RENDER_EDGE


MAX_OCR_TEXT = 20_000
MAX_OCR_BLOCKS = 250
_RAPID_OCR_ENGINE: Any | None = None
_RAPID_OCR_LOCK = threading.RLock()
_OCR_WORKER: subprocess.Popen | None = None
_OCR_WORKER_LOCK = threading.RLock()
_OCR_WORKER_TIMEOUT = 120
_OCR_WORKER_MAX_LINE = 2 * 1024 * 1024

RAPIDOCR_INSTALL_HINT = (
    "RapidOCR ist nicht installiert. Installation im Projektordner "
    "(Windows PowerShell): .\\.venv\\Scripts\\python.exe -m pip install .[ocr] "
    "(falls pip fehlt, vorher python -m ensurepip)."
)
TESSERACT_INSTALL_HINT = (
    "Tesseract OCR ist nicht installiert. Windows: Tesseract-Setup "
    "installieren (z. B. UB Mannheim) und das bin-Verzeichnis in PATH "
    "aufnehmen, danach den Server neu starten."
)


def ocr_availability() -> dict[str, bool]:
    """Report local OCR backend availability without running inference."""
    from .optional_ocr_runtime import host_available

    available = host_available()
    if not available:
        from .optional_ocr_runtime import managed_python
        available = managed_python() is not None
    return {
        "rapidocr": available,
        "tesseract": shutil.which("tesseract") is not None,
    }


def _stop_ocr_worker() -> None:
    global _OCR_WORKER
    with _OCR_WORKER_LOCK:
        worker, _OCR_WORKER = _OCR_WORKER, None
        if worker is None:
            return
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=2)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=2)
        for stream in (worker.stdin, worker.stdout):
            if stream is not None:
                stream.close()


atexit.register(_stop_ocr_worker)


def _managed_rapidocr(path: Path) -> dict[str, Any] | None:
    """Send local image paths to the cached, isolated OCR runtime process."""
    global _OCR_WORKER
    from .optional_ocr_runtime import _WORKER, managed_python

    python = managed_python()
    if python is None:
        return None
    with _OCR_WORKER_LOCK:
        if _OCR_WORKER is not None and _OCR_WORKER.poll() is not None:
            _stop_ocr_worker()
        if _OCR_WORKER is None:
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "ORT_DISABLE_TELEMETRY": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            try:
                _OCR_WORKER = subprocess.Popen(
                    [str(python), str(_WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                    cwd=str(_WORKER.parent.parent), env=environment, shell=False,
                )
            except OSError:
                _OCR_WORKER = None
                return {"engine": "rapidocr", "status": "failed", "text": "", "characters": 0,
                        "confidence": None, "blocks": [], "error": "RapidOCR-Laufzeit konnte nicht gestartet werden"}
        worker = _OCR_WORKER
        try:
            if worker is None or worker.stdin is None or worker.stdout is None:
                raise OSError("OCR worker pipe unavailable")
            worker.stdin.write(json.dumps({"path": str(path)}, ensure_ascii=False) + "\n")
            worker.stdin.flush()
            with selectors.DefaultSelector() as selector:
                selector.register(worker.stdout, selectors.EVENT_READ)
                if not selector.select(_OCR_WORKER_TIMEOUT):
                    raise TimeoutError("OCR worker timed out")
            line = worker.stdout.readline(_OCR_WORKER_MAX_LINE + 1)
            if len(line) > _OCR_WORKER_MAX_LINE or not line.endswith("\n"):
                raise ValueError("Invalid OCR worker response size")
            payload = json.loads(line)
            if (not isinstance(payload, dict) or payload.get("engine") != "rapidocr"
                    or payload.get("status") not in {"completed", "failed"}
                    or not isinstance(payload.get("text"), str)):
                raise ValueError("Invalid OCR worker response")
            payload["text"] = payload["text"][:MAX_OCR_TEXT]
            payload["characters"] = len(payload["text"])
            return payload
        except (OSError, ValueError, TimeoutError, json.JSONDecodeError):
            _stop_ocr_worker()
            return {"engine": "rapidocr", "status": "failed", "text": "", "characters": 0,
                    "confidence": None, "blocks": [], "error": "RapidOCR-Laufzeit fehlgeschlagen oder Zeitüberschreitung"}


def _line(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _rapidocr_engine() -> Any:
    global _RAPID_OCR_ENGINE
    with _RAPID_OCR_LOCK:
        if _RAPID_OCR_ENGINE is None:
            spec = find_spec("rapidocr")
            if spec is None or not spec.submodule_search_locations:
                raise ModuleNotFoundError(RAPIDOCR_INSTALL_HINT)

            # RapidOCR's defaults can download missing model files during the
            # first inference. Point it at the model files shipped in the
            # pinned package and fail closed if they are absent to prevent a
            # runtime fetch of those model files.
            model_root = Path(next(iter(spec.submodule_search_locations))) / "models"
            model_paths = {
                "Det.model_path": model_root / "PP-OCRv6_det_small.onnx",
                "Cls.model_path": model_root / "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
                "Rec.model_path": model_root / "PP-OCRv6_rec_small.onnx",
            }
            missing_models = [str(path.name) for path in model_paths.values() if not path.is_file()]
            if missing_models:
                raise FileNotFoundError(
                    "RapidOCR model files are missing from the local installation: "
                    + ", ".join(missing_models)
                )

            # Set this before the vendor import, which may initialize ONNX Runtime.
            # The supported 1DS opt-out applies to this process for its lifetime.
            os.environ["ORT_DISABLE_TELEMETRY"] = "1"
            from rapidocr import RapidOCR

            _RAPID_OCR_ENGINE = RapidOCR(
                params={
                    "Global.model_root_dir": str(model_root),
                    "Global.max_side_len": OCR_RENDER_EDGE,
                    "Det.lang_type": "german",
                    "Det.model_path": str(model_paths["Det.model_path"]),
                    "Cls.model_path": str(model_paths["Cls.model_path"]),
                    "Rec.lang_type": "german",
                    "Rec.model_path": str(model_paths["Rec.model_path"]),
                }
            )
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
        "error": _line(exc) or (RAPIDOCR_INSTALL_HINT if unavailable else "RapidOCR konnte nicht initialisiert werden"),
    }


def _run_rapidocr(path: Path) -> dict[str, Any]:
    try:
        engine = _rapidocr_engine()
    except (ImportError, ModuleNotFoundError) as exc:
        managed_result = _managed_rapidocr(path)
        if managed_result is not None:
            return managed_result
        return _rapidocr_failure(exc, unavailable=True)
    except FileNotFoundError as exc:
        managed_result = _managed_rapidocr(path)
        if managed_result is not None:
            return managed_result
        return _rapidocr_failure(exc)
    except Exception as exc:
        # A missing or unusable local model must not bypass the Tesseract
        # fallback and break the whole object-photo workflow.
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


def _run_tesseract(path: Path, *, timeout: float = 90) -> dict[str, Any]:
    executable = shutil.which("tesseract")
    if not executable:
        return {
            "engine": "tesseract",
            "status": "unavailable",
            "text": "",
            "characters": 0,
            "confidence": None,
            "blocks": [],
            "error": TESSERACT_INSTALL_HINT,
        }
    environment = ocr_subprocess_environment()
    command = [executable, str(path), "stdout", "-l", "deu+eng"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, env=environment)
        if result.returncode != 0 and "deu" in result.stderr.casefold():
            command[-1] = "eng"
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, env=environment)
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


def analyze_ocr(path: Path, *, fallback_tesseract: bool = True) -> dict[str, Any]:
    """Run local ML OCR, optionally falling back to Tesseract."""
    ml_result = _run_rapidocr(path)
    if ml_result["status"] == "completed" and str(ml_result.get("text", "")).strip():
        return ml_result
    if not fallback_tesseract:
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
