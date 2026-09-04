"""Stable SimpleOffice4Me application/build identity.

The resolver is deliberately dependency-free and does not import the Flask
application, so launchers, diagnostics and offline update tooling can use it.
"""
from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
PACKAGE_NAME = "simpleoffice4me"


def _positive_int(value: object) -> int:
    try:
        parsed = int(str(value or "0").strip())
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _project_version(root: Path) -> str:
    """Read the source version without requiring tomllib on Python 3.10."""
    try:
        lines = (root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    in_project = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if not in_project:
            continue
        match = re.match(r'''version\s*=\s*["']([^"']+)["']''', line)
        if match:
            return match.group(1).strip() or "1.0.0"
    try:
        return importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "1.0.0"


def _release_manifest(root: Path) -> dict[str, Any]:
    try:
        value = json.loads((root / ".simpleoffice-release.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _git(root: Path, *args: str) -> str:
    if not shutil.which("git") or not (root / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True,
            timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _timestamp_token(epoch: int) -> str:
    if epoch <= 0:
        return ""
    try:
        return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    except (OverflowError, OSError, ValueError):
        return ""


def _iso_utc(epoch: int) -> str:
    if epoch <= 0:
        return ""
    try:
        return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return ""


def version_label(info: dict[str, Any]) -> str:
    """Return the concise identity, e.g. ``1-842`` or timestamp fallback."""
    release = str(info.get("release_version") or "1.0.0")
    major = release.split(".", 1)[0] or "1"
    build_number = _positive_int(info.get("build_number"))
    if build_number:
        return f"{major}-{build_number}"
    stamp = _timestamp_token(_positive_int(info.get("build_epoch")))
    if stamp:
        return f"{major}-{stamp}"
    return f"{major}-dev"


def build_info(root: str | Path | None = None) -> dict[str, Any]:
    """Resolve version metadata locally, without network access."""
    root_path = Path(root or PROJECT_ROOT).resolve()
    manifest = _release_manifest(root_path)

    revision = os.environ.get("SIMPLEOFFICE_BUILD_REVISION", "").strip()
    if not revision:
        revision = _git(root_path, "rev-parse", "HEAD") or str(manifest.get("revision") or "")

    # The commit count matches existing offline release metadata. CI can
    # explicitly override it for packaged artifacts.
    build_number = _positive_int(os.environ.get("SIMPLEOFFICE_BUILD_NUMBER"))
    if not build_number:
        build_number = _positive_int(_git(root_path, "rev-list", "--count", "HEAD"))
    if not build_number:
        build_number = _positive_int(manifest.get("commit_count"))
    if not build_number:
        build_number = _positive_int(os.environ.get("GITHUB_RUN_NUMBER"))

    build_epoch = _positive_int(os.environ.get("SIMPLEOFFICE_BUILD_EPOCH"))
    if not build_epoch:
        build_epoch = _positive_int(os.environ.get("SOURCE_DATE_EPOCH"))
    if not build_epoch:
        build_epoch = _positive_int(_git(root_path, "show", "-s", "--format=%ct", "HEAD"))
    if not build_epoch:
        build_epoch = _positive_int(manifest.get("build_epoch"))

    info: dict[str, Any] = {
        "release_version": _project_version(root_path),
        "build_number": build_number,
        "build_epoch": build_epoch,
        "build_timestamp": _iso_utc(build_epoch),
        "revision": revision,
    }
    info["version"] = version_label(info)
    return info
