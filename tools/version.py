#!/usr/bin/env python3
"""Print the current SimpleOffice4Me application/build identity."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simpleoffice_version import build_info  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me version/build identity")
    parser.add_argument("--json", action="store_true", help="print machine-readable metadata")
    parser.add_argument("--root", default=str(PROJECT_ROOT), help="application checkout/install root")
    args = parser.parse_args()
    info = build_info(Path(args.root))
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
