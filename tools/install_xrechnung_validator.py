#!/usr/bin/env python3
"""Install the pinned KoSIT XRechnung validator and offline configuration."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


VALIDATOR_VERSION = "1.6.3"
XRECHNUNG_VERSION = "3.0.2"
CONFIG_RELEASE = "2026-08-31"
JAR_FILENAME = f"validator-{VALIDATOR_VERSION}-standalone.jar"
CONFIG_FILENAME = (
    f"xrechnung-{XRECHNUNG_VERSION}-validator-configuration-{CONFIG_RELEASE}.zip"
)
JAR_SHA256 = "799e64befca97d4080e03608c80b85dd5a5ecc5f4ae4f35d1116ec2855b9a7c9"
CONFIG_SHA256 = "2530cd107c414511c5d0462ec10f886910395abfca820db82e83d70bf01221a8"
JAR_URL = (
    "https://github.com/itplr-kosit/validator/releases/download/"
    f"v{VALIDATOR_VERSION}/{JAR_FILENAME}"
)
CONFIG_URL = (
    "https://github.com/itplr-kosit/validator-configuration-xrechnung/releases/"
    f"download/v{CONFIG_RELEASE}/{CONFIG_FILENAME}"
)
ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".runtime-tools"
JAR_TARGET = RUNTIME_DIR / JAR_FILENAME
CONFIG_DIR = RUNTIME_DIR / f"xrechnung-{XRECHNUNG_VERSION}-{CONFIG_RELEASE}"
MANIFEST_NAME = ".simpleoffice-xrechnung-manifest.json"
MAX_JAR_BYTES = 32 * 1024 * 1024
MAX_CONFIG_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_FILES = 5000
_ALLOWED_URLS = {JAR_URL, CONFIG_URL}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, *, max_bytes: int) -> bytes:
    if url not in _ALLOWED_URLS:
        raise RuntimeError("XRechnung validator download URL is not allowed")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.hostname != "github.com"
        or parsed.port not in {None, 443}
    ):
        raise RuntimeError("XRechnung validator download URL is not allowed")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SimpleOffice4Me XRechnung validator bootstrap"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        final = urllib.parse.urlsplit(response.geturl())
        final_host = str(final.hostname or "").casefold()
        if (
            final.scheme != "https"
            or final.username
            or final.password
            or final.port not in {None, 443}
            or not (
                final_host == "github.com"
                or final_host == "release-assets.githubusercontent.com"
            )
        ):
            raise RuntimeError("XRechnung validator redirect target is not allowed")
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > max_bytes:
                    raise RuntimeError("XRechnung validator download exceeds size limit")
            except ValueError as exc:
                raise RuntimeError(
                    "XRechnung validator download returned invalid Content-Length"
                ) from exc
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise RuntimeError("XRechnung validator download exceeds size limit")
    return payload


def _archive_path(name: str) -> PurePosixPath:
    normalized = str(name or "").replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError("XRechnung configuration archive contains unsafe path")
    return path


def _extract_configuration(payload: bytes, destination: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    total = 0
    with tempfile.NamedTemporaryFile(suffix=".zip") as handle:
        handle.write(payload)
        handle.flush()
        with zipfile.ZipFile(handle.name) as archive:
            infos = archive.infolist()
            files = [info for info in infos if not info.is_dir()]
            if len(files) > MAX_ARCHIVE_FILES:
                raise RuntimeError("XRechnung configuration contains too many files")
            for info in infos:
                relative = _archive_path(info.filename.rstrip("/"))
                mode = (int(info.external_attr) >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise RuntimeError(
                        "XRechnung configuration archive contains a symbolic link"
                    )
                if info.is_dir():
                    (destination / Path(*relative.parts)).mkdir(
                        parents=True, exist_ok=True, mode=0o700
                    )
                    continue
                if info.file_size < 0 or info.file_size > MAX_EXPANDED_BYTES:
                    raise RuntimeError(
                        "XRechnung configuration file exceeds size limit"
                    )
                total += int(info.file_size)
                if total > MAX_EXPANDED_BYTES:
                    raise RuntimeError(
                        "XRechnung configuration exceeds expanded size limit"
                    )
                target = destination / Path(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                digest = hashlib.sha256()
                written = 0
                with archive.open(info, "r") as source, target.open("xb") as output:
                    while True:
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        written += len(block)
                        if written > info.file_size or written > MAX_EXPANDED_BYTES:
                            raise RuntimeError(
                                "XRechnung configuration expanded size changed"
                            )
                        digest.update(block)
                        output.write(block)
                if written != info.file_size:
                    raise RuntimeError(
                        "XRechnung configuration file ended unexpectedly"
                    )
                if os.name == "posix":
                    os.chmod(target, 0o600)
                manifest[relative.as_posix()] = digest.hexdigest()
    if "scenarios.xml" not in manifest:
        raise RuntimeError("XRechnung configuration does not contain scenarios.xml")
    return manifest


def _manifest_path(directory: Path = CONFIG_DIR) -> Path:
    return directory / MANIFEST_NAME


def verify_installation() -> tuple[Path, Path]:
    if not JAR_TARGET.is_file() or JAR_TARGET.is_symlink():
        raise RuntimeError("KoSIT validator JAR is unavailable")
    if _sha256_file(JAR_TARGET) != JAR_SHA256:
        raise RuntimeError("KoSIT validator JAR checksum does not match the pin")
    manifest_path = _manifest_path()
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RuntimeError("XRechnung configuration manifest is unavailable")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("XRechnung configuration manifest is unreadable") from exc
    if (
        not isinstance(data, dict)
        or data.get("xrechnung_version") != XRECHNUNG_VERSION
        or data.get("release") != CONFIG_RELEASE
        or data.get("archive_sha256") != CONFIG_SHA256
        or not isinstance(data.get("files"), dict)
    ):
        raise RuntimeError("XRechnung configuration manifest does not match the pin")
    expected = data["files"]
    actual = {
        path.relative_to(CONFIG_DIR).as_posix()
        for path in CONFIG_DIR.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != MANIFEST_NAME
    }
    if actual != set(expected):
        raise RuntimeError("XRechnung configuration file set changed")
    for relative, digest in expected.items():
        path = CONFIG_DIR / Path(*PurePosixPath(relative).parts)
        if _sha256_file(path) != str(digest):
            raise RuntimeError("XRechnung configuration file checksum changed")
    scenario = CONFIG_DIR / "scenarios.xml"
    return JAR_TARGET, scenario


def install() -> tuple[Path, Path] | None:
    if os.environ.get(
        "SIMPLEOFFICE_SKIP_XRECHNUNG_VALIDATOR_BOOTSTRAP", ""
    ).casefold() in {"1", "true", "yes"}:
        return None
    if not shutil.which("java"):
        print(
            "WARNUNG: Java fehlt; XRechnung 3.0.2 kann nicht mit KoSIT validiert werden.",
            file=sys.stderr,
        )
        return None
    try:
        return verify_installation()
    except RuntimeError:
        pass

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    jar = _download(JAR_URL, max_bytes=MAX_JAR_BYTES)
    if _sha256(jar) != JAR_SHA256:
        raise RuntimeError("downloaded KoSIT validator checksum does not match the pin")
    config = _download(CONFIG_URL, max_bytes=MAX_CONFIG_BYTES)
    if _sha256(config) != CONFIG_SHA256:
        raise RuntimeError(
            "downloaded XRechnung configuration checksum does not match the pin"
        )

    jar_temp = RUNTIME_DIR / f".{JAR_FILENAME}.{os.getpid()}.tmp"
    config_temp = Path(
        tempfile.mkdtemp(prefix=".xrechnung-config-", dir=RUNTIME_DIR)
    )
    published_config = False
    try:
        jar_temp.write_bytes(jar)
        if os.name == "posix":
            os.chmod(jar_temp, 0o600)
        manifest = _extract_configuration(config, config_temp)
        _manifest_path(config_temp).write_text(
            json.dumps(
                {
                    "format": "simpleoffice-xrechnung-validator-config",
                    "format_version": 1,
                    "xrechnung_version": XRECHNUNG_VERSION,
                    "validator_version": VALIDATOR_VERSION,
                    "release": CONFIG_RELEASE,
                    "archive_sha256": CONFIG_SHA256,
                    "files": manifest,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        if os.name == "posix":
            os.chmod(_manifest_path(config_temp), 0o600)
        os.replace(jar_temp, JAR_TARGET)
        if CONFIG_DIR.exists():
            if CONFIG_DIR.is_symlink() or not CONFIG_DIR.is_dir():
                raise RuntimeError(
                    "XRechnung configuration target is not a normal directory"
                )
            shutil.rmtree(CONFIG_DIR)
        os.replace(config_temp, CONFIG_DIR)
        published_config = True
    finally:
        jar_temp.unlink(missing_ok=True)
        if not published_config:
            shutil.rmtree(config_temp, ignore_errors=True)
    return verify_installation()


if __name__ == "__main__":
    try:
        installed = install()
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        print(
            "WARNUNG: XRechnung-Validator konnte nicht installiert werden: "
            f"{type(exc).__name__}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if installed:
        print(installed[0])
        print(installed[1])
