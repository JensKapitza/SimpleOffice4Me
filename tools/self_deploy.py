#!/usr/bin/env python3
"""Build, clone and apply SimpleOffice4Me releases without Internet on the target."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.software_distribution import apply_release_archive, build_release_archive, clone_release_archive, inspect_release_archive  # noqa: E402


def _service_script(root: Path, action: str) -> list[str]:
    if sys.platform == "win32":
        name = "stop.bat" if action == "stop" else "start.bat"
        return ["cmd", "/c", str(root / name)]
    name = "stop.sh" if action == "stop" else "start.sh"
    return [str(root / name)]


def _run_service(root: Path, action: str) -> None:
    script = _service_script(root, action)
    if not Path(script[-1]).exists():
        raise ValueError(f"Service script missing: {script[-1]}")
    result = subprocess.run(script, cwd=root, check=False)
    if result.returncode:
        raise ValueError(f"{action} failed with exit code {result.returncode}")


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
        if args.delay > 0:
            time.sleep(min(args.delay, 30.0))
        was_stopped = False
        if args.stop_running:
            _run_service(root, "stop")
            was_stopped = True
        try:
            result = apply_release_archive(args.archive, root=root, install_dependencies=not args.no_dependencies)
        except Exception:
            if was_stopped and args.restart:
                try:
                    _run_service(root, "start")
                except Exception:
                    pass
            raise
        if args.restart:
            _run_service(root, "start")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
