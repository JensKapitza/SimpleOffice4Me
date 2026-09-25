"""Validate one XRechnung XML with the pinned offline KoSIT runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.xrechnung_validation import MAX_XML_BYTES, validate_xrechnung


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", type=Path)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args(argv)

    try:
        path = args.xml.expanduser().resolve(strict=True)
    except OSError:
        print(json.dumps({"validated": False, "reason": "file_unavailable"}))
        return 2
    if not path.is_file() or path.is_symlink():
        print(json.dumps({"validated": False, "reason": "file_unavailable"}))
        return 2
    if path.stat().st_size > MAX_XML_BYTES:
        print(json.dumps({"validated": False, "reason": "xml_size_invalid"}))
        return 2

    try:
        result = validate_xrechnung(
            path.read_bytes(),
            timeout_seconds=args.timeout,
        )
    except (OSError, RuntimeError, ValueError):
        result = {
            "validated": False,
            "acceptable": False,
            "reason": "validation_failed",
            "exit_code": None,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result.get("validated") is not True:
        return 2
    return 0 if result.get("acceptable") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
