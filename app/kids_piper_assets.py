"""Shared pinned German Piper files with independent Kids opt-in."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .document_store_core import CONTROL_DIR
from .file_lock import exclusive_file_lock


MODEL_REVISION = "d3a970797980dbc61b50bba8852a1728426d46bc"
MODEL_CARD = f"https://huggingface.co/Thorsten-Voice/Piper-Scratch/blob/{MODEL_REVISION}/README.md"
_MODEL_BASE = f"https://huggingface.co/Thorsten-Voice/Piper-Scratch/resolve/{MODEL_REVISION}/"
_MODEL_NAME = "de_DE-thorsten_scratch-medium.onnx"
_DOWNLOAD_HOSTS = {"huggingface.co", "us.aws.cdn.hf.co", "cas-bridge.xethub.hf.co"}
_DOWNLOAD_TIMEOUT = 90
_SOCKET_TIMEOUT = 10
_CHUNK = 128 * 1024
_MAX_MAZE_WAV_BYTES = 2 * 1024 * 1024
_LOG = logging.getLogger(__name__)
_MAZE_DIRECTIONS = re.compile(r"Gehe nach (oben|unten|links|rechts)(, dann nach (oben|unten|links|rechts)){0,14}\.")
_MAZE_FEEDBACK = {
    "Falsch getippt. Hier geht es nicht falsch weiter.",
    "Falsch getippt. Ein Schritt zurück.",
    "Tipp-Modus. Tippe den eingeklammerten Buchstaben.",
}


def is_maze_instruction(text: str) -> bool:
    """Do not turn this optional model into a pronunciation or free-text service."""
    return text in _MAZE_FEEDBACK or _MAZE_DIRECTIONS.fullmatch(text) is not None


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    digest: str


_ASSETS = (
    Asset(_MODEL_NAME, 63516051, "96704e8b515096e84662182599e6c955eb3c5a10341b15619c18eff92ef83f75"),
    Asset(f"{_MODEL_NAME}.json", 5033, "d5909431612ff35daa899ed89c26fffa0de037a64559a9e4aecd80c7fddb9c41"),
)


def asset_manifest() -> tuple[Asset, ...]:
    return _ASSETS


class _VoiceRedirects(HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if (parts.scheme != "https" or parts.hostname not in _DOWNLOAD_HOSTS
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443)):
            raise ValueError("Unbekanntes Downloadziel der Piper-Stimme")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _voice_dir(document_root: str | Path) -> Path:
    return Path(document_root).resolve() / CONTROL_DIR / "kids-voices" / MODEL_REVISION


@lru_cache(maxsize=32)
def _hash_matches(path: Path, digest: str, fingerprint: tuple[int, ...]) -> bool:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == digest


def _matches(path: Path, asset: Asset) -> bool:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != asset.size:
            return False
        fingerprint = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        return _hash_matches(path, asset.digest, fingerprint)
    except OSError:
        return False


def _verified(directory: Path) -> bool:
    return directory.resolve() == directory and all(_matches(directory / asset.name, asset) for asset in _ASSETS)


def installed_model(document_root: str | Path) -> str:
    directory = _voice_dir(document_root)
    try:
        return str(directory / _MODEL_NAME) if _verified(directory) else ""
    except (OSError, RuntimeError):
        return ""


def package_files_present(document_root: str | Path) -> bool:
    directory = _voice_dir(document_root)
    try:
        if directory.resolve() != directory:
            return False
        for asset in _ASSETS:
            try:
                (directory / asset.name).lstat()
            except FileNotFoundError:
                continue
            return True
        return False
    except (OSError, RuntimeError):
        return False


def enabled_model(document_root: str | Path) -> str:
    marker = _voice_dir(document_root) / "enabled"
    try:
        if not stat.S_ISREG(marker.lstat().st_mode):
            return ""
    except OSError:
        return ""
    return installed_model(document_root)


def _remove_staging(directory: Path, pattern: str) -> None:
    for leftover in directory.glob(pattern):
        if not leftover.is_symlink() and leftover.is_dir():
            shutil.rmtree(leftover)


def render_instruction(document_root: str | Path, text: str, model: str) -> bytes | None:
    """Render one managed instruction at a time, with a private bounded cache."""
    from .audio_output_engine import prune_tts_cache, render_tts

    cache = _voice_dir(document_root) / "generated"
    if cache.resolve() != cache:
        raise ValueError("Ungültiges Piper-Audioverzeichnis")
    cache.mkdir(exist_ok=True, mode=0o700)
    with exclusive_file_lock(cache / "render.lock", blocking=False) as acquired:
        if not acquired:
            return None
        _remove_staging(cache, "tts-render-*")
        target = render_tts(text, "kids-de-maze", cache, model=model)
        with target.open("rb") as audio:
            data = audio.read(_MAX_MAZE_WAV_BYTES + 1)
        if len(data) > _MAX_MAZE_WAV_BYTES:
            target.unlink()
            raise ValueError("Labyrinth-Ansage ist zu groß")
        target.touch()  # Cached playback counts as recent use.
        prune_tts_cache(cache, max_files=32, max_bytes=16 * 1024 * 1024)
        return data


def _download(asset: Asset, target: Path, deadline: float) -> None:
    request = Request(_MODEL_BASE + asset.name, headers={"Accept-Encoding": "identity"})
    digest = hashlib.sha256()
    received = 0
    with build_opener(_VoiceRedirects()).open(request, timeout=_SOCKET_TIMEOUT) as response, target.open("xb") as stream:
        if response.status != 200:
            raise OSError("Unerwartete HTTP-Antwort für die Piper-Stimme")
        while time.monotonic() < deadline:
            block = response.read1(min(_CHUNK, asset.size + 1 - received))
            if not block:
                break
            received += len(block)
            if received > asset.size:
                raise ValueError("Piper-Datei größer als erwartet")
            digest.update(block)
            stream.write(block)
        else:
            raise TimeoutError("Zeitlimit für den Piper-Download erreicht")
    if received != asset.size or digest.hexdigest() != asset.digest:
        raise ValueError("Prüfsumme oder Größe der Piper-Datei stimmt nicht")
    target.chmod(0o600)


def _download_pair(directory: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="piper-download-", dir=directory) as staging:
        stage = Path(staging)
        deadline = time.monotonic() + _DOWNLOAD_TIMEOUT
        for asset in _ASSETS:
            _download(asset, stage / asset.name, deadline)
        for asset in _ASSETS:
            os.replace(stage / asset.name, directory / asset.name)


def install_package(document_root: str | Path) -> str:
    """Install only the pinned pair; leave every use/activation setting untouched."""
    return _update_package(document_root, enable_kids=None, allow_download=True)


def set_enabled(document_root: str | Path, enable: bool, *, allow_download: bool = True) -> str:
    """Explicit Kids control; legacy callers may combine it with installation."""
    return _update_package(document_root, enable_kids=enable, allow_download=allow_download)


def _update_package(document_root: str | Path, *, enable_kids: bool | None, allow_download: bool) -> str:
    if enable_kids is True and not shutil.which("piper"):
        return "unavailable"
    directory = _voice_dir(document_root)
    try:
        if directory.resolve() != directory:
            raise ValueError("Ungültiges Piper-Modellverzeichnis")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with exclusive_file_lock(directory / "install.lock", blocking=False) as acquired:
            if not acquired:
                return "busy"
            marker = directory / "enabled"
            if enable_kids is False:
                marker.unlink(missing_ok=True)
                return "disabled"
            _remove_staging(directory, "piper-download-*")
            if not _verified(directory):
                if enable_kids:
                    marker.unlink(missing_ok=True)
                if not allow_download:
                    return "failed"
                _download_pair(directory)
                if not _verified(directory):
                    return "failed"
            if enable_kids is None:
                return "ready"
            if marker.is_symlink():
                marker.unlink()
            marker.touch(mode=0o600)
            return "enabled"
    except (HTTPException, OSError, RuntimeError, ValueError) as exc:
        # Never log URLs: redirected CDN links contain query authentication.
        _LOG.warning("Pinned Piper files setup failed: %s", type(exc).__name__)
        return "failed"
