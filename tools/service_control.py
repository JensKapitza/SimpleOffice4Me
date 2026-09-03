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
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "instance" / "run"
ROLES = ("index", "web", "sftp")


def _role(role: str) -> str:
    value = str(role or "").strip()
    if value not in ROLES:
        raise ValueError("unknown SimpleOffice service role")
    return value


def _linux_start_time(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        # The comm field may contain spaces and parentheses; split after its
        # final ')' before selecting proc(5) field 22.
        tail = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return tail[19]
    except (OSError, IndexError):
        return ""


def _command_line(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        if sys.platform.startswith("linux"):
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")[:16_384]
        if os.name == "nt":
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", timeout=5, check=False,
            )
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, errors="replace", timeout=5, check=False,
            )
        return result.stdout.strip()[:16_384] if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _path(role: str) -> Path:
    return RUN_DIR / f"{_role(role)}.json"


def register(role: str, pid: int, marker: str) -> None:
    role = _role(role)
    try:
        pid = int(pid)
    except (TypeError, ValueError) as exc:
        raise ValueError("service pid must be a positive integer") from exc
    marker = str(marker or "").strip()
    if pid <= 0 or not marker or len(marker) > 512 or "\x00" in marker:
        raise ValueError("invalid service pid or marker")
    RUN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _path(role)
    if path.is_symlink():
        raise RuntimeError("service state file must not be a symbolic link")
    payload = {
        "version": 1,
        "role": role,
        "pid": pid,
        "marker": marker,
        "linux_start_time": _linux_start_time(pid),
    }
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    raw_descriptor_owned = True
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        raw_descriptor_owned = False
        with handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
    except Exception:
        if raw_descriptor_owned:
            try:
                os.close(descriptor)
            except OSError:
                pass
        temporary.unlink(missing_ok=True)
        raise


def unregister(role: str, pid: int | None = None) -> None:
    path = _path(role)
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if pid is not None:
        record = read(role)
        if record and record.get("pid") != pid:
            return
    path.unlink(missing_ok=True)


def read(role: str) -> dict[str, object] | None:
    path = _path(role)
    if path.is_symlink():
        return None
    try:
        if path.stat().st_size > 64 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1 or value.get("role") != _role(role):
            return None
        pid = int(value.get("pid", 0))
        marker = str(value.get("marker", ""))
        if pid <= 0 or not marker or len(marker) > 512 or "\x00" in marker:
            return None
        value["pid"] = pid
        return value
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def process_matches(record: dict[str, object]) -> bool:
    try:
        pid = int(record["pid"])
        if pid <= 0:
            return False
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
        try:
            os.kill(int(record["pid"]), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue
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
