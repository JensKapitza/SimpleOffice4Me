"""Reject mutable third-party GitHub Action references in workflow files."""

from __future__ import annotations

import re
from pathlib import Path


_ACTION_USE = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<reference>[^\s#]+)")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def unpinned_action_references(workflow_root: Path) -> list[str]:
    """List workflow action references that can move without a reviewed diff."""
    findings: list[str] = []
    for path in sorted((*workflow_root.rglob("*.yml"), *workflow_root.rglob("*.yaml"))):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            findings.append(f"{path}: unable to read ({type(exc).__name__})")
            continue
        for line_number, line in enumerate(lines, start=1):
            match = _ACTION_USE.match(line)
            if match is None:
                continue
            reference = match.group("reference")
            if reference.startswith("./"):
                continue
            _, separator, revision = reference.rpartition("@")
            if not separator or not _COMMIT_SHA.fullmatch(revision):
                findings.append(f"{path}:{line_number}: {reference}")
    return findings


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    findings = unpinned_action_references(repository_root / ".github" / "workflows")
    if findings:
        print("External GitHub Actions must use full commit SHAs:")
        print("\n".join(findings))
        return 1
    print("All external GitHub Actions are pinned to full commit SHAs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
