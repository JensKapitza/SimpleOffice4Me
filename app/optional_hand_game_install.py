"""Opt-in, verified install of the existing fixed Hand Landmarker bundle."""
from __future__ import annotations

from hashlib import sha256
from http.client import HTTPException
from pathlib import Path
import os
import shutil
import tarfile
import tempfile
import time
from uuid import uuid4
import logging
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import kids_hand_assets
from .document_store_core import CONTROL_DIR
from .file_lock import exclusive_file_lock

RUNTIME_URL = "https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-0.10.21.tgz"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")
RUNTIME_MAX_BYTES = 20 * 1024 * 1024
RUNTIME_RELEASE = "0.10.21"
MODEL_RELEASE = "Full FP16 /1/"
MODEL_CARD = "https://huggingface.co/litert-community/MediaPipe-HandLandmarker-Full"
MODEL_NAME = "hand_landmarker.task"
_INSTALL_TIMEOUT = 120
_CHUNK = 128 * 1024
_LOG = logging.getLogger(__name__)


def install_supported() -> bool:
    # A package that lacks the upstream notice can still use a separately
    # prepared operator folder, but may not download/manage the assets here.
    return (Path(__file__).resolve().parents[1] / "licenses" / "MEDIAPIPE-APACHE-2.0.txt").is_file()


def managed_directory(root: str | Path) -> Path:
    return Path(root).resolve() / CONTROL_DIR / "optional-models" / "kids-hand-game-v1"


def asset_directory(root: str | Path, manual_directory: str) -> str:
    """A deliberate operator override is authoritative even if its assets are invalid."""
    if manual_directory:
        return manual_directory
    directory = managed_directory(root)
    try:
        return str(directory) if directory.resolve() == directory else ""
    except (OSError, RuntimeError):
        return ""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _download(url: str, target: Path, maximum: int, digest: str, deadline: float,
              *, exact_size: int | None = None, progress=None, cancel_event=None) -> None:
    if url not in {RUNTIME_URL, MODEL_URL}:
        raise ValueError("unsupported media source")
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("optional installation cancelled")
    started = time.monotonic()
    if started >= deadline:
        raise TimeoutError("optional installation elapsed")
    request = Request(url, headers={"Accept-Encoding": "identity", "User-Agent": "SimpleOffice4Me model setup"})
    calculated = sha256()
    received = 0
    # The origin and expected checksum are fixed. Redirects and environment
    # proxy configuration are not a way to reach a different source.
    with build_opener(ProxyHandler({}), _NoRedirects()).open(
        request, timeout=min(8, deadline - started),
    ) as response, target.open("xb") as stream:
        if response.status != 200:
            raise OSError("unexpected optional installation response")
        try:
            declared = int(getattr(response, "headers", {}).get("Content-Length") or 0)
        except (TypeError, ValueError):
            declared = 0
        total = exact_size or (declared if 0 < declared <= maximum else 0)
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("optional installation cancelled")
            chunk = response.read1(min(_CHUNK, maximum + 1 - received))
            if not chunk:
                break
            received += len(chunk)
            if received > maximum:
                raise ValueError("optional asset exceeds the declared limit")
            calculated.update(chunk)
            stream.write(chunk)
            if progress is not None:
                progress(target.name, received, total)
        else:
            raise TimeoutError("optional installation elapsed")
    if calculated.hexdigest() != digest or (exact_size is not None and received != exact_size):
        raise ValueError("optional asset does not match the pinned release")


def install_managed(root: str | Path, *, progress=None, cancel_event=None) -> str:
    """Only called by an admitted admin job. Never load or run a model."""
    # Reuse the same offline extraction/license checks as documented manual setup.
    from tools.provision_kids_hand_game import install, load_assets

    directory = managed_directory(root)
    if not install_supported():
        _LOG.warning("Pinned hand-game setup unavailable (license notice missing)")
        return "failed"
    try:
        if directory.resolve() != directory:
            _LOG.warning("Pinned hand-game setup rejected an indirect destination")
            return "failed"
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with exclusive_file_lock(directory.parent / "hand-install.lock", blocking=False) as acquired:
            if not acquired:
                return "busy"
            for leftover in directory.parent.glob("hand-install-*"):
                if leftover.is_dir() and not leftover.is_symlink():
                    shutil.rmtree(leftover)
            if kids_hand_assets.ready(str(directory)):
                return "ready"
            # Temporary files, tar entries and the verified output never go into
            # the user-visible Documents tree or the Android/browser request body.
            with tempfile.TemporaryDirectory(prefix="hand-install-", dir=directory.parent) as folder:
                stage = Path(folder)
                runtime, model = stage / "runtime.tgz", stage / MODEL_NAME
                deadline = time.monotonic() + _INSTALL_TIMEOUT
                _, size, digest = kids_hand_assets.ASSETS[MODEL_NAME]
                _download(RUNTIME_URL, runtime, RUNTIME_MAX_BYTES, kids_hand_assets.PACKAGE_SHA256,
                          deadline, progress=progress, cancel_event=cancel_event)
                _download(MODEL_URL, model, size, digest, deadline, exact_size=size,
                          progress=progress, cancel_event=cancel_event)
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("optional installation cancelled")
                assembled = stage / "assets"
                install(assembled, load_assets(runtime, model))
                if not kids_hand_assets.ready(str(assembled)):
                    _LOG.warning("Pinned hand-game setup output did not verify")
                    return "failed"
                backup = directory.parent / f"hand-install-backup-{uuid4().hex}"
                had_previous = directory.exists()
                if directory.is_symlink():
                    _LOG.warning("Pinned hand-game setup rejected a symlink destination")
                    return "failed"
                try:
                    if had_previous:
                        os.replace(directory, backup)
                    os.replace(assembled, directory)
                except OSError:
                    if had_previous and backup.exists() and not directory.exists():
                        os.replace(backup, directory)
                    raise
                if backup.exists() and not backup.is_symlink():
                    shutil.rmtree(backup)
            return "ready"
    except InterruptedError:
        return "cancelled"
    except (HTTPException, OSError, RuntimeError, ValueError, KeyError, tarfile.TarError) as exc:
        _LOG.warning("Pinned hand-game setup failed (%s)", type(exc).__name__)
        return "failed"
