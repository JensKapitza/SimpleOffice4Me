"""Inventory Font Awesome references in first-party files without counting vendored CSS."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON = re.compile(r"(?<![\w-])(fa-(?:[a-z][\w-]*))\b")
STYLES = {"fa", "fas", "far", "fab", "fal", "fad", "fa-fw", "fa-lg", "fa-sm", "fa-xs", "fa-2x", "fa-3x", "fa-4x", "fa-5x", "fa-spin", "fa-pulse", "fa-stack", "fa-stack-1x", "fa-stack-2x", "fa-inverse", "fa-border", "fa-ul", "fa-li", "fa-pull-left", "fa-pull-right", "fa-solid", "fa-regular", "fa-brands", "fa-light", "fa-duotone", "fa-thin", "fa-sharp"}
EXTENSIONS = {".html", ".jinja", ".jinja2", ".js", ".ts", ".css", ".py", ".vue", ".jsx", ".tsx"}
SKIP = ("static/fontawesome-free-", "static/bootstrap-", "node_modules/", "vendor/", "static/vendor/", "dist/", "build/")


def inventory() -> dict:
    paths = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    icons = defaultdict(list)
    styles = defaultdict(list)
    for name in paths:
        if name == "tools/inventory_fontawesome_usage.py" or name.startswith(SKIP) or Path(name).suffix.lower() not in EXTENSIONS:
            continue
        try:
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for number, line in enumerate(lines, 1):
            for token in set(ICON.findall(line)):
                (styles if token in STYLES else icons)[token].append(f"{name}:{number}")
            if re.search(r"(?<![\w-])(?:fas|far|fab|fal|fad|fa-solid|fa-regular|fa-brands|fa-light|fa-duotone|fa-thin|fa-sharp)(?![\w-])", line):
                styles["style-prefix"].append(f"{name}:{number}")
    return {"icons": dict(sorted(icons.items())), "modifiers": dict(sorted(styles.items())),
            "icon_count": len(icons), "reference_count": sum(map(len, icons.values())),
            "note": "Locations are candidates: class tokens and non-class occurrences are included for manual review."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Machine-readable complete inventory")
    args = parser.parse_args()
    data = inventory()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"{data['icon_count']} distinct icon candidates, {data['reference_count']} references")
        for icon, locations in data["icons"].items():
            print(f"{icon} ({len(locations)}): {', '.join(locations)}")
        print("Modifiers:", ", ".join(data["modifiers"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
