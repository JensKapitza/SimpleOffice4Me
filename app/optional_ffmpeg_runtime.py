"""Pinned LGPL FFmpeg runtime installer for Linux x86_64 systems."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .document_store_core import CONTROL_DIR
from .file_lock import exclusive_file_lock

VERSION = "N-129308-g17de9f8597-20261126"
RELEASE = "autobuild-2026-11-26-12-56"
LICENSE = "LGPL-3.0-or-later"
LICENSE_URL = "https://github.com/FFmpeg-Builds/FFmpeg-Builds/tree/autobuild-2026-11-26-12-56"
_ARCHIVE = "ffmpeg-N-129308-g17de9f8597-linux64-lgpl-shared.tar.xz"
_ARCHIVE_SIZE = 50_357_160
_ARCHIVE_SHA256 = "b818772f340f8467d2e0a240be3388e93a1746f2b1c6553423dfc5186c2acd63"
_ARCHIVE_URL = (
    "https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/"
    f"{RELEASE}/{_ARCHIVE}"
)
_HOSTS = frozenset(("github.com", "release-assets.githubusercontent.com"))
_RUNTIME_PREFIX = "ffmpeg-N-129308-g17de9f8597-linux64-lgpl-shared"
_EXECUTABLES = ("ffmpeg", "ffprobe", "ffplay")
REQUIRED_SPACE = 700 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 60 * 1024 * 1024
_MAX_EXPANDED_BYTES = 300 * 1024 * 1024
_MAX_MEMBER_BYTES = 128 * 1024 * 1024
_MAX_MEMBERS = 512
_MAX_MANIFEST_BYTES = 128 * 1024
_TIMEOUT = 600
_SOCKET_TIMEOUT = 20
_CHUNK = 128 * 1024
_MARKER = ".simpleoffice-install-incomplete"


def install_supported() -> bool:
    """This archive is built for glibc Linux x86_64, not Android or musl."""
    libc_name, libc_version = platform.libc_ver()
    try:
        glibc = tuple(int(part) for part in libc_version.split(".")[:2])
    except ValueError:
        glibc = ()
    return (
        platform.system() == "Linux"
        and platform.machine().casefold() in {"x86_64", "amd64"}
        and libc_name.casefold() == "glibc"
        and glibc >= (2, 28)
        and not hasattr(sys, "getandroidapilevel")
    )


def artifact() -> dict[str, object]:
    """Return the immutable download metadata shown in expert details."""
    return {"name": _ARCHIVE, "size": _ARCHIVE_SIZE, "sha256": _ARCHIVE_SHA256,
            "url": _ARCHIVE_URL}


class _TrustedRedirects(HTTPRedirectHandler):
    max_redirections = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if (parts.scheme != "https" or parts.hostname not in _HOSTS
                or parts.port not in (None, 443) or parts.username or parts.password):
            raise ValueError("Untrusted FFmpeg download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Invalid FFmpeg archive path")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Invalid FFmpeg archive path")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("Invalid FFmpeg archive path")
    return path


def _wanted(relative: PurePosixPath) -> bool:
    if relative in {PurePosixPath("LICENSE.txt"), *(PurePosixPath("bin") / name for name in _EXECUTABLES)}:
        return True
    return (len(relative.parts) == 2 and relative.parts[0] == "lib"
            and ".so" in relative.name and relative.name not in {".", ".."})


def _write_member(archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> str:
    if member.size < 0 or member.size > _MAX_MEMBER_BYTES:
        raise ValueError("FFmpeg archive member is too large")
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError("FFmpeg archive member is unreadable")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    remaining = member.size
    with stream, target.open("xb") as output:
        while remaining:
            block = stream.read(min(_CHUNK, remaining))
            if not block:
                raise ValueError("FFmpeg archive member is truncated")
            output.write(block)
            digest.update(block)
            remaining -= len(block)
        if stream.read(1):
            raise ValueError("FFmpeg archive member exceeds its declared size")
    target.chmod(0o700 if target.parent.name == "bin" else 0o600)
    return digest.hexdigest()


def _extract(archive_path: Path, stage: Path, *, cancel_event=None) -> tuple[dict[str, str], dict[str, str]]:
    files: dict[str, str] = {}
    links: dict[str, str] = {}
    expanded = 0
    seen = 0
    prefix = _RUNTIME_PREFIX + "/"
    with tarfile.open(archive_path, mode="r:xz") as archive:
        for member in archive:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("FFmpeg setup cancelled")
            seen += 1
            if seen > _MAX_MEMBERS:
                raise ValueError("FFmpeg archive has too many entries")
            name = _safe_relative(member.name)
            if member.isdir() and name.as_posix() == _RUNTIME_PREFIX:
                continue
            if not member.name.startswith(prefix):
                raise ValueError("Unexpected FFmpeg archive root")
            relative = _safe_relative(member.name[len(prefix):])
            if member.isdir():
                continue
            if member.islnk() or member.isdev() or member.isfifo() or not (member.isfile() or member.issym()):
                raise ValueError("Unsupported FFmpeg archive entry type")
            if member.issym() and not _wanted(relative):
                raise ValueError("Unexpected FFmpeg archive link")
            expanded += member.size
            if member.size > _MAX_MEMBER_BYTES or expanded > _MAX_EXPANDED_BYTES:
                raise ValueError("FFmpeg archive expands beyond its size limit")
            if not _wanted(relative):
                continue
            relative_name = relative.as_posix()
            destination = stage.joinpath(*relative.parts)
            if member.issym():
                link = _safe_relative(member.linkname)
                if (relative.parts[0] != "lib" or len(link.parts) != 1
                        or link.as_posix() == relative.name):
                    raise ValueError("Unsafe FFmpeg library link")
                links[relative_name] = link.as_posix()
                continue
            if relative_name in files or relative_name in links:
                raise ValueError("Duplicate FFmpeg archive entry")
            files[relative_name] = _write_member(archive, member, destination)
    required = {f"bin/{name}" for name in _EXECUTABLES} | {"LICENSE.txt"}
    if not required <= files.keys():
        raise ValueError("FFmpeg archive is missing a required file")
    for name, target in links.items():
        if f"lib/{target}" not in files:
            raise ValueError("FFmpeg library link target is missing")
        (stage / name).symlink_to(target)
    for name in _EXECUTABLES:
        if not _is_linux_x86_64(stage / "bin" / name):
            raise ValueError("FFmpeg executable has the wrong architecture")
    return files, links


def _is_linux_x86_64(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(20)
        return len(header) == 20 and header[:6] == b"\x7fELF\x02\x01" and header[18:20] == b"\x3e\x00"
    except OSError:
        return False


def _download(destination: Path, deadline: float, *, progress=None, cancel_event=None, opener=None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("FFmpeg setup cancelled")
    request = Request(_ARCHIVE_URL, headers={"Accept-Encoding": "identity"})
    received = 0
    digest = hashlib.sha256()
    open_url = opener or build_opener(_TrustedRedirects()).open
    with open_url(request, timeout=_SOCKET_TIMEOUT) as response, destination.open("xb") as output:
        if response.status != 200:
            raise OSError("Unexpected FFmpeg download response")
        final_url = urlsplit(response.geturl())
        if (final_url.scheme != "https" or final_url.hostname not in _HOSTS
                or final_url.port not in (None, 443) or final_url.username or final_url.password):
            raise ValueError("Untrusted FFmpeg download destination")
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) != _ARCHIVE_SIZE:
            raise ValueError("Unexpected FFmpeg archive size")
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("FFmpeg setup cancelled")
            block = response.read(min(_CHUNK, _ARCHIVE_SIZE + 1 - received))
            if not block:
                break
            received += len(block)
            if received > _ARCHIVE_SIZE or received > _MAX_ARCHIVE_BYTES:
                raise ValueError("FFmpeg archive is larger than its pinned size")
            output.write(block)
            digest.update(block)
            if progress is not None:
                progress(_ARCHIVE, received, _ARCHIVE_SIZE)
        else:
            raise TimeoutError("FFmpeg download timed out")
    if received != _ARCHIVE_SIZE or digest.hexdigest() != _ARCHIVE_SHA256:
        raise ValueError("Pinned FFmpeg archive size or SHA-256 mismatch")
    destination.chmod(0o600)


def _run(args: list[str], *, cancel_event=None, timeout: int = 45, capture: bool = False) -> bytes:
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                               stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
                               shell=False, cwd=Path(args[0]).parent)
    deadline = time.monotonic() + timeout
    while True:
        if cancel_event is not None and cancel_event.is_set():
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=2)
            raise InterruptedError("FFmpeg setup cancelled")
        if time.monotonic() >= deadline:
            process.kill()
            process.wait(timeout=2)
            raise TimeoutError("FFmpeg verification timed out")
        try:
            stdout, _stderr = process.communicate(timeout=0.2)
        except subprocess.TimeoutExpired:
            continue
        if process.returncode:
            raise RuntimeError("FFmpeg executable check failed")
        return (stdout or b"")[:8192]


def _smoke_test(stage: Path, *, cancel_event=None) -> None:
    ffmpeg = str(stage / "bin" / "ffmpeg")
    ffprobe = str(stage / "bin" / "ffprobe")
    ffplay = str(stage / "bin" / "ffplay")
    for executable in (ffmpeg, ffprobe, ffplay):
        _run([executable, "-version"], cancel_event=cancel_event, capture=True)
    wav = stage / "smoke.wav"
    _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
          "sine=frequency=440:duration=0.25", "-c:a", "pcm_s16le", "-y", str(wav)],
         cancel_event=cancel_event)
    if not wav.is_file() or wav.stat().st_size < 1024:
        raise RuntimeError("FFmpeg test conversion produced no audio")
    details = _run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                    "stream=codec_name,sample_rate,channels", "-show_entries", "format=duration",
                    "-of", "json", str(wav)], cancel_event=cancel_event, capture=True)
    report = json.loads(details.decode("utf-8"))
    streams = report.get("streams")
    duration = float(report.get("format", {}).get("duration", 0))
    if (not isinstance(streams, list) or not streams or streams[0].get("codec_name") != "pcm_s16le"
            or duration < 0.2):
        raise RuntimeError("FFprobe could not analyze the test audio")
    wav.unlink()


def _components(root: str | Path, *, create: bool = True) -> Path:
    document_root = Path(root).expanduser().resolve()
    control = document_root / CONTROL_DIR
    components = control / "components"
    if (control.is_symlink() or components.is_symlink()
            or (control.exists() and control.resolve() != control)
            or (components.exists() and components.resolve() != components)):
        raise ValueError("Unsafe component data directory")
    if not create:
        return components
    components.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = components.lstat()
    if not stat.S_ISDIR(info.st_mode) or (hasattr(os, "geteuid") and info.st_uid != os.geteuid()):
        raise ValueError("Invalid component data directory")
    if stat.S_IMODE(info.st_mode) != 0o700:
        components.chmod(0o700)
    return components


def _manifest(components: Path) -> tuple[dict, bytes | None]:
    path = components / "installed.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"schema": 1, "components": {}}, None
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_MANIFEST_BYTES:
        raise ValueError("Invalid managed component manifest")
    original = path.read_bytes()
    state = json.loads(original)
    if not isinstance(state, dict) or state.get("schema") != 1 or not isinstance(state.get("components"), dict):
        raise ValueError("Invalid managed component manifest")
    return state, original


def _atomic_manifest(components: Path, state: dict) -> None:
    path = components / "installed.json"
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


def _record(components: Path, name: str) -> dict | None:
    try:
        state, _ = _manifest(components)
        row = state["components"].get(name)
        return row if isinstance(row, dict) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def installed_version(root: str | Path) -> str:
    record = _record(_components(root, create=False), "ffmpeg")
    return str(record.get("version", "")) if record else ""


def _cleanup_stages(components: Path) -> None:
    for path in components.iterdir():
        if not path.name.startswith(".ffmpeg-install-stage-"):
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) and (not hasattr(os, "geteuid") or info.st_uid == os.geteuid()):
            shutil.rmtree(path)


def _existing_install(components: Path, runtime: Path) -> bool:
    record = _record(components, "ffmpeg")
    if not record:
        return False
    if record.get("version") != VERSION or record.get("archive_sha256") != _ARCHIVE_SHA256:
        raise ValueError("An unrecognized managed FFmpeg version already exists")
    if not runtime.is_dir() or runtime.is_symlink():
        raise ValueError("Managed FFmpeg runtime directory is missing")
    from .component_runtime import managed_command

    return all(managed_command(name, components.parent.parent) == str(runtime / "bin" / name)
               for name in _EXECUTABLES)


def install(root: str | Path, *, progress=None, cancel_event=None) -> str:
    """Install a pinned FFmpeg/FFprobe/FFplay LGPL runtime without root."""
    if not install_supported():
        return "unsupported"
    components = _components(root)
    runtime = components / f"ffmpeg-{VERSION}-linux-x86_64"
    if runtime.is_symlink():
        return "failed"
    try:
        with exclusive_file_lock(components / ".ffmpeg-install.lock", blocking=False) as acquired:
            if not acquired:
                return "busy"
            _cleanup_stages(components)
            if runtime.exists() and not _existing_install(components, runtime):
                marker = runtime / _MARKER
                if not marker.is_file() or marker.is_symlink() or marker.read_text(encoding="ascii") != VERSION:
                    return "failed"
                shutil.rmtree(runtime)
            if _existing_install(components, runtime):
                marker = runtime / _MARKER
                try:
                    marker_info = marker.lstat()
                except FileNotFoundError:
                    marker_info = None
                if marker_info is not None:
                    if (not stat.S_ISREG(marker_info.st_mode) or marker_info.st_size != len(VERSION)
                            or marker.read_text(encoding="ascii") != VERSION):
                        return "failed"
                    marker.unlink()
                return "ready"
            if shutil.disk_usage(components).free < REQUIRED_SPACE:
                return "insufficient-space"
            if cancel_event is not None and cancel_event.is_set():
                return "cancelled"
            deadline = time.monotonic() + _TIMEOUT
            with tempfile.TemporaryDirectory(prefix=".ffmpeg-install-stage-", dir=components) as temp:
                stage_root = Path(temp)
                archive_path = stage_root / _ARCHIVE
                runtime_stage = stage_root / "runtime"
                runtime_stage.mkdir(mode=0o700)
                if progress is not None:
                    progress(_ARCHIVE, 0, _ARCHIVE_SIZE, "download")
                _download(archive_path, deadline, progress=progress, cancel_event=cancel_event)
                if progress is not None:
                    progress("Checking FFmpeg archive", 0, 0, "prepare")
                files, links = _extract(archive_path, runtime_stage, cancel_event=cancel_event)
                (runtime_stage / _MARKER).write_text(VERSION, encoding="ascii")
                (runtime_stage / _MARKER).chmod(0o600)
                if progress is not None:
                    progress("Testing FFmpeg and FFprobe", 0, 0, "check")
                _smoke_test(runtime_stage, cancel_event=cancel_event)
                (runtime_stage / "smoke.wav").unlink(missing_ok=True)
                if cancel_event is not None and cancel_event.is_set():
                    return "cancelled"
                if runtime.exists():
                    return "failed"
                state, _ = _manifest(components)
                if any(name in state["components"] for name in _EXECUTABLES):
                    return "failed"
                os.replace(runtime_stage, runtime)
                state["components"]["ffmpeg-runtime"] = {
                    "version": VERSION,
                    "license": LICENSE,
                    "license_url": LICENSE_URL,
                    "archive_sha256": _ARCHIVE_SHA256,
                    "runtime": runtime.name,
                    "files": [
                        {"path": f"{runtime.name}/{path}", "sha256": digest}
                        for path, digest in files.items() if path.startswith("lib/")
                    ],
                    "links": [
                        {"path": f"{runtime.name}/{path}", "target": target}
                        for path, target in links.items()
                    ],
                }
                for name in _EXECUTABLES:
                    relative = f"{runtime.name}/bin/{name}"
                    state["components"][name] = {
                        "filename": name,
                        "path": relative,
                        "sha256": files[f"bin/{name}"],
                        "version": VERSION,
                        "license": LICENSE,
                        "archive_sha256": _ARCHIVE_SHA256,
                        "runtime_key": "ffmpeg-runtime",
                    }
                _atomic_manifest(components, state)
                (runtime / _MARKER).unlink(missing_ok=True)
            return "ready"
    except InterruptedError:
        return "cancelled"
    except OSError:
        return "failed"
    except (ValueError, RuntimeError, TimeoutError, tarfile.TarError, json.JSONDecodeError,
            subprocess.SubprocessError):
        return "failed"
