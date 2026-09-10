#!/usr/bin/env python3
"""Build the SimpleOffice4Me Python backend for the Electron desktop package."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BUILD_VENV = HERE / ".build-venv"
DIST = HERE / "dist"
WORK = HERE / "build"
ENTRY = HERE / "runtime_entry.py"


def python_in_venv() -> Path:
    if os.name == "nt":
        return BUILD_VENV / "Scripts" / "python.exe"
    return BUILD_VENV / "bin" / "python"


def build_environment() -> dict[str, str]:
    """Use UTF-8 for all build subprocesses, including PyInstaller isolation."""
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=REPO, env=build_environment(), check=True)


def add_data(source: Path, destination: str) -> str:
    separator = ";" if os.name == "nt" else ":"
    return f"{source}{separator}{destination}"


def main() -> None:
    if sys.version_info < (3, 12):
        raise SystemExit("Desktop-Build benötigt Python 3.12 oder neuer.")
    if not ENTRY.is_file() or not (REPO / "pyproject.toml").is_file():
        raise SystemExit("Build muss aus einem vollständigen SimpleOffice4Me-Checkout erfolgen.")

    if not python_in_venv().is_file():
        venv.EnvBuilder(with_pip=True, clear=True).create(BUILD_VENV)
    python = str(python_in_venv())

    run(python, "-m", "pip", "install", "--upgrade", "pip")
    run(python, "-m", "pip", "install", str(REPO))
    run(python, "-m", "pip", "install", "-r", str(HERE / "requirements-build.txt"))

    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    DIST.mkdir(parents=True, exist_ok=True)

    command = [
        python, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name", "simpleoffice-python",
        "--distpath", str(DIST),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        "--paths", str(REPO),
        "--collect-submodules", "app",
        "--collect-submodules", "tools",
        "--collect-data", "app",
        "--add-data", add_data(REPO / "templates", "templates"),
        "--add-data", add_data(REPO / "static", "static"),
        str(ENTRY),
    ]
    run(*command)

    binary = DIST / ("simpleoffice-python.exe" if os.name == "nt" else "simpleoffice-python")
    if not binary.is_file():
        raise SystemExit(f"PyInstaller-Binary fehlt: {binary}")
    print(f"Desktop-Backend gebaut: {binary}")


if __name__ == "__main__":
    main()
