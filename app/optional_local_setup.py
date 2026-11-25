"""Readiness and bounded admin-triggered jobs for optional local capabilities."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import shutil
import threading
import time
from uuid import uuid4

from . import kids_hand_assets, kids_piper_assets, optional_hand_game_install
from .document_store_core import CONTROL_DIR, atomic_json_write
from .file_lock import exclusive_file_lock
from .media_settings import PIPER_READER_THORSTEN
from .settings_store import SettingsStore

FEATURES = frozenset(("kids-voice", "kids-hand"))
_ACTIVE = frozenset(("queued", "downloading"))
_JOB_LIFETIME = 300
_LOG = logging.getLogger(__name__)
_WORKERS = ThreadPoolExecutor(max_workers=1, thread_name_prefix="optional-model-setup")
_ADMISSION = threading.BoundedSemaphore(2)
_JOB_STATES = _ACTIVE | {"ready", "failed"}


def hand_override(config) -> str:
    return str(config.get("KIDS_HAND_GAME_ASSET_DIR")
               or os.environ.get("SIMPLEOFFICE_KIDS_HAND_GAME_ASSET_DIR", "")).strip()


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
    if state in _ACTIVE and (type(started) not in {int, float}
                             or not 0 <= time.time() - started <= _JOB_LIFETIME):
        return "failed"
    return state


def progress_states(root: str | Path) -> dict[str, str]:
    data = _read(Path(root).resolve())
    return {feature: _job_state(data.get(feature)) for feature in FEATURES}


def _transition(root: Path, feature: str, job_id: str, state: str) -> bool:
    with exclusive_file_lock(_job_lock(root)):
        data = _read(root)
        row = data.get(feature)
        if not isinstance(row, dict) or row.get("id") != job_id:
            return False
        if state == "downloading" and _job_state(row) != "queued":
            return False
        row["state"] = state
        atomic_json_write(_job_file(root), data)
        return True


def _execute(root: Path, feature: str, job_id: str) -> None:
    try:
        if not _transition(root, feature, job_id, "downloading"):
            return
        if feature == "kids-voice":
            result = kids_piper_assets.set_enabled(root, True)
            ready = result == "enabled" and bool(kids_piper_assets.enabled_model(root))
        elif feature == "kids-hand":
            result = optional_hand_game_install.install_managed(root)
            ready = result == "ready" and kids_hand_assets.ready(optional_hand_game_install.asset_directory(root, ""))
        else:
            ready = False
        _transition(root, feature, job_id, "ready" if ready else "failed")
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        try:
            _transition(root, feature, job_id, "failed")
        except (OSError, ValueError):
            pass  # If state cannot be written, the capped lease reports failed after a restart.
        _LOG.warning("Optional local setup failed (%s)", type(exc).__name__)
    finally:
        _ADMISSION.release()


def queue(root: str | Path, feature: str, *, manual_hand_directory: str = "") -> str:
    if feature not in FEATURES:
        raise ValueError("unknown optional capability")
    root = Path(root).resolve()
    if feature == "kids-voice" and not shutil.which("piper"):
        return "engine-missing"
    if feature == "kids-hand" and (manual_hand_directory or not optional_hand_game_install.install_supported()):
        return "manual"
    if ((feature == "kids-voice" and kids_piper_assets.enabled_model(root))
            or (feature == "kids-hand" and kids_hand_assets.ready(
                optional_hand_game_install.asset_directory(root, "")))):
        return "ready"
    with exclusive_file_lock(_job_lock(root)):
        data = _read(root)
        state = _job_state(data.get(feature))
        if state in _ACTIVE:
            return state
        if not _ADMISSION.acquire(blocking=False):
            return "busy"
        job_id = uuid4().hex
        try:
            data[feature] = {"id": job_id, "started": time.time(), "state": "queued"}
            atomic_json_write(_job_file(root), data)
            _WORKERS.submit(_execute, root, feature, job_id)
        except (OSError, RuntimeError, ValueError):
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
    return bool(shutil.which("piper") and kids_piper_assets.installed_model(root))


def readiness(root: str | Path, *, manual_hand_directory: str = "") -> dict[str, str | bool]:
    """Read configuration/verified manifests only; no subprocesses or network calls."""
    root = Path(root).resolve()
    available = bool(shutil.which("piper"))
    media = SettingsStore(root).settings()["media"]
    voice = _voice_file_status(media.get("piper_model"))
    english = _voice_file_status(media.get("piper_model_en"))
    reader_selected = media.get("piper_reader_choice") == PIPER_READER_THORSTEN
    if english == "missing" and voice == "configured":
        english = "shared-fallback" if reader_selected else "fallback"
    installed = bool(kids_piper_assets.installed_model(root)) if available or reader_selected else False
    jobs = _read(root)

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
        kids_voice = status("kids-voice", kids_fallback, "missing")
        if kids_voice in {"missing", "failed"} and installed:
            kids_voice = "installed-disabled"
    reader_default = voice
    if reader_selected:
        reader_default = "reader-thorsten" if installed else "reader-thorsten-invalid"
    return {
        "piper": "engine-found" if available else "engine-missing",
        "reader_default": reader_default,
        "reader_thorsten_selected": reader_selected,
        "reader_thorsten_available": installed and available,
        "reader_english": english,
        "kids_voice": kids_voice,
        "kids_hand": ("ready" if hand else "manual") if hand_manual else status("kids-hand", hand, "missing"),
    }
