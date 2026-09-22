#!/usr/bin/env python3
"""Fail when high-confidence credential material is committed to the repository."""
from __future__ import annotations

import argparse
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
IGNORED_PARTS = {".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", "__pycache__"}
PLACEHOLDER_MARKERS = ("placeholder", "example", "dummy", "changeme", "not-a-real", "test-only", "fake", "sample")

PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    ("github-fine-grained-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,255}\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("openai-api-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)
LITERAL_SECRET = re.compile(
    r"""(?ix)\b(?:password|passwd|secret|token|api[_-]?key|client[_-]?secret|access[_-]?key)\b
        \s*[:=]\s*["']([^"'\s]{24,})["']"""
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str


def _entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def _looks_like_secret_literal(value: str) -> bool:
    lowered = value.casefold()
    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return False
    classes = sum((
        any(ch.islower() for ch in value),
        any(ch.isupper() for ch in value),
        any(ch.isdigit() for ch in value),
        any(not ch.isalnum() for ch in value),
    ))
    return len(value) >= 24 and classes >= 3 and _entropy(value) >= 3.0


def scan_text(text: str, path: str = "<memory>") -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in PATTERNS:
            if pattern.search(line):
                findings.append(Finding(path, number, kind))
        for match in LITERAL_SECRET.finditer(line):
            if _looks_like_secret_literal(match.group(1)):
                findings.append(Finding(path, number, "literal-secret"))
    return findings


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        payload = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in payload:
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_repository(root: Path) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        text = _read_text(path)
        if text is not None:
            findings.extend(scan_text(text, relative.as_posix()))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    findings = scan_repository(Path(args.root))
    if not findings:
        print("OK: no high-confidence committed secrets detected")
        return 0
    print("Potential committed secrets detected:")
    for finding in findings:
        print(f"- {finding.path}:{finding.line}: {finding.kind}")
    print("Remove/rotate real credentials before merging; do not print secret values in CI.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
