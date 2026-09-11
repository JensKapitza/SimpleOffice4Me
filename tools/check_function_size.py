#!/usr/bin/env python3
"""Fail when a Python function grows beyond the configured line limit."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

DEFAULT_LIMIT = 300
DEFAULT_ROOTS = ("app", "tools")
SKIP_PARTS = {".git", ".venv", "venv", "build", "dist", "__pycache__"}


def iter_python_files(root: Path):
    if root.is_file():
        if root.suffix == ".py":
            yield root
        return
    for path in root.rglob("*.py"):
        if not any(part in SKIP_PARTS for part in path.parts):
            yield path


def function_span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    end_lineno = getattr(node, "end_lineno", None)
    if end_lineno is None:
        return 0
    return end_lineno - node.lineno + 1


def find_oversized_functions(path: Path, limit: int):
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [(str(path), 0, "<parse-error>", 0, str(exc))]

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            span = function_span(node)
            if span > limit:
                violations.append((str(path), node.lineno, node.name, span, ""))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_ROOTS))
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()

    violations = []
    for raw in args.paths:
        root = Path(raw)
        if root.exists():
            for path in iter_python_files(root):
                violations.extend(find_oversized_functions(path, args.limit))

    if violations:
        for path, line, name, span, error in sorted(violations):
            if error:
                print(f"{path}: {error}")
            else:
                print(f"{path}:{line}: {name} spans {span} lines (limit {args.limit})")
        return 1

    print(f"All Python functions are <= {args.limit} lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
