"""Linux-first virtualization and container integration.

Read operations are intentionally broad. Mutating operations stay narrow and
explicit: VM autostart is delegated to libvirt, Podman autostart is represented
through Quadlet/systemd, and registry configuration is limited to a managed
user-level drop-in owned by SimpleOffice.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path


SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
REGISTRY_RE = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._/-]+)?$")


def _run(args: list[str], timeout: int = 8) -> dict[str, object]:
    executable = shutil.which(args[0])
    if not executable:
        return {"ok": False, "missing": True, "stdout": "", "stderr": "", "returncode": None}
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "NO_COLOR": "1"}
    try:
        result = subprocess.run(
            [executable, *args[1:]], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", timeout=timeout, check=False, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "missing": False, "stdout": "", "stderr": str(exc)[:2000], "returncode": None}
    return {
        "ok": result.returncode == 0,
        "missing": False,
        "stdout": result.stdout[:2_000_000],
        "stderr": result.stderr[:20_000],
        "returncode": result.returncode,
    }


def _json_lines(text: str) -> list[dict[str, object]]:
    text = text.strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        return [payload]
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def libvirt_snapshot() -> dict[str, object]:
    if not shutil.which("virsh"):
        return {"available": False, "domains": [], "networks": [], "pools": []}
    domains_result = _run(["virsh", "list", "--all", "--name"])
    names = [line.strip() for line in str(domains_result["stdout"]).splitlines() if line.strip()] if domains_result["ok"] else []
    domains = []
    for name in names[:200]:
        if not SAFE_NAME.fullmatch(name):
            continue
        state = _run(["virsh", "domstate", name])
        autostart = _run(["virsh", "dominfo", name])
        info = str(autostart["stdout"])
        auto = any(line.split(":", 1)[-1].strip().lower() in {"yes", "ja"} for line in info.splitlines() if line.lower().startswith("autostart"))
        domains.append({"name": name, "state": str(state["stdout"]).strip(), "autostart": auto})
    networks = _run(["virsh", "net-list", "--all", "--name"])
    pools = _run(["virsh", "pool-list", "--all", "--name"])
    return {
        "available": True,
        "domains": domains,
        "networks": [line.strip() for line in str(networks["stdout"]).splitlines() if line.strip()] if networks["ok"] else [],
        "pools": [line.strip() for line in str(pools["stdout"]).splitlines() if line.strip()] if pools["ok"] else [],
    }


def set_vm_autostart(name: str, enabled: bool) -> dict[str, object]:
    name = name.strip()
    if not SAFE_NAME.fullmatch(name):
        raise ValueError("Ungültiger VM-Name")
    args = ["virsh", "autostart", name]
    if not enabled:
        args.append("--disable")
    result = _run(args)
    if not result["ok"]:
        raise RuntimeError(str(result["stderr"] or result["stdout"] or "libvirt autostart failed")[:1000])
    return {"name": name, "autostart": bool(enabled)}


def podman_snapshot() -> dict[str, object]:
    if not shutil.which("podman"):
        return {"available": False, "containers": [], "pods": [], "images": [], "registries": registry_config()}
    containers = _run(["podman", "ps", "-a", "--format", "json"])
    pods = _run(["podman", "pod", "ps", "--format", "json"])
    images = _run(["podman", "images", "--format", "json"])
    return {
        "available": True,
        "containers": _json_lines(str(containers["stdout"])) if containers["ok"] else [],
        "pods": _json_lines(str(pods["stdout"])) if pods["ok"] else [],
        "images": _json_lines(str(images["stdout"])) if images["ok"] else [],
        "registries": registry_config(),
        "quadlet_available": bool(shutil.which("systemctl")),
    }


def _registry_dropin() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser()
    return config_home / "containers" / "registries.conf.d" / "90-simpleoffice.conf"


def registry_config() -> dict[str, object]:
    path = _registry_dropin()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    entries = []
    current: dict[str, object] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line == "[[registry]]":
            if current:
                entries.append(current)
            current = {"location": "", "insecure": False, "blocked": False}
            continue
        if current is None or "=" not in line or line.startswith("#"):
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if key == "location":
            current["location"] = value.strip('"')
        elif key in {"insecure", "blocked"}:
            current[key] = value.lower() == "true"
    if current:
        entries.append(current)
    return {"path": str(path), "managed": entries}


def write_registry_config(entries: list[dict[str, object]]) -> dict[str, object]:
    if len(entries) > 100:
        raise ValueError("Zu viele Registry-Einträge")
    normalized = []
    for entry in entries:
        location = str(entry.get("location", "")).strip()
        if not REGISTRY_RE.fullmatch(location):
            raise ValueError(f"Ungültige Registry: {location}")
        normalized.append({
            "location": location,
            "insecure": bool(entry.get("insecure", False)),
            "blocked": bool(entry.get("blocked", False)),
        })
    path = _registry_dropin()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Managed by SimpleOffice4Me. User-level Podman registry policy.", ""]
    for entry in normalized:
        lines.extend([
            "[[registry]]",
            f'location = "{entry["location"]}"',
            f'insecure = {str(entry["insecure"]).lower()}',
            f'blocked = {str(entry["blocked"]).lower()}',
            "",
        ])
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, path)
    return registry_config()


def host_services_snapshot() -> dict[str, object]:
    return {"libvirt": libvirt_snapshot(), "podman": podman_snapshot()}
