"""Readiness and bounded admin-triggered jobs for optional local capabilities."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import importlib.util
import logging
import os
import platform
from pathlib import Path
import shutil
import sys
import threading
import time
from uuid import uuid4

from . import (
    english_voice_assets, kids_hand_assets, kids_piper_assets, optional_hand_game_install, optional_piper_runtime,
    optional_ffmpeg_runtime, optional_ocr_runtime,
)
from .document_store_core import CONTROL_DIR, atomic_json_write
from .component_runtime import managed_command, resolve_command
from .file_lock import exclusive_file_lock
from .media_settings import PIPER_READER_THORSTEN
from .settings_store import SettingsStore

VOICE_PACKAGE = "voice-package"
ENGLISH_VOICE_PACKAGE = "english-voice"
PIPER_ENGINE = "piper-engine"
FFMPEG_RUNTIME = "ffmpeg-runtime"
OCR_RUNTIME = "ocr-runtime"
FEATURES = frozenset((VOICE_PACKAGE, "kids-hand", ENGLISH_VOICE_PACKAGE, PIPER_ENGINE, FFMPEG_RUNTIME,
                      OCR_RUNTIME))
_ACTIVE = frozenset(("queued", "downloading"))
_IN_PROGRESS = _ACTIVE | {"cancelling"}
# Piper setup allows up to 300 s of wheel downloads plus venv/pip smoke checks.
# Keep its queued/running lease longer so a slow but valid install isn't reopened.
_JOB_LIFETIME = 1800
_LOG = logging.getLogger(__name__)
_WORKERS = ThreadPoolExecutor(max_workers=1, thread_name_prefix="optional-model-setup")
_ADMISSION = threading.BoundedSemaphore(3)
_JOB_STATES = _ACTIVE | {"cancelling", "cancelled", "ready", "failed"}
_CANCEL_EVENTS: dict[str, threading.Event] = {}
_CANCEL_GUARD = threading.Lock()
_PROGRESS_WRITES: dict[str, float] = {}
_PROGRESS_GUARD = threading.Lock()
_PROGRESS_INTERVAL = 0.5
_VOICE_INSTALL_SPACE = 128 * 1024 * 1024
_ENGLISH_VOICE_INSTALL_SPACE = 160 * 1024 * 1024
_HAND_INSTALL_SPACE = 64 * 1024 * 1024
_PIPER_ENGINE_INSTALL_SPACE = 300 * 1024 * 1024
_FFMPEG_INSTALL_SPACE = optional_ffmpeg_runtime.REQUIRED_SPACE
_OCR_INSTALL_SPACE = optional_ocr_runtime.artifact()["required_bytes"]


class _PersistentCancelEvent:
    """Thread event plus a file-backed signal, so another WSGI worker can cancel."""

    def __init__(self, root: Path, feature: str, job_id: str, local: threading.Event):
        self.root = root
        self.feature = feature
        self.job_id = job_id
        self.local = local

    def is_set(self) -> bool:
        if self.local.is_set():
            return True
        row = _read(self.root).get(self.feature)
        return (isinstance(row, dict) and row.get("id") == self.job_id
                and row.get("state") == "cancelling")


def hand_override(config) -> str:
    return str(config.get("KIDS_HAND_GAME_ASSET_DIR")
               or os.environ.get("SIMPLEOFFICE_KIDS_HAND_GAME_ASSET_DIR", "")).strip()


def bundled_android_runtime() -> bool:
    """Match the environment set by the APK bootstrap, not a client user-agent."""
    return os.environ.get("SIMPLEOFFICE_ANDROID") == "1"


def _job_file(root: Path) -> Path:
    return root / CONTROL_DIR / "optional-setup-jobs.json"


def _job_lock(root: Path) -> Path:
    return root / CONTROL_DIR / ".optional-setup-jobs.lock"


def _read(root: Path) -> dict:
    try:
        payload = json.loads(_job_file(root).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _job_state(row) -> str:
    if not isinstance(row, dict):
        return ""
    state = row.get("state")
    if not isinstance(state, str) or state not in _JOB_STATES:
        return ""
    started = row.get("started")
    if state in _IN_PROGRESS and (type(started) not in {int, float}
                             or not 0 <= time.time() - started <= _JOB_LIFETIME):
        return "failed"
    return state


def progress_states(root: str | Path) -> dict[str, str]:
    data = _read(Path(root).resolve())
    return {feature: _job_state(data.get(feature)) for feature in FEATURES}


def progress_details(root: str | Path) -> dict[str, dict[str, object]]:
    """Return small, private progress records without job IDs or filesystem paths."""
    data = _read(Path(root).resolve())
    details = {}
    for feature in FEATURES:
        row = data.get(feature)
        if not isinstance(row, dict):
            continue
        progress = row.get("progress")
        if not isinstance(progress, dict):
            progress = {}
        received = progress.get("received", 0)
        total = progress.get("total", 0)
        if type(received) is not int or received < 0:
            received = 0
        if type(total) is not int or total < 0:
            total = 0
        error = row.get("error")
        if not isinstance(error, str) or error not in {"install-failed", "setup-error"}:
            error = ""
        phase = row.get("progress", {}).get("phase") if isinstance(row.get("progress"), dict) else ""
        if phase not in {"download", "prepare", "install", "check"}:
            phase = "download"
        details[feature] = {
            "state": _job_state(row),
            "file": Path(str(progress.get("file", ""))).name[:80],
            "received": received,
            "total": total,
            "percent": min(100, received * 100 // total) if total else None,
            "error": error,
            "phase": phase,
        }
    return details


def _transition(root: Path, feature: str, job_id: str, state: str, *, error: str = "") -> bool:
    with exclusive_file_lock(_job_lock(root)):
        data = _read(root)
        row = data.get(feature)
        if not isinstance(row, dict) or row.get("id") != job_id:
            return False
        if state == "downloading" and _job_state(row) != "queued":
            return False
        row["state"] = state
        if error:
            row["error"] = error if error in {"install-failed", "setup-error"} else "setup-error"
        atomic_json_write(_job_file(root), data)
        return True


def _record_progress(root: Path, feature: str, job_id: str, filename: str,
                     received: int, total: int, *, phase: str = "download") -> None:
    now = time.monotonic()
    with _PROGRESS_GUARD:
        if (phase == "download" and now - _PROGRESS_WRITES.get(job_id, 0.0) < _PROGRESS_INTERVAL
                and (total <= 0 or received < total)):
            return
        _PROGRESS_WRITES[job_id] = now
    try:
        received = max(0, int(received))
        total = max(0, int(total))
    except (TypeError, ValueError, OverflowError):
        return
    with exclusive_file_lock(_job_lock(root)):
        data = _read(root)
        row = data.get(feature)
        if not isinstance(row, dict) or row.get("id") != job_id or _job_state(row) not in _IN_PROGRESS:
            return
        row["progress"] = {
            "file": Path(str(filename)).name[:80],
            "received": received,
            "total": total,
            "phase": phase if phase in {"download", "prepare", "install", "check"} else "download",
        }
        atomic_json_write(_job_file(root), data)


def cancel(root: str | Path, feature: str) -> str:
    """Request cancellation for one allowlisted managed asset download."""
    if feature not in FEATURES:
        raise ValueError("unknown optional capability")
    root = Path(root).resolve()
    event = None
    job_id = ""
    with exclusive_file_lock(_job_lock(root)):
        data = _read(root)
        row = data.get(feature)
        state = _job_state(row)
        if state not in _ACTIVE and state != "cancelling":
            return state or "missing"
        job_id = str(row.get("id") or "") if isinstance(row, dict) else ""
        row["state"] = "cancelling"
        atomic_json_write(_job_file(root), data)
        with _CANCEL_GUARD:
            event = _CANCEL_EVENTS.get(job_id)
    if event is not None:
        event.set()
    return "cancelling"


def _execute(root: Path, feature: str, job_id: str) -> None:
    with _CANCEL_GUARD:
        local_event = _CANCEL_EVENTS.get(job_id)
    cancel_event = _PersistentCancelEvent(root, feature, job_id, local_event or threading.Event())
    try:
        if cancel_event is not None and cancel_event.is_set():
            _transition(root, feature, job_id, "cancelled")
            return
        if not _transition(root, feature, job_id, "downloading"):
            return
        progress = lambda name, received, total, phase="download": _record_progress(  # noqa: E731
            root, feature, job_id, name, received, total,
            phase=phase,
        )
        if feature == VOICE_PACKAGE:
            result = kids_piper_assets.install_package(root, progress=progress, cancel_event=cancel_event)
        elif feature == "kids-hand":
            result = optional_hand_game_install.install_managed(
                root, progress=progress, cancel_event=cancel_event,
            )
        elif feature == ENGLISH_VOICE_PACKAGE:
            result = english_voice_assets.install(root, progress=progress, cancel_event=cancel_event)
        elif feature == PIPER_ENGINE:
            result = optional_piper_runtime.install(root, progress=progress, cancel_event=cancel_event)
        elif feature == FFMPEG_RUNTIME:
            result = optional_ffmpeg_runtime.install(root, progress=progress, cancel_event=cancel_event)
        elif feature == OCR_RUNTIME:
            result = optional_ocr_runtime.install(root, progress=progress, cancel_event=cancel_event)
        else:
            result = "failed"
        state = "cancelled" if result == "cancelled" or cancel_event.is_set() else (
            "ready" if result == "ready" else "failed"
        )
        _transition(root, feature, job_id, state,
                    error="install-failed" if state == "failed" else "")
    except Exception as exc:
        try:
            failed = not cancel_event.is_set()
            _transition(root, feature, job_id,
                        "cancelled" if not failed else "failed",
                        error="setup-error" if failed else "")
        except (OSError, ValueError):
            pass  # If state cannot be written, the capped lease reports failed after a restart.
        _LOG.warning("Optional local setup worker failed (%s)", type(exc).__name__)
    finally:
        with _CANCEL_GUARD:
            _CANCEL_EVENTS.pop(job_id, None)
        with _PROGRESS_GUARD:
            _PROGRESS_WRITES.pop(job_id, None)
        _ADMISSION.release()


def queue(root: str | Path, feature: str, *, manual_hand_directory: str = "") -> str:
    if feature not in FEATURES:
        raise ValueError("unknown optional capability")
    root = Path(root).resolve()
    if feature == "kids-hand" and (manual_hand_directory or not optional_hand_game_install.install_supported()):
        return "manual"
    if feature == PIPER_ENGINE and not optional_piper_runtime.install_supported():
        return "unsupported"
    if feature == FFMPEG_RUNTIME and not optional_ffmpeg_runtime.install_supported():
        return "unsupported"
    if feature == OCR_RUNTIME and optional_ocr_runtime.host_available():
        return "ready"
    if feature == OCR_RUNTIME and not optional_ocr_runtime.install_supported():
        return "unsupported"
    with exclusive_file_lock(_job_lock(root)):
        data = _read(root)
        state = _job_state(data.get(feature))
        if state in _ACTIVE:
            return state
        if ((feature == VOICE_PACKAGE and kids_piper_assets.installed_model(root))
                or (feature == "kids-hand" and kids_hand_assets.ready(
                    optional_hand_game_install.asset_directory(root, "")))
                or (feature == ENGLISH_VOICE_PACKAGE and english_voice_assets.installed_model(root))
                or (feature == PIPER_ENGINE and resolve_command("piper", root))
                or (feature == FFMPEG_RUNTIME and all(resolve_command(name, root)
                                                       for name in ("ffmpeg", "ffprobe", "ffplay")))
                or (feature == OCR_RUNTIME and optional_ocr_runtime.ready(root))):
            return "ready"
        if feature in {VOICE_PACKAGE, ENGLISH_VOICE_PACKAGE} and bundled_android_runtime() \
                and not resolve_command("piper", root):
            return "unsupported"
        required = {
            VOICE_PACKAGE: _VOICE_INSTALL_SPACE,
            "kids-hand": _HAND_INSTALL_SPACE,
            ENGLISH_VOICE_PACKAGE: _ENGLISH_VOICE_INSTALL_SPACE,
            PIPER_ENGINE: _PIPER_ENGINE_INSTALL_SPACE,
            FFMPEG_RUNTIME: _FFMPEG_INSTALL_SPACE,
            OCR_RUNTIME: _OCR_INSTALL_SPACE,
        }[feature]
        try:
            if shutil.disk_usage(root).free < required:
                return "insufficient-space"
        except OSError:
            return "insufficient-space"
        if not _ADMISSION.acquire(blocking=False):
            return "busy"
        job_id = uuid4().hex
        local_event = threading.Event()
        try:
            data[feature] = {"id": job_id, "started": time.time(), "state": "queued",
                             "progress": {"file": "", "received": 0, "total": 0}}
            atomic_json_write(_job_file(root), data)
            with _CANCEL_GUARD:
                _CANCEL_EVENTS[job_id] = local_event
            _WORKERS.submit(_execute, root, feature, job_id)
        except (OSError, RuntimeError, ValueError):
            with _CANCEL_GUARD:
                _CANCEL_EVENTS.pop(job_id, None)
            _ADMISSION.release()
            if _job_state(data.get(feature)) == "queued":
                data[feature]["state"] = "failed"
                atomic_json_write(_job_file(root), data)
            return "failed"
    return "queued"


def _voice_file_status(value: object) -> str:
    configured = str(value or "").strip()
    if not configured:
        return "missing"
    if "://" in configured or configured.startswith(("//", "\\")):
        return "invalid"
    try:
        path = Path(configured).expanduser()
        if path.suffix.casefold() != ".onnx" or not path.is_file():
            return "invalid"
        if not Path(str(path.resolve()) + ".json").is_file():
            return "config-missing"
    except (OSError, ValueError, RuntimeError):
        return "invalid"
    return "configured"


def reader_choice_available(root: str | Path) -> bool:
    """The optional Reader choice never downloads, launches, or repairs an engine."""
    return bool(resolve_command("piper", root) and kids_piper_assets.installed_model(root))


def readiness(root: str | Path, *, manual_hand_directory: str = "") -> dict[str, str | bool]:
    """Read configuration/verified manifests only; no subprocesses or network calls."""
    root = Path(root).resolve()
    available = bool(resolve_command("piper", root))
    media = SettingsStore(root).settings()["media"]
    voice = _voice_file_status(media.get("piper_model"))
    english = _voice_file_status(media.get("piper_model_en"))
    reader_selected = media.get("piper_reader_choice") == PIPER_READER_THORSTEN
    if english == "missing" and voice == "configured":
        english = "shared-fallback" if reader_selected else "fallback"
    installed = bool(kids_piper_assets.installed_model(root))
    managed_english_path = english_voice_assets.installed_model(root)
    managed_english = bool(managed_english_path)
    managed_english_selected = bool(managed_english_path and media.get("piper_model_en") == managed_english_path)
    jobs = _read(root)
    voice_job = _job_state(jobs.get(VOICE_PACKAGE))
    if voice_job in _ACTIVE:
        voice_package = voice_job
    elif installed:
        voice_package = "ready"
    elif voice_job in {"failed", "ready"} or kids_piper_assets.package_files_present(root):
        voice_package = "failed"
    else:
        voice_package = "missing"
    english_job = _job_state(jobs.get(ENGLISH_VOICE_PACKAGE))
    if english_job in _ACTIVE:
        english_package = english_job
    elif managed_english:
        english_package = "ready"
    elif english_job in {"failed", "ready"}:
        english_package = "failed"
    else:
        english_package = "missing"

    def status(key: str, installed: bool, missing: str) -> str:
        job_status = _job_state(jobs.get(key))
        if job_status in _ACTIVE:
            return job_status
        if installed:
            return "ready"
        return "failed" if job_status == "failed" else missing

    hand = kids_hand_assets.ready(optional_hand_game_install.asset_directory(root, manual_hand_directory))
    hand_manual = bool(manual_hand_directory) or not optional_hand_game_install.install_supported()
    kids_fallback = available and bool(kids_piper_assets.enabled_model(root))
    # A regular configured voice takes priority on the live Kids endpoint.
    if not available:
        kids_voice = "engine-missing"
    elif voice == "configured":
        kids_voice = "shared"
    elif str(media.get("piper_model") or "").strip():
        kids_voice = "invalid"
    else:
        kids_voice = "ready" if kids_fallback else "installed-disabled" if installed else "missing"
    reader_default = voice
    if reader_selected:
        reader_default = "reader-thorsten" if installed else "reader-thorsten-invalid"
    return {
        "piper": "engine-found" if available else "engine-missing",
        "reader_default": reader_default,
        "reader_thorsten_selected": reader_selected,
        "reader_thorsten_available": installed and available,
        "reader_english": english,
        "managed_english_selected": managed_english_selected,
        "english_voice_package": english_package,
        "voice_package": voice_package,
        "kids_voice": kids_voice,
        "kids_hand": ("ready" if hand else "manual") if hand_manual else status("kids-hand", hand, "missing"),
    }


def component_inventory(root: str | Path, *, manual_hand_directory: str = "") -> dict[str, object]:
    """Inventory optional runtimes without network access, subprocesses, or mutation."""
    root = Path(root).resolve()
    system = platform.system()
    machine = platform.machine().casefold()
    if bundled_android_runtime():
        runtime = "android-apk"
    elif "PREFIX" in os.environ and "com.termux" in os.environ.get("PREFIX", ""):
        runtime = "termux"
    elif "ANDROID_ROOT" in os.environ or hasattr(sys, "getandroidapilevel"):
        runtime = "android-apk"
    elif Path("/.dockerenv").exists():
        runtime = "docker"
    else:
        runtime = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}.get(system, "unsupported")
    architecture = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64",
                    "i386": "x86", "i686": "x86"}.get(machine, "unknown")
    if system == "Darwin" and architecture == "x86_64":
        architecture = "intel"
    elif system == "Darwin" and architecture == "arm64":
        architecture = "apple-silicon"

    piper = resolve_command("piper", root)
    voice_download_supported = not bundled_android_runtime() or bool(piper)
    ffmpeg = resolve_command("ffmpeg", root)
    ffprobe = resolve_command("ffprobe", root)
    ffplay = resolve_command("ffplay", root)
    ffmpeg_managed = any(
        managed_command(name, root) for name in ("ffmpeg", "ffprobe", "ffplay")
    )
    try:
        # Match the import names checked by app.object_vision and pdf_conversion.
        rapidocr_present = importlib.util.find_spec("rapidocr") is not None
    except (ImportError, ValueError):
        rapidocr_present = False
    try:
        onnx_present = importlib.util.find_spec("onnxruntime") is not None
    except (ImportError, ValueError):
        onnx_present = False
    readiness_state = readiness(root, manual_hand_directory=manual_hand_directory)
    piper_job = _job_state(_read(root).get(PIPER_ENGINE))
    ffmpeg_job = _job_state(_read(root).get(FFMPEG_RUNTIME))
    ocr_job = _job_state(_read(root).get(OCR_RUNTIME))
    managed_ocr = optional_ocr_runtime.ready(root)
    system_ocr = optional_ocr_runtime.host_available()
    piper_from_app = False
    if piper:
        try:
            piper_from_app = Path(piper).resolve().is_relative_to((root / CONTROL_DIR / "components").resolve())
        except (OSError, RuntimeError, ValueError):
            piper_from_app = False
    if piper_job in _ACTIVE or piper_job == "cancelled":
        piper_status = piper_job
    elif piper:
        piper_status = "installed"
    elif piper_job == "failed":
        piper_status = "failed"
    else:
        piper_status = "missing" if optional_piper_runtime.install_supported() else "unsupported"
    components = [
        {"id": PIPER_ENGINE, "status": piper_status, "managed": piper_from_app,
         "install_feature": PIPER_ENGINE if not piper and optional_piper_runtime.install_supported() else None,
         "download_bytes": (sum(item.size for item in optional_piper_runtime.manifest())
                            if not piper and optional_piper_runtime.install_supported() else 0),
         "required_bytes": _PIPER_ENGINE_INSTALL_SPACE if not piper and optional_piper_runtime.install_supported() else 0,
         "version": optional_piper_runtime.installed_version(root),
         "license": optional_piper_runtime.LICENSE,
         "wheels": [{"name": item.filename, "size": item.size, "sha256": item.digest,
                     "url": item.url} for item in optional_piper_runtime.manifest()],
         "detail": "app-managed" if piper_from_app else "host" if piper else
         "available-here" if optional_piper_runtime.install_supported() else "not-installable-here"},
        {"id": "german-voice", "status": str(readiness_state["reader_default"]), "managed": False},
        {"id": "english-voice", "status": str(readiness_state["english_voice_package"]), "managed": True,
         "install_feature": ENGLISH_VOICE_PACKAGE if voice_download_supported else None,
         "download_bytes": sum(item.size for item in english_voice_assets.asset_manifest()) if voice_download_supported else 0,
         "required_bytes": _ENGLISH_VOICE_INSTALL_SPACE if voice_download_supported else 0,
         "detail": str(readiness_state["reader_english"])},
        {"id": "ffmpeg", "status": (ffmpeg_job if ffmpeg_job in _ACTIVE or ffmpeg_job == "cancelled" else
         "installed" if ffmpeg and ffprobe and ffplay else "failed" if ffmpeg_job == "failed" else
         "partial" if ffmpeg or ffprobe or ffplay else
         "missing" if optional_ffmpeg_runtime.install_supported() else "unsupported"),
         "managed": ffmpeg_managed,
         "install_feature": FFMPEG_RUNTIME if not (ffmpeg and ffprobe and ffplay)
         and optional_ffmpeg_runtime.install_supported() else None,
         "download_bytes": optional_ffmpeg_runtime.artifact()["size"]
         if not (ffmpeg and ffprobe and ffplay) and optional_ffmpeg_runtime.install_supported() else 0,
         "required_bytes": _FFMPEG_INSTALL_SPACE if not (ffmpeg and ffprobe and ffplay)
         and optional_ffmpeg_runtime.install_supported() else 0,
         "version": optional_ffmpeg_runtime.installed_version(root),
         "license": optional_ffmpeg_runtime.LICENSE,
         "license_url": optional_ffmpeg_runtime.LICENSE_URL,
         "artifact": optional_ffmpeg_runtime.artifact(),
         "detail": "ffplay-available" if ffplay else "ffplay-missing"},
        {"id": "thorsten-scratch", "status": str(readiness_state["voice_package"]), "managed": True,
         "install_feature": VOICE_PACKAGE if voice_download_supported else None,
         "download_bytes": sum(item.size for item in kids_piper_assets.asset_manifest()) if voice_download_supported else 0,
         "required_bytes": _VOICE_INSTALL_SPACE if voice_download_supported else 0},
        {"id": "mediapipe-hand", "status": str(readiness_state["kids_hand"]), "managed": True,
         "install_feature": "kids-hand",
         "download_bytes": optional_hand_game_install.RUNTIME_MAX_BYTES + kids_hand_assets.ASSETS[
             optional_hand_game_install.MODEL_NAME
         ][1], "required_bytes": _HAND_INSTALL_SPACE},
        {"id": "rapidocr-onnx", "status": "installed" if system_ocr or managed_ocr else
         "failed" if ocr_job == "failed" else
         ocr_job if ocr_job in _ACTIVE or ocr_job == "cancelled" else
         "partial" if rapidocr_present or onnx_present else
         "missing" if optional_ocr_runtime.install_supported() else "unsupported",
         "managed": managed_ocr,
         "install_feature": OCR_RUNTIME if not (system_ocr or managed_ocr)
         and optional_ocr_runtime.install_supported() else None,
         "download_bytes": optional_ocr_runtime.artifact()["download_bytes"]
         if not (system_ocr or managed_ocr) and optional_ocr_runtime.install_supported() else 0,
         "required_bytes": _OCR_INSTALL_SPACE if not (system_ocr or managed_ocr)
         and optional_ocr_runtime.install_supported() else 0,
         "version": optional_ocr_runtime.installed_version(root),
         "license": optional_ocr_runtime.LICENSE,
         "license_url": optional_ocr_runtime.LICENSE_URL,
         "artifact": optional_ocr_runtime.artifact(),
         "detail": "app-managed" if managed_ocr else "server-python" if system_ocr else
         "rapidocr-present" if rapidocr_present else "rapidocr-missing",
         "onnx": "installed" if onnx_present else "missing"},
        {"id": "osm-data", "status": "separate-workflow", "managed": False},
    ]
    return {"runtime": runtime, "architecture": architecture, "components": components,
            "voice_download_supported": voice_download_supported,
            "supported_install_features": ([VOICE_PACKAGE, "kids-hand", ENGLISH_VOICE_PACKAGE]
                                           if voice_download_supported else ["kids-hand"])
            + ([PIPER_ENGINE] if optional_piper_runtime.install_supported() else [])
            + ([FFMPEG_RUNTIME] if optional_ffmpeg_runtime.install_supported() else [])
            + ([OCR_RUNTIME] if optional_ocr_runtime.install_supported() else [])}
