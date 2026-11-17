"""Run npm audit with a narrowly scoped, time-limited upstream advisory exception."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "desktop" / "electron"
EXCEPTION_EXPIRES = date(2026, 12, 1)
ADVISORY_URL = "https://github.com/advisories/GHSA-fc5q-39xc-vjpf"
UPSTREAM_FIX_URL = "https://github.com/micromatch/picomatch/pull/194"
ALLOWED_CHAIN = {
    "@electron/rebuild",
    "app-builder-lib",
    "dmg-builder",
    "electron-builder",
    "electron-builder-squirrel-windows",
    "fdir",
    "node-gyp",
    "picomatch",
    "tinyglobby",
}


def _known_build_only_advisory(report: dict[str, Any], lock: dict[str, Any]) -> bool:
    vulnerabilities = report.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict) or set(vulnerabilities) != ALLOWED_CHAIN:
        return False

    root = vulnerabilities["picomatch"]
    advisories = root.get("via")
    if (
        root.get("severity") != "high"
        or root.get("isDirect") is not False
        or not isinstance(advisories, list)
        or len(advisories) != 1
        or not isinstance(advisories[0], dict)
        or advisories[0].get("url") != ADVISORY_URL
        or advisories[0].get("range") != "<=4.0.7"
        or advisories[0].get("severity") != "high"
    ):
        return False

    packages = lock.get("packages")
    if not isinstance(packages, dict):
        return False
    root_package = packages.get("node_modules/picomatch")
    if not isinstance(root_package, dict) or root_package.get("version") != "4.0.7":
        return False

    for name, item in vulnerabilities.items():
        if not isinstance(item, dict) or item.get("severity") != "high":
            return False
        nodes = item.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return False
        for node in nodes:
            package = packages.get(node)
            if not isinstance(package, dict) or package.get("dev") is not True:
                return False
        if name == "picomatch":
            continue
        via = item.get("via")
        if not isinstance(via, list) or not via or any(
            not isinstance(parent, str) or parent not in ALLOWED_CHAIN for parent in via
        ):
            return False

    summary = report.get("metadata", {}).get("vulnerabilities", {})
    return summary.get("total") == len(ALLOWED_CHAIN) and summary.get("critical") == 0


def main() -> int:
    result = subprocess.run(
        ["npm", "audit", "--json"],
        cwd=PACKAGE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(result.stderr or result.stdout or "npm audit returned no JSON report\n")
        return result.returncode or 1

    summary = report.get("metadata", {}).get("vulnerabilities", {})
    if result.returncode == 0 and summary.get("total", 0) == 0:
        print("Electron npm dependency audit passed with no advisories.")
        return 0

    today = datetime.now(timezone.utc).date()
    lock = json.loads((PACKAGE_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    if today < EXCEPTION_EXPIRES and _known_build_only_advisory(report, lock):
        print(
            "Temporary exception: npm reports only GHSA-fc5q-39xc-vjpf in the "
            "Electron build-only dependency chain. No patched npm release exists; "
            f"upstream fix: {UPSTREAM_FIX_URL}. Exception expires {EXCEPTION_EXPIRES.isoformat()}."
        )
        return 0

    sys.stderr.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if today >= EXCEPTION_EXPIRES:
        sys.stderr.write(f"The temporary npm audit exception expired on {EXCEPTION_EXPIRES}.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
