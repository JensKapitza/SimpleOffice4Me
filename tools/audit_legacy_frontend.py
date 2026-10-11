"""Inventory tracked legacy frontend assets and references."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ("static/bootstrap-5.0.1-dist/", "static/fontawesome-free-5.15.3-web/")
TEXT = {".html", ".jinja", ".jinja2", ".css", ".js", ".json", ".py", ".md", ".yml", ".yaml", ".webmanifest"}


def main() -> int:
    paths = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    assets = sorted(p for p in paths if p.startswith(LEGACY))
    references = []
    for name in paths:
        if name.startswith(LEGACY) or Path(name).suffix.lower() not in TEXT:
            continue
        try:
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for number, line in enumerate(lines, 1):
            if any(prefix.rstrip("/") in line for prefix in LEGACY):
                references.append((name, number))
    print(f"Legacy assets: {len(assets)}")
    for name in assets:
        print(f"ASSET {name}")
    print(f"Legacy references: {len(references)}")
    for name, number in references:
        print(f"REFERENCE {name}:{number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
