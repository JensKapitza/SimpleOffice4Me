#!/usr/bin/env python3
"""Validate repository-level development policy and CI consistency.

This check intentionally avoids third-party dependencies so it can run before
or alongside the normal test suite on every supported Python version.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

REQUIRED_AGENT_MARKERS = (
    "Sicherheit und Datenintegrität",
    "Root Cause vor Symptom-Fix",
    "Best-of-all",
    "Definition of Done",
    "python -m compileall -q app tools",
    "python -m unittest discover -s tests -v",
    "pip-audit",
    "tools/cra_check.py",
    "SBOM",
)

REQUIRED_CI_MARKERS = (
    '"3.10"',
    '"3.14"',
    "python tools/check_project_policy.py .",
    "python tools/check_file_size.py . --limit 1000",
    "python tools/check_function_size.py app tools --limit 300",
    "python -m compileall -q app tools",
    "python -m unittest discover -s tests -v",
    "python tools/check_secret_leaks.py .",
    "python -m pip_audit",
    "python tools/cra_check.py",
    "python tools/generate_sbom.py",
)

CSHARP_AGENT_MARKERS = (
    "MetaBridge",
    "BrabenderCodeAnalysis.ruleset",
    "CA1823",
    "C6259",
    "SX1101",
)


def _read(path: Path, errors: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append(f"missing required file: {path}")
    except (OSError, UnicodeError) as exc:
        errors.append(f"cannot read {path}: {exc}")
    return ""


def _missing_markers(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker not in text]


def validate_agents(root: Path, errors: list[str]) -> None:
    text = _read(root / "AGENTS.md", errors)
    if not text:
        return
    for marker in _missing_markers(text, REQUIRED_AGENT_MARKERS):
        errors.append(f"AGENTS.md is missing policy marker: {marker}")


def validate_copilot_instructions(root: Path, errors: list[str]) -> None:
    text = _read(root / ".github" / "copilot-instructions.md", errors)
    if text and "AGENTS.md" not in text:
        errors.append(".github/copilot-instructions.md must point to AGENTS.md")


def validate_python_requirement(root: Path, errors: list[str]) -> None:
    text = _read(root / "pyproject.toml", errors)
    if not text:
        return
    match = re.search(r'^requires-python\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if match is None:
        errors.append("pyproject.toml does not define requires-python")
        return
    requirement = match.group(1).replace(" ", "")
    if ">=3.10" not in requirement:
        errors.append(
            "pyproject.toml requires-python must retain Python >=3.10 support; "
            f"found {match.group(1)!r}"
        )


def validate_ci(root: Path, errors: list[str]) -> None:
    text = _read(root / ".github" / "workflows" / "ci.yml", errors)
    if not text:
        return
    for marker in _missing_markers(text, REQUIRED_CI_MARKERS):
        errors.append(f"ci.yml is missing required quality gate: {marker}")


def _contains_csharp_project(root: Path) -> bool:
    ignored = {".git", ".venv", "venv", "node_modules", "build", "dist"}
    for path in root.rglob("*.csproj"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if not any(part in ignored for part in relative.parts):
            return True
    return False


def validate_conditional_csharp_policy(root: Path, errors: list[str]) -> None:
    """Only enforce MetaBridge references when a C# project exists in this tree."""
    if not _contains_csharp_project(root):
        return

    for name in ("instruction.md", "BrabenderCodeAnalysis.ruleset"):
        if not (root / name).is_file():
            errors.append(f"C# project detected but required project reference is missing: {name}")

    agents = _read(root / "AGENTS.md", errors)
    if not agents:
        return
    for marker in _missing_markers(agents, CSHARP_AGENT_MARKERS):
        errors.append(f"C# project detected but AGENTS.md is missing marker: {marker}")


def policy_errors(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    validate_agents(root, errors)
    validate_copilot_instructions(root, errors)
    validate_python_requirement(root, errors)
    validate_ci(root, errors)
    validate_conditional_csharp_policy(root, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()

    errors = policy_errors(Path(args.root))
    if not errors:
        print("OK: project policy, Python baseline and CI quality gates are consistent")
        return 0

    print("Project policy consistency errors:")
    for error in errors:
        print(f"- {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
