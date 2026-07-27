#!/usr/bin/env python3
"""Minimal, repeatable evidence check for the CRA technical file."""
from __future__ import annotations

from pathlib import Path


REQUIRED = ("docs/CRA.md", "docs/SECURITY.md", "docs/RELEASE_SECURITY_CHECKLIST.md", "tools/generate_sbom.py")


def main() -> None:
    missing = [path for path in REQUIRED if not Path(path).is_file()]
    if missing: raise SystemExit("CRA evidence missing: " + ", ".join(missing))
    print("CRA evidence baseline OK")


if __name__ == "__main__": main()
