"""Pinned, opt-in English Piper voice files from the Piper voices archive."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .document_store_core import CONTROL_DIR
from .file_lock import exclusive_file_lock

REVISION = "375a0fe641dea077c2a47b4e9a056d6da521eed3"
MODEL_NAME = "en_US-libritts-high.onnx"
MODEL_CARD = f"https://huggingface.co/rhasspy/piper-voices/blob/{REVISION}/en/en_US/libritts/high/MODEL_CARD"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
_BASE = f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}/en/en_US/libritts/high/"
_HOSTS = {"huggingface.co", "us.aws.cdn.hf.co", "cas-bridge.xethub.hf.co"}
_TIMEOUT = 180
_CHUNK = 128 * 1024


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    digest: str


_ASSETS = (
    Asset(MODEL_NAME, 136673811, "9127a559e11603f10b366d1a20ac7426826081dbc521de4c2420c57728d73f0f"),
    Asset(f"{MODEL_NAME}.json", 20163, "2efdc6d7f954588b8180132cbd9b8001933fdd00932c92bc92fd0d2028a9eb3d"),
)


def asset_manifest() -> tuple[Asset, ...]:
    return _ASSETS


def _directory(root: str | Path) -> Path:
    return Path(root).resolve() / CONTROL_DIR / "voices" / f"en-us-libritts-high-{REVISION}"


class _Redirects(HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if (parts.scheme != "https" or parts.hostname not in _HOSTS or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443)):
            raise ValueError("Unbekanntes Downloadziel des Sprachmodells")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@lru_cache(maxsize=16)
def _hash_matches(path: Path, digest: str, fingerprint: tuple[int, ...]) -> bool:
    try:
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            current = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            return current == fingerprint and hashlib.file_digest(stream, "sha256").hexdigest() == digest
    except OSError:
        return False


def _matches(path: Path, asset: Asset) -> bool:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != asset.size:
            return False
        return _hash_matches(path, asset.digest,
                             (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    except OSError:
        return False


def installed_model(root: str | Path) -> str:
    directory = _directory(root)
    try:
        if directory.resolve() != directory:
            return ""
        return str(directory / MODEL_NAME) if all(_matches(directory / item.name, item) for item in _ASSETS) else ""
    except (OSError, RuntimeError):
        return ""


def _download(asset: Asset, target: Path, deadline: float, *, progress=None, cancel_event=None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Download abgebrochen")
    request = Request(_BASE + asset.name, headers={"Accept-Encoding": "identity"})
    digest = hashlib.sha256()
    received = 0
    with build_opener(_Redirects()).open(request, timeout=10) as response, target.open("xb") as stream:
        if response.status != 200:
            raise OSError("Unerwartete HTTP-Antwort für das Sprachmodell")
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Download abgebrochen")
            block = response.read1(min(_CHUNK, asset.size + 1 - received))
            if not block:
                break
            received += len(block)
            if received > asset.size:
                raise ValueError("Sprachmodell größer als erwartet")
            digest.update(block)
            stream.write(block)
            if progress is not None:
                progress(asset.name, received, asset.size)
        else:
            raise TimeoutError("Zeitlimit für das Sprachmodell erreicht")
    if received != asset.size or digest.hexdigest() != asset.digest:
        raise ValueError("Prüfsumme oder Größe des Sprachmodells stimmt nicht")
    target.chmod(0o600)


def install(root: str | Path, *, progress=None, cancel_event=None) -> str:
    """Install the fixed CC BY 4.0 model without selecting or running it."""
    directory = _directory(root)
    try:
        if directory.resolve() != directory:
            return "failed"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with exclusive_file_lock(directory / "install.lock", blocking=False) as acquired:
            if not acquired:
                return "busy"
            if installed_model(root):
                return "ready"
            for leftover in directory.glob("voice-download-*"):
                if leftover.is_dir() and not leftover.is_symlink():
                    shutil.rmtree(leftover)
            with tempfile.TemporaryDirectory(prefix="voice-download-", dir=directory) as folder:
                stage = Path(folder)
                deadline = time.monotonic() + _TIMEOUT
                total = sum(item.size for item in _ASSETS)
                offset = 0
                for asset in _ASSETS:
                    report = None
                    if progress is not None:
                        report = lambda name, received, _size, base=offset: progress(  # noqa: E731
                            name, base + received, total,
                        )
                    _download(asset, stage / asset.name, deadline, progress=report, cancel_event=cancel_event)
                    offset += asset.size
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Download abgebrochen")
                for asset in _ASSETS:
                    os.replace(stage / asset.name, directory / asset.name)
            return "ready" if installed_model(root) else "failed"
    except InterruptedError:
        return "cancelled"
    except (OSError, RuntimeError, ValueError, TimeoutError):
        return "failed"
