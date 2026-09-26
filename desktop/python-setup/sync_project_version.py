#!/usr/bin/env python3
"""Synchronize the Electron package version with pyproject.toml."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PACKAGE_JSON = REPO / "desktop" / "electron" / "package.json"
PYPROJECT = REPO / "pyproject.toml"


def project_version() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    version = str(data["project"]["version"]).strip()
    if not version:
        raise SystemExit("Projektversion in pyproject.toml ist leer.")
    return version


def main() -> None:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    version = project_version()
    if package.get("version") != version:
        package["version"] = version
        PACKAGE_JSON.write_text(
            json.dumps(package, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"Desktop-Paketversion: {version}")


if __name__ == "__main__":
    main()
