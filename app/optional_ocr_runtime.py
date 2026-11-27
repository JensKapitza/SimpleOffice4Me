"""Pinned local RapidOCR/ONNX Runtime for Linux x86_64, CPython 3.12."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import venv
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .document_store_core import CONTROL_DIR
from .file_lock import exclusive_file_lock

VERSION = "3.10.0-1.32.0"
PYTHON_VERSION = (3, 12)
ARCHITECTURE = "x86_64"
LICENSE = "Apache-2.0 (RapidOCR and bundled OCR models); MIT (ONNX Runtime)"
LICENSE_URL = "https://github.com/RapidAI/RapidOCR/tree/v3.10.0"
_HOST = "files.pythonhosted.org"
_RUNTIME = "rapidocr-3.10.0-onnxruntime-1.32.0-cp312-linux-x86_64"
_MANIFEST_NAME = "rapidocr-installed.json"
_WORKER = Path(__file__).with_name("ocr_runtime_worker.py")
_TIMEOUT = 900
_SOCKET_TIMEOUT = 20
_CHUNK = 128 * 1024
_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_WHEEL_BYTES = 128 * 1024 * 1024
_REQUIRED_SPACE = 800 * 1024 * 1024
_INCOMPLETE_MARKER = ".simpleoffice-install-incomplete"
_ROOT: Path | None = None


@dataclass(frozen=True)
class Wheel:
    filename: str
    size: int
    digest: str
    url: str


# Complete wheel-only dependency closure for RapidOCR 3.10.0 and ONNX Runtime
# 1.32.0 on CPython 3.12 / Linux x86_64. URLs, sizes and hashes were compared
# with the exact PyPI file records; no dependency resolution occurs at install.
_WHEELS = (
    Wheel("certifi-2026.11.20-py3-none-any.whl", 127913,
          "7422df25243767b9877031a2248cb14d09ddac9beee846f4cef3d5c9191d488f",
          "https://files.pythonhosted.org/packages/6d/39/5eec3296860b94ac20bb8ed807725b88f6adfab3f343df592afd975f5f86/certifi-2026.11.20-py3-none-any.whl"),
    Wheel("charset_normalizer-3.5.3-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl", 257134,
          "e5e4785d8a0adeadfbdf75f4efc357911842674651cbafd7e97f69b11ec3cbc6",
          "https://files.pythonhosted.org/packages/67/92/6db11254f4009dd980e17473a0ee4a4b54945df4d8346af4bef8999ab85a/charset_normalizer-3.5.3-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl"),
    Wheel("colorlog-6.12.0-py3-none-any.whl", 12239,
          "30d392604e9110045a2c2aeefc27d7a017abbab63f3a8aee594eac0801df784e",
          "https://files.pythonhosted.org/packages/d4/19/0b6647bf5e331521e55d2b63bfbdc210bd9cd605189273f03614a05f702d/colorlog-6.12.0-py3-none-any.whl"),
    Wheel("flatbuffers-25.12.19-py2.py3-none-any.whl", 26661,
          "7634f50c427838bb021c2d66a3d1168e9d199b0607e6329399f04846d42e20b4",
          "https://files.pythonhosted.org/packages/e8/2d/d2a548598be01649e2d46231d151a6c56d10b964d94043a335ae56ea2d92/flatbuffers-25.12.19-py2.py3-none-any.whl"),
    Wheel("idna-3.20-py3-none-any.whl", 69583,
          "ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c",
          "https://files.pythonhosted.org/packages/58/a2/bb081bab032533a855d44de1d56f8e8426114ff1ba5d1f07a438a0a654f8/idna-3.20-py3-none-any.whl"),
    Wheel("numpy-2.5.5-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl", 15720599,
          "704d819265e5d1064d932968e9ee85fff2103ccc78c05b878c06943ba27b8507",
          "https://files.pythonhosted.org/packages/99/61/522b973ba38987b22a4a71beaed2ea6ac9fd4f687baaa2bc9a45100625e0/numpy-2.5.5-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"),
    Wheel("omegaconf-2.4.0-py3-none-any.whl", 254152,
          "f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c",
          "https://files.pythonhosted.org/packages/4f/bd/0d9f8247b0819385213d09564eda5c52d3673313c2414d51f72883ec24cd/omegaconf-2.4.0-py3-none-any.whl"),
    Wheel("onnxruntime-1.32.0-cp312-cp312-manylinux_2_28_x86_64.whl", 27780211,
          "99e1074c4b2197accd5073052a465361be17b89c093b8ffb6e6bd8f986c52df8",
          "https://files.pythonhosted.org/packages/a3/81/55845186c20cba59f614991f4065dea148e48eeb9749893768c85ce64327/onnxruntime-1.32.0-cp312-cp312-manylinux_2_28_x86_64.whl"),
    Wheel("opencv_python_headless-5.1.0.95-cp37-abi3-manylinux_2_28_x86_64.whl", 63704594,
          "7d387d4a64abe0c737440d38b5a90822f9bbcc10c8ce06a35904d6ecf7a2fb30",
          "https://files.pythonhosted.org/packages/c2/be/cdf44e32b580af6c0e8f237665c538e6a5a37872f865e92e08fb854d5fd9/opencv_python_headless-5.1.0.95-cp37-abi3-manylinux_2_28_x86_64.whl"),
    Wheel("packaging-26.4-py3-none-any.whl", 140049,
          "81383611a09c1bc0b08b76e0fd5829d7c6824c65b27a6aee339da133f4233ea2",
          "https://files.pythonhosted.org/packages/ba/7b/5504f91c9c2733f693d7759e50ede5d27119d4da7e5836c46d3b5ff81d56/packaging-26.4-py3-none-any.whl"),
    Wheel("pillow-13.0.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl", 7144542,
          "42d63be3af4f9b951b199b0a9d23365d37ab14687e6d77b74940432c8515c4ca",
          "https://files.pythonhosted.org/packages/e6/99/fdf65cc539e266ab93546c261bdf98411fe165c6e4e13f9dbf597097192b/pillow-13.0.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"),
    Wheel("protobuf-7.36.2-cp310-abi3-manylinux2014_x86_64.whl", 343223,
          "89f23aa53c24553a2416fd4fd1ec06f74fa42b14b546d8883128813f775bbfd2",
          "https://files.pythonhosted.org/packages/db/f3/3996583dd2906297a637af12114deddf7658af6e683fedb83be061983fb5/protobuf-7.36.2-cp310-abi3-manylinux2014_x86_64.whl"),
    Wheel("pyclipper-1.4.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", 978235,
          "d1f807e2b4760a8e5c6d6b4e8c1d71ef52b7fe1946ff088f4fa41e16a881a5ca",
          "https://files.pythonhosted.org/packages/7a/06/6e3e241882bf7d6ab23d9c69ba4e85f1ec47397cbbeee948a16cf75e21ed/pyclipper-1.4.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"),
    Wheel("pyyaml-6.0.4-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl", 849249,
          "93bfb2459195406a518e2f487a9bbf01df240175f31e06f5a252ac6734c7bbf0",
          "https://files.pythonhosted.org/packages/94/80/1d27f7084b983b7cfd1fb35e65a38276d9eddfdcdabc0922bb26cf9521bc/pyyaml-6.0.4-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl"),
    Wheel("rapidocr-3.10.0-py3-none-any.whl", 27288134,
          "2fc34e26cd0f48514804a94f8832283039c7334076bff60768b1fed9ad068050",
          "https://files.pythonhosted.org/packages/59/07/203065283bba0c5e9c10af3602dc25ab3e59ee54dc2d1d22c8faeabf5ed8/rapidocr-3.10.0-py3-none-any.whl"),
    Wheel("requests-2.34.2-py3-none-any.whl", 73075,
          "2a0d60c172f83ac6ab31e4554906c0f3b3588d37b5cb939b1c061f4907e278e0",
          "https://files.pythonhosted.org/packages/a0/f4/c67b0b3f1b9245e8d266f0f112c500d50e5b4e83cb6f3b71b6528104182a/requests-2.34.2-py3-none-any.whl"),
    Wheel("shapely-2.2.0-cp312-cp312-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl", 2300970,
          "650a5f4d8a8e3c96982079d8c99b6ddbe6602bbd1e34c75c2b95dbc0d28ac997",
          "https://files.pythonhosted.org/packages/ad/87/6842e4c996914a47b6bfd3ef14a543e67e993f86a9ea5679c34efc9314ab/shapely-2.2.0-cp312-cp312-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl"),
    Wheel("six-1.17.0-py2.py3-none-any.whl", 11050,
          "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274",
          "https://files.pythonhosted.org/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl"),
    Wheel("tqdm-4.70.1-py3-none-any.whl", 80199,
          "c293e525e6fef9c20e8728fd4612df02a0aa31bb5fe91ecd93e123b1b7bffa73",
          "https://files.pythonhosted.org/packages/a7/03/921a3d3c75785aca9ebfbfcabfbc3a1be12e2ab5265deb026d55a5a3f83e/tqdm-4.70.1-py3-none-any.whl"),
    Wheel("urllib3-2.8.0-py3-none-any.whl", 135717,
          "0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3",
          "https://files.pythonhosted.org/packages/92/9d/c4e665119135114480843e7ab388fa94d8480650450e6f8e26b70d323a4c/urllib3-2.8.0-py3-none-any.whl"),
)
_WHEEL_LICENSES = {
    "certifi-2026.11.20-py3-none-any.whl": "MPL-2.0",
    "charset_normalizer-3.5.3-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl": "MIT",
    "colorlog-6.12.0-py3-none-any.whl": "MIT",
    "flatbuffers-25.12.19-py2.py3-none-any.whl": "Apache-2.0",
    "idna-3.20-py3-none-any.whl": "BSD-3-Clause",
    "numpy-2.5.5-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl": "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
    "omegaconf-2.4.0-py3-none-any.whl": "BSD-3-Clause (ANTLR notices retained)",
    "onnxruntime-1.32.0-cp312-cp312-manylinux_2_28_x86_64.whl": "MIT",
    "opencv_python_headless-5.1.0.95-cp37-abi3-manylinux_2_28_x86_64.whl": "Apache-2.0",
    "packaging-26.4-py3-none-any.whl": "Apache-2.0 OR BSD-2-Clause",
    "pillow-13.0.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl": "MIT-CMU",
    "protobuf-7.36.2-cp310-abi3-manylinux2014_x86_64.whl": "BSD-3-Clause",
    "pyclipper-1.4.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl": "MIT",
    "pyyaml-6.0.4-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl": "MIT",
    "rapidocr-3.10.0-py3-none-any.whl": "Apache-2.0",
    "requests-2.34.2-py3-none-any.whl": "Apache-2.0",
    "shapely-2.2.0-cp312-cp312-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl": "BSD-3-Clause",
    "six-1.17.0-py2.py3-none-any.whl": "MIT",
    "tqdm-4.70.1-py3-none-any.whl": "MPL-2.0 AND MIT",
    "urllib3-2.8.0-py3-none-any.whl": "MIT",
}


def install_supported() -> bool:
    libc_name, libc_version = platform.libc_ver()
    try:
        glibc = tuple(int(part) for part in libc_version.split(".")[:2])
    except ValueError:
        glibc = ()
    return (sys.implementation.name == "cpython" and sys.version_info[:2] == PYTHON_VERSION
            and platform.system() == "Linux" and platform.machine().casefold() in {"x86_64", "amd64"}
            and libc_name.casefold() == "glibc" and glibc >= (2, 28)
            and not hasattr(sys, "getandroidapilevel"))


def manifest() -> tuple[Wheel, ...]:
    return _WHEELS


def host_available() -> bool:
    try:
        if importlib.util.find_spec("onnxruntime") is None:
            return False
        spec = importlib.util.find_spec("rapidocr")
        if spec is None or not spec.submodule_search_locations:
            return False
        model_root = Path(next(iter(spec.submodule_search_locations))) / "models"
        return all((model_root / name).is_file() for name in (
            "PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx", "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        ))
    except (ImportError, ValueError):
        return False


def artifact() -> dict[str, object]:
    return {"version": VERSION, "license": LICENSE, "license_url": LICENSE_URL,
            "wheels": [{"name": row.filename, "size": row.size, "sha256": row.digest, "url": row.url,
                        "license": _WHEEL_LICENSES[row.filename]}
                       for row in _WHEELS],
            "download_bytes": sum(row.size for row in _WHEELS),
            "required_bytes": _REQUIRED_SPACE}


class _TrustedRedirects(HTTPRedirectHandler):
    max_redirections = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if (parts.scheme != "https" or parts.hostname != _HOST or parts.port not in (None, 443)
                or parts.username or parts.password):
            raise ValueError("Untrusted OCR wheel download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def configure_document_root(root: str | Path) -> None:
    global _ROOT
    _ROOT = Path(root).expanduser().resolve()


def _document_root(root: str | Path | None = None) -> Path | None:
    if root is not None:
        try:
            return Path(root).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return None
    try:
        from flask import current_app
        configured = current_app.config.get("DOCUMENT_ROOT")
        if configured:
            return Path(configured).expanduser().resolve()
    except (ImportError, RuntimeError, OSError, ValueError):
        pass
    if _ROOT is not None:
        return _ROOT
    configured = os.environ.get("SIMPLEOFFICE_DOCUMENT_ROOT", "").strip()
    if configured:
        try:
            return Path(configured).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return None
    return None


def _components(root: str | Path, *, create: bool = True) -> Path:
    document_root = Path(root).expanduser().resolve()
    control = document_root / CONTROL_DIR
    components = control / "components"
    if (control.is_symlink() or components.is_symlink()
            or (control.exists() and control.resolve() != control)
            or (components.exists() and components.resolve() != components)):
        raise ValueError("Unsafe component data directory")
    if create:
        components.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = components.lstat()
        if not stat.S_ISDIR(info.st_mode) or (hasattr(os, "geteuid") and info.st_uid != os.geteuid()):
            raise ValueError("Invalid component data directory")
        if stat.S_IMODE(info.st_mode) != 0o700:
            components.chmod(0o700)
    return components


def _read_manifest(components: Path) -> tuple[dict, bytes | None]:
    path = components / _MANIFEST_NAME
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"schema": 1, "components": {}}, None
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_MANIFEST_BYTES:
        raise ValueError("Invalid managed component manifest")
    original = path.read_bytes()
    state = json.loads(original)
    if (not isinstance(state, dict) or state.get("schema") != 1
            or not isinstance(state.get("components"), dict)):
        raise ValueError("Invalid managed component manifest")
    return state, original


def _write_manifest(components: Path, state: dict) -> None:
    path = components / _MANIFEST_NAME
    descriptor, filename = tempfile.mkstemp(prefix=".components-", dir=components)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(state, output, ensure_ascii=False, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _download(wheel: Wheel, destination: Path, deadline: float, *, progress=None, cancel_event=None,
              opener=None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("OCR setup cancelled")
    parsed = urlsplit(wheel.url)
    if (parsed.scheme != "https" or parsed.hostname != _HOST or parsed.port not in (None, 443)
            or parsed.username or parsed.password):
        raise ValueError("Untrusted OCR wheel source")
    request = Request(wheel.url, headers={"Accept-Encoding": "identity"})
    digest = hashlib.sha256()
    received = 0
    open_url = opener or build_opener(_TrustedRedirects()).open
    with open_url(request, timeout=_SOCKET_TIMEOUT) as response, destination.open("xb") as output:
        if response.status != 200:
            raise OSError("Unexpected OCR wheel download response")
        final_url = urlsplit(response.geturl())
        if (final_url.scheme != "https" or final_url.hostname != _HOST or final_url.port not in (None, 443)
                or final_url.username or final_url.password):
            raise ValueError("Untrusted OCR wheel download destination")
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) != wheel.size:
            raise ValueError("Unexpected OCR wheel size")
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("OCR setup cancelled")
            block = response.read(min(_CHUNK, wheel.size + 1 - received))
            if not block:
                break
            received += len(block)
            if received > wheel.size or received > _MAX_WHEEL_BYTES:
                raise ValueError("OCR wheel exceeds its pinned size")
            output.write(block)
            digest.update(block)
            if progress is not None:
                progress(wheel.filename, received, wheel.size)
        else:
            raise TimeoutError("OCR wheel download timed out")
    if received != wheel.size or digest.hexdigest() != wheel.digest:
        raise ValueError("Pinned OCR wheel size or SHA-256 mismatch")
    destination.chmod(0o600)


@lru_cache(maxsize=16)
def _verified_hash(path: Path, fingerprint: tuple[int, ...]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        info = os.fstat(source.fileno())
        current = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if current != fingerprint:
            return ""
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash(path: Path) -> str:
    info = path.stat()
    fingerprint = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return _verified_hash(path, fingerprint)


def _run(args: list[str], *, timeout: int, cancel_event=None) -> str:
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, shell=False)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=2)
                raise InterruptedError("OCR setup cancelled")
            if time.monotonic() >= deadline:
                process.kill()
                process.communicate(timeout=2)
                raise TimeoutError("OCR runtime check timed out")
            try:
                output, _ = process.communicate(timeout=0.2)
                if process.returncode:
                    raise RuntimeError("OCR runtime check failed")
                return (output or b"").decode("utf-8", errors="replace")[:4096]
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def _verified_python(components: Path, record: object) -> Path | None:
    if not isinstance(record, dict) or record.get("version") != VERSION or record.get("runtime") != _RUNTIME:
        return None
    runtime = components / _RUNTIME
    executable = runtime / "bin" / "python"
    try:
        marker = runtime / _INCOMPLETE_MARKER
        if runtime.is_symlink() or not runtime.is_dir() or marker.exists() or marker.is_symlink():
            return None
        runtime_resolved = runtime.resolve(strict=True)
        info = executable.lstat()
        digest = record.get("python_sha256")
        if (not stat.S_ISREG(info.st_mode) or not os.access(executable, os.X_OK)
                or not executable.resolve(strict=True).is_relative_to(runtime_resolved)
                or not isinstance(digest, str) or len(digest) != 64 or _hash(executable) != digest):
            return None
        return executable
    except (OSError, RuntimeError, ValueError):
        return None


def managed_python(root: str | Path | None = None) -> Path | None:
    document_root = _document_root(root)
    if document_root is None:
        return None
    components = document_root / CONTROL_DIR / "components"
    try:
        if components.is_symlink() or not components.is_dir():
            return None
        state, _ = _read_manifest(components)
        record = state["components"].get("rapidocr-runtime")
        return _verified_python(components, record)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def ready(root: str | Path) -> bool:
    return managed_python(root) is not None


def installed_version(root: str | Path) -> str:
    python = managed_python(root)
    if python is None:
        return ""
    return VERSION


def _stage_ready(python: Path) -> bool:
    try:
        output = _run([str(python), str(_WORKER), "--smoke"], timeout=180)
        result = json.loads(output)
        return (result.get("ok") is True and "OCR" in result.get("text", "").upper())
    except (OSError, RuntimeError, TimeoutError, ValueError, json.JSONDecodeError):
        return False


def _cleanup_stages(components: Path) -> None:
    for path in components.iterdir():
        if not path.name.startswith(".rapidocr-install-stage-"):
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) and (not hasattr(os, "geteuid") or info.st_uid == os.geteuid()):
            shutil.rmtree(path)


def install(root: str | Path, *, progress=None, cancel_event=None) -> str:
    """Install hash-pinned RapidOCR in an isolated private Python runtime."""
    if not install_supported():
        return "unsupported"
    components = _components(root)
    runtime = components / _RUNTIME
    if runtime.is_symlink():
        return "failed"
    try:
        with exclusive_file_lock(components / ".rapidocr-install.lock", blocking=False) as acquired:
            if not acquired:
                return "busy"
            _cleanup_stages(components)
            if ready(root):
                return "ready"
            if runtime.exists():
                marker = runtime / _INCOMPLETE_MARKER
                try:
                    marker_info = marker.lstat()
                except FileNotFoundError:
                    marker_info = None
                if marker_info is None:
                    return "failed"
                if (not stat.S_ISREG(marker_info.st_mode) or marker_info.st_size != len(VERSION)
                        or marker.read_text(encoding="ascii") != VERSION
                        or (hasattr(os, "geteuid") and runtime.stat().st_uid != os.geteuid())):
                    return "failed"
                state, _ = _read_manifest(components)
                record = state["components"].get("rapidocr-runtime")
                if record is not None:
                    if not isinstance(record, dict) or record.get("version") != VERSION \
                            or record.get("runtime") != _RUNTIME:
                        return "failed"
                    state["components"].pop("rapidocr-runtime", None)
                    _write_manifest(components, state)
                shutil.rmtree(runtime)
            if shutil.disk_usage(components).free < _REQUIRED_SPACE:
                return "insufficient-space"
            if cancel_event is not None and cancel_event.is_set():
                return "cancelled"
            deadline = time.monotonic() + _TIMEOUT
            with tempfile.TemporaryDirectory(prefix=".rapidocr-install-stage-", dir=components) as temporary:
                stage_root = Path(temporary)
                wheelhouse = stage_root / "wheels"
                wheelhouse.mkdir(mode=0o700)
                total = sum(row.size for row in _WHEELS)
                received = 0
                for wheel in _WHEELS:
                    if progress is not None:
                        progress(wheel.filename, received, total, "download")
                    _download(wheel, wheelhouse / wheel.filename, deadline,
                              cancel_event=cancel_event,
                              progress=lambda _name, got, _size: progress(
                                  wheel.filename, received + got, total, "download") if progress else None)
                    received += wheel.size
                if progress is not None:
                    progress("Creating isolated OCR runtime", received, total, "prepare")
                runtime_stage = stage_root / _RUNTIME
                venv.EnvBuilder(with_pip=True, symlinks=False, clear=True).create(runtime_stage)
                python = runtime_stage / "bin" / "python"
                if cancel_event is not None and cancel_event.is_set():
                    return "cancelled"
                if progress is not None:
                    progress("Installing verified OCR wheels", total, total, "install")
                wheels = [str(wheelhouse / wheel.filename) for wheel in _WHEELS]
                _run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-index",
                      "--no-deps", *wheels], timeout=600, cancel_event=cancel_event)
                _run([str(python), "-m", "pip", "check"], timeout=90, cancel_event=cancel_event)
                if progress is not None:
                    progress("Testing local OCR recognition", total, total, "check")
                if not _stage_ready(python):
                    return "failed"
                if cancel_event is not None and cancel_event.is_set():
                    return "cancelled"
                (runtime_stage / _INCOMPLETE_MARKER).write_text(VERSION, encoding="ascii")
                (runtime_stage / _INCOMPLETE_MARKER).chmod(0o600)
                python_digest = _hash(python)
                state, _ = _read_manifest(components)
                if state["components"].get("rapidocr-runtime") is not None:
                    return "failed"
                os.replace(runtime_stage, runtime)
                try:
                    state["components"]["rapidocr-runtime"] = {
                        "version": VERSION, "runtime": _RUNTIME, "python_sha256": python_digest,
                        "license": LICENSE, "license_url": LICENSE_URL,
                        "wheels": [{"filename": row.filename, "size": row.size, "sha256": row.digest,
                                    "license": _WHEEL_LICENSES[row.filename]}
                                   for row in _WHEELS],
                    }
                    _write_manifest(components, state)
                    (runtime / _INCOMPLETE_MARKER).unlink()
                except Exception:
                    shutil.rmtree(runtime)
                    raise
                return "ready"
    except InterruptedError:
        return "cancelled"
    except OSError:
        return "failed"
    except (ValueError, RuntimeError, TimeoutError):
        return "failed"
