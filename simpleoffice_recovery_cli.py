"""Runtime-policy recovery entry point.

The sentinel is process-local and consumed by app.__init__; it is not an
environment-variable bypass for normal application startup.
"""
from __future__ import annotations

import sys


def main() -> int | None:
    sys._simpleoffice_recovery_import = True
    try:
        from app.v2.recovery_cli import main as recovery_main
    finally:
        if hasattr(sys, "_simpleoffice_recovery_import"):
            del sys._simpleoffice_recovery_import
    return recovery_main()


if __name__ == "__main__":
    raise SystemExit(main())
