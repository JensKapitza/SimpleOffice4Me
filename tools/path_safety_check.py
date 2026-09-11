#!/usr/bin/env python3
"""Fail CI on known unsafe filesystem path construction patterns.

This is intentionally conservative.  It does not replace CodeQL; it protects
SimpleOffice4Me's local security boundary from regressions between CodeQL runs.
Untrusted/request/metadata paths must be resolved through app.safe_paths before
filesystem I/O.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (ROOT / "app", ROOT / "tools")

# These patterns represent path constructions that have repeatedly produced
# CodeQL py/path-injection findings in this repository.  Do not suppress a
# match; replace the construction with resolve_under/resolve_file_under/
# resolve_directory_under/resolve_for_write_under or safe_filename.
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "metadata-last-path-join",
        re.compile(
            r"(?:self\.root|\broot|_root\(\)|_store\(\)\.root)\s*/\s*"
            r"(?:str\()?\s*(?:document|metadata|snapshot)\.get\([\"']last_path[\"']"
        ),
    ),
    (
        "request-path-to-pathlib",
        re.compile(
            r"Path\(\s*(?:request\.(?:args|form|values)\.get\(|restore_path\b)"
        ),
    ),
    (
        "stored-payload-path-join",
        re.compile(
            r"(?:self\.)?(?:root|control|directory|media_directory)\s*/\s*"
            r"(?:str\()?\s*(?:row|item|record|payload|target)\s*\[\s*[\"'](?:path|payload_path|relative_path)[\"']"
        ),
    ),
)

# Files implementing the sanitizer itself necessarily manipulate path strings.
EXCLUDED = {
    ROOT / "app" / "safe_paths.py",
    ROOT / "tools" / "path_safety_check.py",
}


def source_files():
    for base in SCAN_ROOTS:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if path in EXCLUDED or "__pycache__" in path.parts:
                continue
            yield path


def main() -> None:
    findings: list[str] = []
    for path in source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(f"{path.relative_to(ROOT)}: cannot inspect: {exc}")
            continue
        for rule_name, pattern in RULES:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                excerpt = text.splitlines()[line - 1].strip()[:220]
                findings.append(
                    f"{path.relative_to(ROOT)}:{line}: {rule_name}: {excerpt}"
                )
    if findings:
        print("CRA path safety gate failed. Route filesystem paths through app.safe_paths:")
        for finding in findings:
            print(f" - {finding}")
        raise SystemExit(1)
    print("CRA path safety gate OK")


if __name__ == "__main__":
    main()
