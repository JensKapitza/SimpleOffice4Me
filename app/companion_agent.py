"""Lifecycle helpers for the optional SimpleOffice companion agent."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

STATE_VERSION = 3
HEARTBEAT_MAX_AGE_SECONDS = 15


def _meta_dir(root: str | Path) -> Path:
    path = Path(root).expanduser().resolve() / ".simpleoffice-meta"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(root: str | Path) -> Path:
    return _meta_dir(root) / "companion-agent.json"


def _heartbeat_path(root: str | Path) -> Path:
    return _meta_dir(root) / "companion-agent-heartbeat.json"


def _stop_path(root: str | Path) -> Path:
    return _meta_dir(root) / "companion-agent-stop.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _read_state(root: str | Path) -> dict[str, Any]:
    return _read_json(_state_path(root))


def _write_state(root: str | Path, value: dict[str, Any]) -> None:
    _write_json(_state_path(root), value)


def platform_name() -> str:
    if "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ:
        return "android"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _heartbeat_matches(root: str | Path, state: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    heartbeat = _read_json(_heartbeat_path(root))
    token = str(state.get("instance_token") or "")
    try:
        pid = int(state.get("pid") or 0)
        heartbeat_pid = int(heartbeat.get("pid") or 0)
        heartbeat_at = int(heartbeat.get("heartbeat_at") or 0)
    except (TypeError, ValueError):
        return False, heartbeat
    age = int(time.time()) - heartbeat_at
    matches = bool(
        token
        and heartbeat.get("instance_token") == token
        and heartbeat_pid == pid
        and 0 <= age <= HEARTBEAT_MAX_AGE_SECONDS
        and _pid_alive(pid)
    )
    return matches, heartbeat


def status(root: str | Path) -> dict[str, Any]:
    state = _read_state(root)
    running, heartbeat = _heartbeat_matches(root, state)
    return {
        "installed": bool(state.get("installed")),
        "running": running,
        "pid": int(state.get("pid") or 0) if running else 0,
        "platform": platform_name(),
        "started_at": heartbeat.get("started_at", "") if running else "",
        "heartbeat_at": heartbeat.get("heartbeat_at", 0) if running else 0,
        "stop_pending": bool(state.get("stop_pending")) if running else False,
    }


def install(root: str | Path) -> dict[str, Any]:
    state = _read_state(root)
    state.update({"version": STATE_VERSION, "installed": True})
    _write_state(root, state)
    return status(root)


def start(root: str | Path) -> dict[str, Any]:
    current = status(root)
    if not current["installed"]:
        raise ValueError("Companion-Agent ist nicht installiert")
    if current["running"]:
        return current

    root_path = Path(root).expanduser().resolve()
    instance_token = secrets.token_urlsafe(32)
    for stale_path in (_stop_path(root_path), _heartbeat_path(root_path)):
        try:
            stale_path.unlink()
        except FileNotFoundError:
            pass

    command = [
        sys.executable,
        "-m",
        "tools.background_worker",
        "--root",
        str(root_path),
        "--token",
        instance_token,
    ]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(Path(__file__).resolve().parents[1]),
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)
    state = _read_state(root_path)
    state.update(
        {
            "version": STATE_VERSION,
            "installed": True,
            "pid": process.pid,
            "instance_token": instance_token,
            "stop_pending": False,
        }
    )
    _write_state(root_path, state)

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = status(root_path)
        if current["running"]:
            return current
        if process.poll() is not None:
            break
        time.sleep(0.1)

    state = _read_state(root_path)
    state.update({"pid": 0, "instance_token": "", "stop_pending": False})
    _write_state(root_path, state)
    raise RuntimeError("Companion-Agent konnte nicht gestartet werden")


def stop(root: str | Path) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    state = _read_state(root_path)
    running, _heartbeat = _heartbeat_matches(root_path, state)
    if not running:
        state.update({"pid": 0, "instance_token": "", "stop_pending": False})
        _write_state(root_path, state)
        return status(root_path)

    token = str(state.get("instance_token") or "")
    _write_json(
        _stop_path(root_path),
        {"instance_token": token, "requested_at": int(time.time())},
    )
    state["stop_pending"] = True
    _write_state(root_path, state)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = status(root_path)
        if not current["running"]:
            state = _read_state(root_path)
            state.update({"pid": 0, "instance_token": "", "stop_pending": False})
            _write_state(root_path, state)
            return status(root_path)
        time.sleep(0.1)

    return status(root_path)
