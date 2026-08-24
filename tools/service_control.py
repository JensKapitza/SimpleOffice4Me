#!/usr/bin/env python3
"""Small dependency-free lifecycle manager for local SimpleOffice processes."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "instance" / "run"
ROLES = ("index", "web")


def _linux_start_time(pid: int) -> str:
    try:
        # The comm field may contain spaces and parentheses; split after its
        # final ')' before selecting proc(5) field 22.
        tail = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return tail[19]
    except (OSError, IndexError):
        return ""


def _command_line(pid: int) -> str:
    try:
        if sys.platform.startswith("linux"):
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        if os.name == "nt":
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        else:
            result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _path(role: str) -> Path:
    return RUN_DIR / f"{role}.json"


def register(role: str, pid: int, marker: str) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "role": role, "pid": pid, "marker": marker, "linux_start_time": _linux_start_time(pid)}
    temporary = _path(role).with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(_path(role))


def unregister(role: str, pid: int | None = None) -> None:
    path = _path(role)
    if pid is not None:
        record = read(role)
        if record and record.get("pid") != pid:
            return
    path.unlink(missing_ok=True)


def read(role: str) -> dict[str, object] | None:
    try:
        value = json.loads(_path(role).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def process_matches(record: dict[str, object]) -> bool:
    try:
        pid = int(record["pid"])
        os.kill(pid, 0)
    except (KeyError, TypeError, ValueError, ProcessLookupError, PermissionError, OSError):
        return False
    recorded_start = str(record.get("linux_start_time", ""))
    if recorded_start and _linux_start_time(pid) != recorded_start:
        return False
    marker = str(record.get("marker", ""))
    command = _command_line(pid)
    return bool(marker and command and marker in command)


def running_roles() -> list[str]:
    result: list[str] = []
    for role in ROLES:
        record = read(role)
        if record and process_matches(record):
            result.append(role)
        elif record:
            unregister(role)
    return result


def stop(timeout: float = 20.0) -> bool:
    records = [(role, read(role)) for role in ROLES]
    active = [(role, record) for role, record in records if record and process_matches(record)]
    for role, record in active:
        os.kill(int(record["pid"]), signal.SIGTERM)
        print(f"{role} wird kontrolliert beendet (PID {record['pid']}).")
    deadline = time.monotonic() + timeout
    while active and time.monotonic() < deadline:
        active = [(role, record) for role, record in active if process_matches(record)]
        if active:
            time.sleep(0.1)
    for role, record in records:
        if record and not process_matches(record):
            unregister(role, int(record["pid"]))
    if active:
        print("Nicht rechtzeitig beendet: " + ", ".join(role for role, _ in active), file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me Dienste sicher verwalten")
    parser.add_argument("command", choices=("status", "stop"))
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    if args.command == "status":
        roles = running_roles()
        print("Laufende Dienste: " + ", ".join(roles) if roles else "SimpleOffice4Me ist gestoppt.")
        return 0 if roles else 3
    return 0 if stop(max(1.0, min(args.timeout, 120.0))) else 1


if __name__ == "__main__":
    raise SystemExit(main())
