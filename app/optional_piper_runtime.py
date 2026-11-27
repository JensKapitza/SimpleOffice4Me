"""Pinned Linux x86_64 / CPython 3.12 Piper runtime installer.

Only pre-verified PyPI wheels are installed, offline, into a private app-data
virtual environment. This intentionally does not treat Linux wheels as Android
or cross-platform artifacts.
"""
from __future__ import annotations

import hashlib
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
from dataclasses import dataclass
from threading import Event
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .document_store_core import CONTROL_DIR
from .file_lock import exclusive_file_lock

VERSION = "1.8.0"
PYTHON_VERSION = (3, 12)
ARCHITECTURE = "x86_64"
LICENSE = "GPL-3.0-or-later"
_HOST = "files.pythonhosted.org"
_TIMEOUT = 300
_SOCKET_TIMEOUT = 20
_CHUNK = 128 * 1024
_REQUIRED_SPACE = 300 * 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024
_INCOMPLETE_MARKER = ".simpleoffice-install-incomplete"


@dataclass(frozen=True)
class Wheel:
    filename: str
    size: int
    digest: str
    url: str
    requirement: str


# Exact CPython 3.12 manylinux x86_64 wheels. Transitive dependencies are
# explicit so pip never resolves or downloads another package at install time.
_WHEELS = (
    Wheel("piper_tts-1.8.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl",
          34131442, "25b4d3f31ff70c8fa7151908e00aaa5650cbdf16bca8fcf21299f3941b89a7d3",
          "https://files.pythonhosted.org/packages/84/81/0112a7d510911f33018dc24023d1655bb772f84a4e95fe7f0180f66bbd17/piper_tts-1.8.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl",
          "piper-tts==1.8.0"),
    Wheel("onnxruntime-1.32.0-cp312-cp312-manylinux_2_28_x86_64.whl",
          27780211, "99e1074c4b2197accd5073052a465361be17b89c093b8ffb6e6bd8f986c52df8",
          "https://files.pythonhosted.org/packages/a3/81/55845186c20cba59f614991f4065dea148e48eeb9749893768c85ce64327/onnxruntime-1.32.0-cp312-cp312-manylinux_2_28_x86_64.whl",
          "onnxruntime==1.32.0"),
    Wheel("numpy-2.5.5-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
          15720599, "704d819265e5d1064d932968e9ee85fff2103ccc78c05b878c06943ba27b8507",
          "https://files.pythonhosted.org/packages/99/61/522b973ba38987b22a4a71beaed2ea6ac9fd4f687baaa2bc9a45100625e0/numpy-2.5.5-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
          "numpy==2.5.5"),
    Wheel("protobuf-7.36.2-cp310-abi3-manylinux2014_x86_64.whl",
          343223, "89f23aa53c24553a2416fd4fd1ec06f74fa42b14b546d8883128813f775bbfd2",
          "https://files.pythonhosted.org/packages/db/f3/3996583dd2906297a637af12114deddf7658af6e683fedb83be061983fb5/protobuf-7.36.2-cp310-abi3-manylinux2014_x86_64.whl",
          "protobuf==7.36.2"),
    Wheel("flatbuffers-25.12.19-py2.py3-none-any.whl", 26661,
          "7634f50c427838bb021c2d66a3d1168e9d199b0607e6329399f04846d42e20b4",
          "https://files.pythonhosted.org/packages/e8/2d/d2a548598be01649e2d46231d151a6c56d10b964d94043a335ae56ea2d92/flatbuffers-25.12.19-py2.py3-none-any.whl",
          "flatbuffers==25.12.19"),
    Wheel("pathvalidate-3.3.1-py3-none-any.whl", 24305,
          "5263baab691f8e1af96092fa5137ee17df5bdfbd6cff1fcac4d6ef4bc2e1735f",
          "https://files.pythonhosted.org/packages/9a/70/875f4a23bfc4731703a5835487d0d2fb999031bd415e7d17c0ae615c18b7/pathvalidate-3.3.1-py3-none-any.whl",
          "pathvalidate==3.3.1"),
    Wheel("packaging-26.4-py3-none-any.whl", 140049,
          "81383611a09c1bc0b08b76e0fd5829d7c6824c65b27a6aee339da133f4233ea2",
          "https://files.pythonhosted.org/packages/ba/7b/5504f91c9c2733f693d7759e50ede5d27119d4da7e5836c46d3b5ff81d56/packaging-26.4-py3-none-any.whl",
          "packaging==26.4"),
)


def install_supported() -> bool:
    """The wheel set is only valid for CPython 3.12 on Linux x86_64."""
    libc_name, libc_version = platform.libc_ver()
    try:
        glibc = tuple(int(part) for part in libc_version.split(".")[:2])
    except ValueError:
        glibc = ()
    return (
        sys.implementation.name == "cpython"
        and sys.version_info[:2] == PYTHON_VERSION
        and platform.system() == "Linux"
        and platform.machine().casefold() in {"x86_64", "amd64"}
        and libc_name.casefold() == "glibc"
        and glibc >= (2, 28)
        and not hasattr(sys, "getandroidapilevel")
    )


def manifest() -> tuple[Wheel, ...]:
    return _WHEELS


class _TrustedRedirects(HTTPRedirectHandler):
    max_redirections = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.hostname != _HOST or parts.port not in (None, 443):
            raise ValueError("Untrusted PyPI wheel redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _paths(root: str | Path) -> tuple[Path, Path, Path]:
    document_root = Path(root).expanduser().resolve()
    control = document_root / CONTROL_DIR
    components = control / "components"
    runtime = components / f"piper-{VERSION}-cp312-linux-x86_64"
    if (control.is_symlink() or components.is_symlink()
            or (control.exists() and control.resolve() != control)
            or (components.exists() and components.resolve() != components)):
        raise ValueError("Unsafe component data directory")
    return control, components, runtime


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Invalid component directory")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise ValueError("Component directory is owned by another user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        path.chmod(0o700)


def _atomic_write_manifest(path: Path, state: dict) -> None:
    encoded = (json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd, name = tempfile.mkstemp(prefix=".installed-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _launcher_content(runtime: Path) -> bytes:
    return (
        "#!/bin/sh\n"
        "COMPONENT_ROOT=$(CDPATH= cd -P \"$(dirname \"$0\")/..\" && pwd) || exit 126\n"
        f"exec \"$COMPONENT_ROOT/{runtime.name}/bin/python\" "
        f"\"$COMPONENT_ROOT/{runtime.name}/bin/piper\" \"$@\"\n"
    ).encode("utf-8")


def _download(wheel: Wheel, destination: Path, deadline: float, *, progress=None, cancel_event=None,
              opener=None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Piper setup cancelled")
    parsed = urlsplit(wheel.url)
    if parsed.scheme != "https" or parsed.hostname != _HOST or parsed.username or parsed.password:
        raise ValueError("Untrusted PyPI wheel source")
    request = Request(wheel.url, headers={"Accept-Encoding": "identity"})
    digest = hashlib.sha256()
    received = 0
    open_url = opener or build_opener(_TrustedRedirects()).open
    with open_url(request, timeout=_SOCKET_TIMEOUT) as response, destination.open("xb") as output:
        if response.status != 200:
            raise OSError("Unexpected wheel download response")
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Piper setup cancelled")
            block = response.read(min(_CHUNK, wheel.size + 1 - received))
            if not block:
                break
            received += len(block)
            if received > wheel.size:
                raise ValueError("Wheel is larger than pinned size")
            digest.update(block)
            output.write(block)
            if progress is not None:
                progress(wheel.filename, received, wheel.size)
        else:
            raise TimeoutError("Wheel download timed out")
    if received != wheel.size or digest.hexdigest() != wheel.digest:
        raise ValueError("Pinned wheel size or SHA-256 mismatch")
    destination.chmod(0o600)


def _run(args: list[str], *, cancel_event=None, timeout: int = 300) -> None:
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              shell=False)
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            raise InterruptedError("Piper setup cancelled")
        if time.monotonic() >= deadline:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            raise TimeoutError("Piper package installation timed out")
        time.sleep(0.1)
    if process.returncode != 0:
        raise RuntimeError("Pinned Piper wheel installation failed")


def _write_manifest(components: Path, wrapper: Path, runtime: Path) -> None:
    manifest_path = components / "installed.json"
    wrapper_dir = components / "bin"
    _ensure_private_directory(wrapper_dir)
    if wrapper_dir.resolve() != wrapper_dir:
        raise ValueError("Invalid managed executable directory")
    state = {"schema": 1, "components": {}}
    try:
        info = manifest_path.lstat()
    except FileNotFoundError:
        info = None
    previous_manifest = None
    if info is not None:
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("Invalid managed component manifest")
        previous_manifest = manifest_path.read_bytes()
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("schema") != 1 or not isinstance(loaded.get("components"), dict):
            raise ValueError("Invalid managed component manifest")
        state = loaded
    entries = state["components"]
    prior = entries.get("piper")
    if prior is not None:
        # Never replace a binary the manager cannot positively identify.
        if (not isinstance(prior, dict) or prior.get("path") != "bin/piper"
                or prior.get("filename") != "piper"):
            raise ValueError("Existing Piper runtime is not a managed installer entry")
        try:
            existing = wrapper.lstat()
            expected = str(prior.get("sha256") or "")
            if (not stat.S_ISREG(existing.st_mode) or existing.st_size > 4096 or len(expected) != 64
                    or hashlib.sha256(wrapper.read_bytes()).hexdigest() != expected):
                raise ValueError("Existing Piper executable failed manager integrity check")
            if prior.get("version") != VERSION:
                raise ValueError("Refusing to replace a different managed Piper version")
        except OSError as exc:
            raise ValueError("Existing managed Piper executable is missing") from exc
    else:
        if wrapper.exists() or wrapper.is_symlink():
            raise ValueError("Refusing to overwrite an unmanaged Piper executable")

    command = _launcher_content(runtime)
    wrapper.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous_wrapper = None
    previous_mode = 0o700
    try:
        previous_wrapper = wrapper.read_bytes()
        previous_mode = stat.S_IMODE(wrapper.lstat().st_mode)
    except FileNotFoundError:
        pass
    digest = hashlib.sha256(command).hexdigest()
    entries["piper"] = {"filename": "piper", "path": "bin/piper", "sha256": digest,
                        "version": VERSION, "license": LICENSE}
    temp_fd, temp_name = tempfile.mkstemp(prefix=".piper-new-", dir=wrapper.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(temp_fd, "wb") as output:
            output.write(command)
            output.flush()
            os.fsync(output.fileno())
        temp.chmod(0o700)
        os.replace(temp, wrapper)
        try:
            _atomic_write_manifest(manifest_path, state)
        except Exception:
            if previous_manifest is None:
                manifest_path.unlink(missing_ok=True)
            else:
                manifest_fd, manifest_name = tempfile.mkstemp(prefix=".manifest-restore-", dir=components)
                manifest_temp = Path(manifest_name)
                try:
                    with os.fdopen(manifest_fd, "wb") as output:
                        output.write(previous_manifest)
                        output.flush()
                        os.fsync(output.fileno())
                    manifest_temp.chmod(0o600)
                    os.replace(manifest_temp, manifest_path)
                finally:
                    manifest_temp.unlink(missing_ok=True)
            if previous_wrapper is None:
                wrapper.unlink(missing_ok=True)
            else:
                restore_fd, restore_name = tempfile.mkstemp(prefix=".piper-restore-", dir=wrapper.parent)
                restore = Path(restore_name)
                try:
                    with os.fdopen(restore_fd, "wb") as output:
                        output.write(previous_wrapper)
                        output.flush()
                        os.fsync(output.fileno())
                    restore.chmod(previous_mode)
                    os.replace(restore, wrapper)
                finally:
                    restore.unlink(missing_ok=True)
            raise
    finally:
        temp.unlink(missing_ok=True)


def _manifest_state(components: Path) -> dict:
    manifest_path = components / "installed.json"
    try:
        info = manifest_path.lstat()
    except FileNotFoundError:
        return {"schema": 1, "components": {}}
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_MANIFEST_BYTES:
        raise ValueError("Invalid managed component manifest")
    state = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schema") != 1 or not isinstance(state.get("components"), dict):
        raise ValueError("Invalid managed component manifest")
    return state


def _managed_wrapper(components: Path) -> tuple[Path | None, dict | None]:
    wrapper = components / "bin" / "piper"
    try:
        record = _manifest_state(components)["components"].get("piper")
        if (not isinstance(record, dict) or record.get("filename") != "piper"
                or record.get("path") != "bin/piper"):
            return None, None
        binary = wrapper.lstat()
        digest = str(record.get("sha256") or "")
        if (not stat.S_ISREG(binary.st_mode) or len(digest) != 64
                or hashlib.sha256(wrapper.read_bytes()).hexdigest() != digest):
            return None, None
        return wrapper, record
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None, None


def _discard_incomplete_runtime(runtime: Path) -> bool:
    """Remove only a real, installer-marked version directory after a crash."""
    try:
        runtime_info = runtime.lstat()
    except FileNotFoundError:
        return True
    if not stat.S_ISDIR(runtime_info.st_mode):
        return False
    marker = runtime / _INCOMPLETE_MARKER
    try:
        marker_info = marker.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(marker_info.st_mode) or marker_info.st_size != len(VERSION):
        return False
    try:
        if marker.read_text(encoding="ascii") != VERSION:
            return False
    except (OSError, UnicodeError):
        return False
    shutil.rmtree(runtime)
    return True


def _recover_interrupted_install(components: Path, runtime: Path) -> str | None:
    """Reconcile a crash between placing the launcher and committing its manifest."""
    marker = runtime / _INCOMPLETE_MARKER
    try:
        marker_info = marker.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(marker_info.st_mode) or marker_info.st_size != len(VERSION)
            or marker.read_text(encoding="ascii") != VERSION):
        return "failed"
    wrapper = components / "bin" / "piper"
    state = _manifest_state(components)
    record = state["components"].get("piper")
    try:
        wrapper_info = wrapper.lstat()
    except FileNotFoundError:
        wrapper_info = None
    if wrapper_info is not None:
        if not stat.S_ISREG(wrapper_info.st_mode) or wrapper_info.st_size > 4096:
            return "failed"
        content = wrapper.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        expected = hashlib.sha256(_launcher_content(runtime)).hexdigest()
        if digest != expected:
            return "failed"
        if record is not None:
            known_wrapper, known_record = _managed_wrapper(components)
            if (known_wrapper is not None and known_record is not None
                    and known_record.get("version") == VERSION):
                marker.unlink()
                return "ready"
            return "failed"
        wrapper.unlink()
    elif record is not None:
        return "failed"
    if not _discard_incomplete_runtime(runtime):
        return "failed"
    return "retry"


def _cleanup_stale_stages(components: Path) -> None:
    """Delete abandoned installer staging directories while holding its lock."""
    for entry in components.iterdir():
        if not entry.name.startswith(".piper-install-stage-"):
            continue
        info = entry.lstat()
        if not stat.S_ISDIR(info.st_mode):
            continue
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise ValueError("Piper staging directory is owned by another user")
        shutil.rmtree(entry)


def install(root: str | Path, *, progress=None, cancel_event=None) -> str:
    """Install the pinned Piper CLI without root or runtime network resolution."""
    if not install_supported():
        return "unsupported"
    control, components, runtime = _paths(root)
    _ensure_private_directory(components)
    if runtime.is_symlink():
        return "failed"
    try:
        with exclusive_file_lock(components / ".piper-install.lock", blocking=False) as acquired:
            if not acquired:
                return "busy"
            try:
                _cleanup_stale_stages(components)
                wrapper_dir = components / "bin"
                if wrapper_dir.is_symlink():
                    return "failed"
                interrupted = _recover_interrupted_install(components, runtime)
                if interrupted in {"failed", "ready"}:
                    return interrupted
                wrapper = wrapper_dir / "piper"
                manager_state = _manifest_state(components)
                existing_record = manager_state["components"].get("piper")
                known_wrapper, known_record = _managed_wrapper(components)
                if ((wrapper.exists() or wrapper.is_symlink()) and known_wrapper is None
                        or known_record is not None and known_wrapper is None):
                    return "failed"
                if existing_record is not None and not known_record:
                    return "failed"
                if runtime.exists():
                    ready = bool(known_wrapper and known_record
                                 and known_record.get("version") == VERSION
                                 and (runtime / "bin" / "python").is_file())
                    if ready:
                        marker = runtime / _INCOMPLETE_MARKER
                        try:
                            marker_info = marker.lstat()
                        except FileNotFoundError:
                            marker_info = None
                        if marker_info is not None and not stat.S_ISREG(marker_info.st_mode):
                            return "failed"
                        if marker_info is not None:
                            marker.unlink()
                        return "ready"
                    if not _discard_incomplete_runtime(runtime):
                        return "failed"
                    # This exact version directory carries our pre-promotion
                    # marker, so it is safe to discard and retry after a crash.
                if shutil.disk_usage(components).free < _REQUIRED_SPACE:
                    return "insufficient-space"
                if cancel_event is not None and cancel_event.is_set():
                    return "cancelled"
                deadline = time.monotonic() + _TIMEOUT
                total = sum(wheel.size for wheel in _WHEELS)
                offset = 0
                promoted_runtime = False
                with tempfile.TemporaryDirectory(prefix=".piper-install-stage-", dir=components) as temp_dir:
                    stage = Path(temp_dir)
                    wheelhouse = stage / "wheels"
                    wheelhouse.mkdir(mode=0o700)
                    for wheel in _WHEELS:
                        report = None
                        if progress is not None:
                            report = lambda name, got, _size, base=offset: progress(name, base + got, total)
                        _download(wheel, wheelhouse / wheel.filename, deadline, progress=report,
                                  cancel_event=cancel_event)
                        offset += wheel.size
                    if cancel_event is not None and cancel_event.is_set():
                        return "cancelled"
                    runtime_stage = stage / "runtime"
                    if progress is not None:
                        progress("Preparing Piper environment", 0, 0, "prepare")
                    _run([sys.executable, "-m", "venv", str(runtime_stage)], cancel_event=cancel_event, timeout=60)
                    requirements = stage / "requirements.txt"
                    requirements.write_text("\n".join(
                        wheel.requirement + " --hash=sha256:" + wheel.digest for wheel in _WHEELS
                    ) + "\n", encoding="utf-8")
                    python = runtime_stage / "bin" / "python"
                    if progress is not None:
                        progress("Installing Piper runtime", 0, 0, "install")
                    _run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-index",
                          "--no-deps", "--require-hashes", "--find-links", str(wheelhouse), "-r",
                          str(requirements)], cancel_event=cancel_event)
                    if progress is not None:
                        progress("Checking Piper runtime", 0, 0, "check")
                    _run([str(python), "-m", "pip", "check"], cancel_event=cancel_event, timeout=30)
                    piper_cli = runtime_stage / "bin" / "piper"
                    _run([str(piper_cli), "--help"], cancel_event=cancel_event, timeout=30)
                    if cancel_event is not None and cancel_event.is_set():
                        return "cancelled"
                    marker = runtime_stage / _INCOMPLETE_MARKER
                    marker_fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(marker_fd, "wb") as marker_file:
                        marker_file.write(VERSION.encode("ascii"))
                        marker_file.flush()
                        os.fsync(marker_file.fileno())
                    os.replace(runtime_stage, runtime)
                    promoted_runtime = True
                    wrapper = components / "bin" / "piper"
                    _write_manifest(components, wrapper, runtime)
                    (runtime / _INCOMPLETE_MARKER).unlink()
                    promoted_runtime = False
                return "ready"
            except InterruptedError:
                return "cancelled"
            except (OSError, RuntimeError, TimeoutError, ValueError, json.JSONDecodeError):
                if "promoted_runtime" in locals() and promoted_runtime:
                    current_wrapper, current_record = _managed_wrapper(components)
                    if not current_wrapper or not current_record or current_record.get("version") != VERSION:
                        shutil.rmtree(runtime, ignore_errors=True)
                return "failed"
    except (OSError, RuntimeError, ValueError):
        return "failed"


def installed_version(root: str | Path) -> str:
    try:
        _, _, runtime = _paths(root)
        components = runtime.parent
        wrapper, record = _managed_wrapper(components)
        return VERSION if wrapper and record and record.get("version") == VERSION and (runtime / "bin" / "python").is_file() else ""
    except (OSError, RuntimeError, ValueError):
        return ""
