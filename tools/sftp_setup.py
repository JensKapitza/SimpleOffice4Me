#!/usr/bin/env python3
"""Safe first-run helper for the optional, shell-free SFTP service."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEY = ROOT / "instance" / "sftp_host_rsa_key"


def key_path() -> Path:
    return Path(os.environ.get("SIMPLEOFFICE_SFTP_HOST_KEY", str(DEFAULT_KEY))).expanduser().resolve()


def dependency():
    try:
        import paramiko
        return paramiko
    except ImportError as exc:
        raise RuntimeError("Paramiko fehlt. Installieren mit: python -m pip install '.[sftp]'") from exc


def validate(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"SFTP-Hostschlüssel fehlt: {path}. Einmalig --init ausführen.")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(f"Hostschlüssel ist zu offen. Ausführen: chmod 600 {path}")


def initialize(path: Path) -> None:
    library = dependency()
    if path.exists():
        validate(path)
        print(f"Vorhandener Hostschlüssel bleibt unverändert: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    library.RSAKey.generate(3072).write_private_key_file(str(path))
    if os.name != "nt":
        path.chmod(0o600)
    print(f"Neuer SFTP-Hostschlüssel erzeugt: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="SimpleOffice SFTP einrichten und starten")
    parser.add_argument("action", choices=("status", "init", "run"), nargs="?", default="status")
    args = parser.parse_args()
    path = key_path()
    try:
        if args.action == "init":
            initialize(path)
        else:
            dependency(); validate(path)
        print(f"SFTP bereit: Schlüssel={path}, Bind={os.environ.get('SIMPLEOFFICE_SFTP_BIND', '127.0.0.1')}:{os.environ.get('SIMPLEOFFICE_SFTP_PORT', '2222')}")
        if args.action == "run":
            os.environ["SIMPLEOFFICE_SFTP_HOST_KEY"] = str(path)
            sys.path.insert(0, str(ROOT))
            from app.sftp_server import serve
            from tools.service_control import register, unregister
            register("sftp", os.getpid(), "sftp_setup")
            try:
                serve()
            finally:
                unregister("sftp", os.getpid())
        return 0
    except RuntimeError as exc:
        print(f"SFTP nicht bereit: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
