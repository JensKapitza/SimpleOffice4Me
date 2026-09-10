"""Fail CI when project-owned maintainable source files exceed the line limit.

Only explicit third-party distributions and generated/build directories are
excluded; project-owned application code and tests remain subject to the limit.
"""
from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_LIMIT = 1000
SOURCE_SUFFIXES = {".py", ".js", ".ts", ".css", ".html", ".jinja", ".jinja2"}
IGNORED_PARTS = {".git", ".venv", "venv", "node_modules", "vendor", "dist", "build"}
VENDORED_PREFIXES = {
    Path("static/bootstrap-5.0.1-dist"),
    Path("static/fontawesome-free-5.15.3-web"),
}


def _is_vendored(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return any(relative == prefix or prefix in relative.parents for prefix in VENDORED_PREFIXES)


def oversized_files(root: Path, limit: int = DEFAULT_LIMIT) -> list[tuple[Path, int]]:
    root = root.resolve()
    result: list[tuple[Path, int]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if any(part in IGNORED_PARTS for part in path.parts) or _is_vendored(path, root):
            continue
        try:
            count = sum(1 for _ in path.open("r", encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if count > limit:
            result.append((path.relative_to(root), count))
    return sorted(result, key=lambda item: (-item[1], str(item[0])))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    violations = oversized_files(Path(args.root), args.limit)
    if not violations:
        print(f"OK: no project-owned source file exceeds {args.limit} lines")
        return 0
    print(f"Project-owned source files exceeding {args.limit} lines:")
    for path, count in violations:
        print(f"{count:5d}  {path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())