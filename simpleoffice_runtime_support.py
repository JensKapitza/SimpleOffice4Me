"""Production runtime policy; stdlib-only so installers can check before pip."""
from __future__ import annotations

import platform
import sys

_RUNTIME_PROBLEM_UNSET = object()
_RUNTIME_PROBLEM_CACHE = _RUNTIME_PROBLEM_UNSET
SUPPORTED_SERIES = {(3, 11), (3, 12), (3, 13), (3, 14), (3, 15)}


def runtime_problem() -> str | None:
    version = sys.version_info
    series = tuple(version[:2])
    if platform.python_implementation() != "CPython":
        return "Unterstützt wird CPython; siehe docs/PYTHON_RUNTIME_SUPPORT.md."
    if version.releaselevel != "final":
        return "Python-Vorabversionen sind nicht unterstützt; eine freigegebene finale CPython-Version verwenden."
    if series in SUPPORTED_SERIES:
        return None
    return "Freigegeben sind CPython 3.11–3.15 final; siehe docs/PYTHON_RUNTIME_SUPPORT.md."


def require_supported_runtime() -> None:
    global _RUNTIME_PROBLEM_CACHE
    if _RUNTIME_PROBLEM_CACHE is _RUNTIME_PROBLEM_UNSET:
        _RUNTIME_PROBLEM_CACHE = runtime_problem()
    problem = _RUNTIME_PROBLEM_CACHE
    if problem:
        raise RuntimeError(problem)


if __name__ == "__main__":
    problem = runtime_problem()
    if problem:
        print(problem, file=sys.stderr)
        raise SystemExit(1)
