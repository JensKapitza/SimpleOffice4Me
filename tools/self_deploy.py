#!/usr/bin/env python3
"""Build, clone and apply SimpleOffice4Me releases without Internet on the target."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.software_distribution import apply_release_archive, build_release_archive, clone_release_archive, inspect_release_archive  # noqa: E402


def _stop_script(root: Path) -> list[str]:
    if sys.platform == "win32":
        return ["cmd", "/c", str(root / "stop.bat")]
    return [str(root / "stop.sh")]


def _stop_service(root: Path) -> None:
    script = _stop_script(root)
    if not Path(script[-1]).exists():
        raise ValueError(f"Service script missing: {script[-1]}")
    result = subprocess.run(script, cwd=root, check=False)
    if result.returncode:
        raise ValueError(f"stop failed with exit code {result.returncode}")


def _offline_restart(root: Path) -> None:
    """Restart using the already prepared venv; never invoke pip or start.sh."""
    python = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        raise ValueError("Offline-Neustart nicht möglich: lokale .venv fehlt")
    command = [str(python), "-m", "tools.launcher", "start"]
    kwargs = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


def _install_candidate_dependencies(archive: Path, root: Path) -> None:
    manifest = inspect_release_archive(archive)
    release = manifest["release"]
    wheelhouse = manifest.get("wheelhouse") or {}
    if not wheelhouse.get("included"):
        return
    expected_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if release.get("platform") != sys.platform or release.get("machine") != platform.machine() or release.get("python") != expected_python:
        raise ValueError("Wheelhouse passt nicht zu Plattform/Architektur/Python dieser Instanz")
    python = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        raise ValueError("Offline-Abhängigkeiten können nicht installiert werden: lokale .venv fehlt")
    with tempfile.TemporaryDirectory(prefix="simpleoffice-wheelhouse-") as tmp:
        directory = Path(tmp)
        with zipfile.ZipFile(archive, "r") as package:
            for item in package.infolist():
                if item.filename.startswith("wheelhouse/") and item.filename.endswith(".whl"):
                    target = directory / Path(item.filename).name
                    with package.open(item, "r") as source, target.open("wb") as sink:
                        sink.write(source.read())
        if not any(directory.glob("*.whl")):
            raise ValueError("Release meldet ein Wheelhouse, enthält aber keine Wheels")
        result = subprocess.run(
            [
                str(python), "-m", "pip", "install", "--disable-pip-version-check",
                "--no-index", "--find-links", str(directory), "--no-build-isolation",
                "--editable", str(root),
            ],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise ValueError((result.stderr or result.stdout or "Offline dependency installation failed").strip()[-4000:])
        check = subprocess.run([str(python), "-m", "pip", "check"], cwd=root, text=True, capture_output=True, check=False)
        if check.returncode:
            raise ValueError((check.stderr or check.stdout or "pip check failed").strip()[-4000:])


def _rollback(root: Path, revision: str) -> None:
    subprocess.run(["git", "reset", "--hard", revision], cwd=root, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me self deploy / offline updater")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="Create a self-deploy release archive")
    build.add_argument("output")
    build.add_argument("--with-wheels", action="store_true")
    build.add_argument("--root", default=str(ROOT))

    inspect = commands.add_parser("inspect", help="Verify and show a release archive")
    inspect.add_argument("archive")

    clone = commands.add_parser("clone", help="Clone SimpleOffice onto another PC from an archive")
    clone.add_argument("archive")
    clone.add_argument("target")
    clone.add_argument("--offline-install", action="store_true")

    update = commands.add_parser("update", help="Apply an archive to an existing checkout")
    update.add_argument("archive")
    update.add_argument("--root", default=str(ROOT))
    update.add_argument("--no-dependencies", action="store_true")
    update.add_argument("--stop-running", action="store_true")
    update.add_argument("--restart", action="store_true")
    update.add_argument("--delay", type=float, default=0.0)

    args = parser.parse_args()
    if args.command == "build":
        result = build_release_archive(args.output, root=args.root, include_wheels=args.with_wheels)
    elif args.command == "inspect":
        result = inspect_release_archive(args.archive)
    elif args.command == "clone":
        result = clone_release_archive(args.archive, args.target, offline_install=args.offline_install)
    else:
        root = Path(args.root).expanduser().resolve()
        archive = Path(args.archive).expanduser().resolve()
        if args.delay > 0:
            time.sleep(min(args.delay, 30.0))
        was_stopped = False
        if args.stop_running:
            _stop_service(root)
            was_stopped = True
        old_revision = ""
        try:
            result = apply_release_archive(archive, root=root, install_dependencies=False)
            old_revision = result["old_revision"]
            if not args.no_dependencies:
                _install_candidate_dependencies(archive, root)
        except Exception:
            if old_revision:
                _rollback(root, old_revision)
            if was_stopped and args.restart:
                try:
                    _offline_restart(root)
                except Exception:
                    pass
            raise
        if args.restart:
            _offline_restart(root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
