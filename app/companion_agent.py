"""Lifecycle helpers for the optional SimpleOffice companion agent."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

STATE_VERSION = 1


def _state_path(root: str | Path) -> Path:
    path = Path(root).expanduser().resolve() / ".simpleoffice-meta" / "companion-agent.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_state(root: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _write_state(root: str | Path, value: dict[str, Any]) -> None:
    path = _state_path(root)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def platform_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ:
        return "android"
    return "linux"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=5, check=False)
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def status(root: str | Path) -> dict[str, Any]:
    state = _read_state(root)
    pid = int(state.get("pid") or 0)
    running = _pid_alive(pid)
    return {
        "installed": bool(state.get("installed")),
        "running": running,
        "pid": pid if running else 0,
        "platform": platform_name(),
        "started_at": state.get("started_at", "") if running else "",
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
    command = [sys.executable, "-m", "tools.companion_agent", "--root", str(Path(root).resolve())]
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    state = _read_state(root)
    state.update({"installed": True, "pid": process.pid, "started_at": int(time.time())})
    _write_state(root, state)
    return status(root)


def stop(root: str | Path) -> dict[str, Any]:
    state = _read_state(root)
    pid = int(state.get("pid") or 0)
    if _pid_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T"], timeout=10, check=False, capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    state.update({"pid": 0, "started_at": ""})
    _write_state(root, state)
    return status(root)
