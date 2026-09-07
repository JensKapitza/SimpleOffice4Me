"""Linux user inventory and cautious account-management primitives."""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
from pathlib import Path

from .document_store import CONTROL_DIR

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
PROTECTED_USERS = {"root", "nobody", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats", "systemd-network", "systemd-timesync", "messagebus", "syslog", "_apt"}


def _run(args: list[str], *, stdin: bytes | None = None, timeout: int = 10) -> dict[str, object]:
    executable = shutil.which(args[0])
    if not executable:
        return {"ok": False, "missing": True, "returncode": None, "stdout": "", "stderr": ""}
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "NO_COLOR": "1"}
    try:
        result = subprocess.run([executable, *args[1:]], input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "missing": False, "returncode": None, "stdout": "", "stderr": str(exc)[:2000]}
    return {"ok": result.returncode == 0, "missing": False, "returncode": result.returncode, "stdout": result.stdout.decode("utf-8", "replace")[:20000], "stderr": result.stderr.decode("utf-8", "replace")[:20000]}


class LinuxUserLinkStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "linux-user-links.json"

    def all(self) -> dict[str, dict[str, object]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        rows = payload.get("users") if isinstance(payload, dict) else None
        return {str(key): value for key, value in rows.items() if isinstance(value, dict)} if isinstance(rows, dict) else {}

    def set_contact(self, username: str, contact_id: str | None) -> dict[str, object]:
        username = validate_username(username)
        rows = self.all()
        if contact_id:
            rows[username] = {"contact_id": str(contact_id)[:160]}
        else:
            rows.pop(username, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "users": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        if os.name == "posix":
            os.chmod(self.path, 0o600)
        return rows.get(username, {})


def validate_username(username: str) -> str:
    username = str(username or "").strip()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Ungültiger Linux-Benutzername")
    return username


def linux_user_snapshot(root: str | Path) -> dict[str, object]:
    if os.name != "posix" or not Path("/etc/passwd").exists():
        return {"available": False, "users": []}
    links = LinuxUserLinkStore(root).all()
    rows = []
    try:
        entries = pwd.getpwall()
    except (OSError, PermissionError):
        entries = []
    for entry in entries:
        username = entry.pw_name
        rows.append({
            "username": username,
            "uid": entry.pw_uid,
            "gid": entry.pw_gid,
            "home": entry.pw_dir,
            "shell": entry.pw_shell,
            "gecos": entry.pw_gecos,
            "system": entry.pw_uid < 1000,
            "protected": username in PROTECTED_USERS or entry.pw_uid == 0,
            "contact_id": str(links.get(username, {}).get("contact_id") or ""),
        })
    return {"available": True, "users": rows}


def create_linux_user(username: str, *, gecos: str = "", shell: str = "/bin/bash", create_home: bool = True) -> dict[str, object]:
    username = validate_username(username)
    if username in PROTECTED_USERS:
        raise ValueError("Geschützter Systembenutzername")
    if shell not in {"/bin/bash", "/bin/sh", "/usr/sbin/nologin", "/bin/false"}:
        raise ValueError("Nicht freigegebene Login-Shell")
    args = ["useradd"]
    if create_home:
        args.append("--create-home")
    args.extend(["--shell", shell, "--comment", " ".join(str(gecos).split())[:160], username])
    result = _run(args)
    if not result["ok"]:
        raise RuntimeError(str(result["stderr"] or result["stdout"] or "useradd failed")[:1000])
    locked = _run(["usermod", "--lock", username])
    if not locked["ok"]:
        raise RuntimeError(str(locked["stderr"] or locked["stdout"] or "user lock failed")[:1000])
    return {"username": username, "created": True, "password_locked": True}


def set_linux_password(username: str, password: str) -> dict[str, object]:
    username = validate_username(username)
    if username in PROTECTED_USERS:
        raise ValueError("Passwort geschützter Systemkonten wird hier nicht geändert")
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024 or "\x00" in password or "\n" in password or "\r" in password:
        raise ValueError("Passwort muss 12 bis 1024 Zeichen lang sein")
    # chpasswd reads the secret from stdin so it never appears in argv/process listings.
    result = _run(["chpasswd"], stdin=f"{username}:{password}\n".encode("utf-8"))
    if not result["ok"]:
        raise RuntimeError(str(result["stderr"] or result["stdout"] or "password change failed")[:1000])
    return {"username": username, "password_changed": True}


def set_linux_user_locked(username: str, locked: bool) -> dict[str, object]:
    username = validate_username(username)
    if username in PROTECTED_USERS:
        raise ValueError("Geschütztes Systemkonto")
    result = _run(["usermod", "--lock" if locked else "--unlock", username])
    if not result["ok"]:
        raise RuntimeError(str(result["stderr"] or result["stdout"] or "usermod failed")[:1000])
    return {"username": username, "locked": bool(locked)}
