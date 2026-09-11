#!/usr/bin/env python3
"""Repeatable security-evidence gate for the CRA technical file."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REQUIRED = (
    "docs/CRA.md",
    "docs/SECURITY.md",
    "docs/RELEASE_SECURITY_CHECKLIST.md",
    "tools/generate_sbom.py",
    "tools/path_safety_check.py",
    "app/security_controls.py",
    "app/runtime_inventory.py",
    "static/js/security.js",
    "static/js/theme.js",
)

REQUIRED_MARKERS = {
    "app/__init__.py": ("protect_browser_mutation", "Strict-Transport-Security", "theme_preference"),
    "app/auth.py": ("record_login_failure", "GOOGLE_OAUTH_AUTO_PROVISION", "protect_value"),
    "app/admin.py": ("runtime_inventory", "refresh_inventory"),
    ".github/workflows/ci.yml": ("pip_audit", "tools/cra_check.py", "tools/generate_sbom.py"),
}


def _check_path_safety() -> None:
    """Keep known path-injection constructions out of CRA-relevant releases."""
    result = subprocess.run(
        [sys.executable, "tools/path_safety_check.py"],
        check=False,
        text=True,
    )
    if result.returncode:
        raise SystemExit("CRA path safety controls failed")


def main() -> None:
    missing = [path for path in REQUIRED if not Path(path).is_file()]
    if missing:
        raise SystemExit("CRA evidence missing: " + ", ".join(missing))
    incomplete = []
    for path, markers in REQUIRED_MARKERS.items():
        content = Path(path).read_text(encoding="utf-8") if Path(path).is_file() else ""
        incomplete.extend(f"{path}:{marker}" for marker in markers if marker not in content)
    if incomplete:
        raise SystemExit("CRA baseline control missing: " + ", ".join(incomplete))
    _check_path_safety()
    print("CRA evidence baseline OK")


if __name__ == "__main__":
    main()
