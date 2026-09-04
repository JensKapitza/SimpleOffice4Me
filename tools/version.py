#!/usr/bin/env python3
"""Report a stable SimpleOffice4Me application/build identity.

The human-facing version is ``<major>-<build>``.  A build number can be
injected by CI, recovered from an installed release manifest, or derived from
Git history.  The commit timestamp is retained as a stable fallback for
shallow/no-Git installations.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _positive_int(value: object) -> int:
    try:
        parsed = int(str(value or "0").strip())
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _project_version(root: Path) -> str:
    """Read ``project.version`` without requiring tomllib on Python 3.10."""
    try:
        lines = (root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "1.0.0"
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
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _iso_utc(epoch: int) -> str:
    if epoch <= 0:
        return ""
    try:
        return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return ""


def version_label(info: dict[str, Any]) -> str:
    """Return the concise support/update identity, for example ``1-842``."""
    release = str(info.get("release_version") or "1.0.0")
    major = release.split(".", 1)[0] or "1"
    build_number = _positive_int(info.get("build_number"))
    if build_number:
        return f"{major}-{build_number}"
    epoch = _positive_int(info.get("build_epoch"))
    if epoch:
        stamp = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"{major}-{stamp}"
    return f"{major}-dev"


def build_info(root: str | Path | None = None) -> dict[str, Any]:
    """Resolve version/build metadata without network access or optional deps."""
    root_path = Path(root or PROJECT_ROOT).resolve()
    manifest = _release_manifest(root_path)

    revision = os.environ.get("SIMPLEOFFICE_BUILD_REVISION", "").strip()
    if not revision:
        revision = _git(root_path, "rev-parse", "HEAD") or str(manifest.get("revision") or "")

    # CI may provide a monotonic build number even with a shallow checkout.
    build_number = _positive_int(os.environ.get("SIMPLEOFFICE_BUILD_NUMBER"))
    if not build_number:
        build_number = _positive_int(os.environ.get("GITHUB_RUN_NUMBER"))
    if not build_number:
        git_count = _git(root_path, "rev-list", "--count", "HEAD")
        build_number = _positive_int(git_count) or _positive_int(manifest.get("commit_count"))

    build_epoch = _positive_int(os.environ.get("SIMPLEOFFICE_BUILD_EPOCH"))
    if not build_epoch:
        build_epoch = _positive_int(os.environ.get("SOURCE_DATE_EPOCH"))
    if not build_epoch:
        git_epoch = _git(root_path, "show", "-s", "--format=%ct", "HEAD")
        build_epoch = _positive_int(git_epoch) or _positive_int(manifest.get("build_epoch"))

    release_version = _project_version(root_path)
    info: dict[str, Any] = {
        "release_version": release_version,
        "build_number": build_number,
        "build_epoch": build_epoch,
        "build_timestamp": _iso_utc(build_epoch),
        "revision": revision,
    }
    info["version"] = version_label(info)
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me version/build identity")
    parser.add_argument("--json", action="store_true", help="print machine-readable metadata")
    parser.add_argument("--root", default=str(PROJECT_ROOT), help="application checkout/install root")
    args = parser.parse_args()
    info = build_info(args.root)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
        return
    detail = []
    if info["build_timestamp"]:
        detail.append(info["build_timestamp"])
    if info["revision"]:
        detail.append(str(info["revision"])[:12])
    suffix = f" ({' · '.join(detail)})" if detail else ""
    print(f"SimpleOffice4Me {info['version']}{suffix}")


if __name__ == "__main__":
    main()
