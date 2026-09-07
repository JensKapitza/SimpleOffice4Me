"""Cross-platform system overview with Linux-first storage and network diagnostics.

The module deliberately separates *observation* from *mutation*.  System state is
collected with code-owned command lines only.  Storage layout helpers create
reviewable plans but do not execute destructive commands.  This keeps the web
UI useful on Windows and Android while allowing Linux to expose substantially
more detail without turning HTTP parameters into shell commands.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

from .document_store import CONTROL_DIR


ROLE_VALUES = {"work", "backup", "archive", "scratch"}
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,47}$")

COCKPIT_PARITY = (
    ("overview", "Systemübersicht und Hardware", "partial"),
    ("storage", "Storage, RAID, LVM und Verschlüsselung", "partial"),
    ("network", "Netzwerkgeräte, Adressen und Routen", "partial"),
    ("firewall", "Firewall", "planned"),
    ("services", "systemd-Dienste", "planned"),
    ("logs", "Systemprotokolle / Journal", "partial"),
    ("updates", "Software-Updates", "partial"),
    ("users", "Benutzerverwaltung", "partial"),
    ("terminal", "Terminal", "planned"),
    ("virtual-machines", "Virtuelle Maschinen", "planned"),
    ("containers", "Podman/Container", "planned"),
    ("metrics", "CPU, RAM, Netzwerk- und Storage-Metriken", "planned"),
    ("multi-host", "Mehrere Server / Instanzen", "planned"),
)

QNAP_PARITY = (
    ("storage", "Speicherpools, RAID und Datenträger", "partial"),
    ("health", "Kapazität, Ausfall- und Backup-Warnungen", "partial"),
    ("smart", "SMART / Laufwerkszustand", "planned"),
    ("shares", "Freigaben", "planned"),
    ("snapshots", "Snapshots", "planned"),
    ("backup", "Backup und Replikation", "partial"),
    ("network", "Netzwerk", "partial"),
    ("services", "Dienste und Anwendungen", "planned"),
    ("notifications", "Systemmeldungen", "partial"),
)

LINUX_TOOLS = (
    ("lsblk", "Datenträger"),
    ("findmnt", "Dateisysteme"),
    ("mdadm", "Linux Software-RAID"),
    ("pvs", "LVM Physical Volumes"),
    ("vgs", "LVM Volume Groups"),
    ("lvs", "LVM Logical Volumes"),
    ("cryptsetup", "LUKS-Verschlüsselung"),
    ("udisksctl", "UDisks2 / Storaged"),
    ("smartctl", "SMART"),
    ("ip", "Netzwerkstatus"),
    ("nmcli", "NetworkManager"),
    ("nft", "nftables"),
    ("firewall-cmd", "firewalld"),
    ("ufw", "UFW"),
    ("systemctl", "systemd"),
    ("journalctl", "Journal"),
    ("podman", "Podman"),
    ("virsh", "libvirt"),
)


def platform_kind() -> str:
    if os.name == "nt":
        return "windows"
    if os.environ.get("ANDROID_ROOT") or os.environ.get("ANDROID_DATA") or os.environ.get("TERMUX_VERSION"):
        return "android"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def _safe_text(value: object, limit: int = 1000) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def _run(command: tuple[str, ...], timeout: int = 5) -> dict[str, object]:
    """Run a fixed command without a shell and return a bounded result."""
    executable = shutil.which(command[0])
    if not executable:
        return {"ok": False, "missing": True, "stdout": "", "stderr": "", "returncode": None}
    env = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
    }
    try:
        result = subprocess.run(
            (executable, *command[1:]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "missing": False,
            "stdout": "",
            "stderr": _safe_text(exc, 500),
            "returncode": None,
        }
    return {
        "ok": result.returncode == 0,
        "missing": False,
        "stdout": result.stdout[:2_000_000],
        "stderr": result.stderr[:20_000],
        "returncode": result.returncode,
    }


def _json_command(command: tuple[str, ...]) -> object | None:
    result = _run(command)
    if not result["ok"]:
        return None
    try:
        return json.loads(str(result["stdout"]))
    except (TypeError, json.JSONDecodeError):
        return None


def _tool_inventory() -> list[dict[str, object]]:
    rows = []
    for command, label in LINUX_TOOLS:
        path = shutil.which(command)
        rows.append({"command": command, "label": label, "available": bool(path)})
    return rows


def _flatten_blocks(nodes: Iterable[dict[str, object]], depth: int = 0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for node in nodes:
        mountpoints = node.get("mountpoints")
        if not isinstance(mountpoints, list):
            mountpoint = node.get("mountpoint")
            mountpoints = [mountpoint] if mountpoint else []
        row = {
            "name": _safe_text(node.get("name"), 120),
            "path": _safe_text(node.get("path"), 300),
            "type": _safe_text(node.get("type"), 80),
            "size": int(node.get("size") or 0) if str(node.get("size") or "0").isdigit() else 0,
            "fstype": _safe_text(node.get("fstype"), 80),
            "label": _safe_text(node.get("label"), 160),
            "uuid": _safe_text(node.get("uuid"), 160),
            "model": _safe_text(node.get("model"), 200),
            "serial": _safe_text(node.get("serial"), 200),
            "transport": _safe_text(node.get("tran"), 80),
            "state": _safe_text(node.get("state"), 80),
            "mountpoints": [_safe_text(item, 500) for item in mountpoints if item],
            "depth": depth,
        }
        rows.append(row)
        children = node.get("children")
        if isinstance(children, list):
            rows.extend(_flatten_blocks(children, depth + 1))
    return rows


def _linux_blocks() -> list[dict[str, object]]:
    fields = "NAME,PATH,TYPE,SIZE,FSTYPE,MOUNTPOINTS,LABEL,UUID,MODEL,SERIAL,ROTA,TRAN,STATE"
    payload = _json_command(("lsblk", "--json", "--bytes", "--output", fields))
    if not isinstance(payload, dict) or not isinstance(payload.get("blockdevices"), list):
        fallback = "NAME,PATH,TYPE,SIZE,FSTYPE,MOUNTPOINT,LABEL,UUID,MODEL,SERIAL"
        payload = _json_command(("lsblk", "--json", "--bytes", "--output", fallback))
    if not isinstance(payload, dict) or not isinstance(payload.get("blockdevices"), list):
        return []
    return _flatten_blocks(payload["blockdevices"])


def _linux_mounts() -> list[dict[str, object]]:
    payload = _json_command(
        ("findmnt", "--json", "--bytes", "--output", "TARGET,SOURCE,FSTYPE,SIZE,USED,AVAIL,USE%")
    )
    filesystems = payload.get("filesystems") if isinstance(payload, dict) else None
    rows: list[dict[str, object]] = []
    if isinstance(filesystems, list):
        for item in filesystems:
            target = _safe_text(item.get("target"), 600)
            if not target:
                continue
            used_percent = str(item.get("use%") or "").replace("%", "")
            try:
                percent = int(float(used_percent)) if used_percent else None
            except ValueError:
                percent = None
            rows.append({
                "target": target,
                "source": _safe_text(item.get("source"), 600),
                "fstype": _safe_text(item.get("fstype"), 80),
                "size": _number(item.get("size")),
                "used": _number(item.get("used")),
                "available": _number(item.get("avail")),
                "used_percent": percent,
            })
    return rows


def _number(value: object) -> int:
    try:
        return max(0, int(float(str(value or "0"))))
    except (TypeError, ValueError):
        return 0


def _lvm_report(command: str, fields: str, key: str) -> list[dict[str, str]]:
    payload = _json_command(
        (command, "--reportformat", "json", "--units", "b", "--nosuffix", "-o", fields)
    )
    if not isinstance(payload, dict):
        return []
    reports = payload.get("report")
    if not isinstance(reports, list):
        return []
    rows: list[dict[str, str]] = []
    for report in reports:
        values = report.get(key) if isinstance(report, dict) else None
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict):
                rows.append({str(k): _safe_text(v, 500) for k, v in value.items()})
    return rows


def _linux_lvm() -> dict[str, list[dict[str, str]]]:
    return {
        "pvs": _lvm_report("pvs", "pv_name,pv_size,pv_free,vg_name,pv_attr", "pv"),
        "vgs": _lvm_report("vgs", "vg_name,vg_size,vg_free,pv_count,lv_count,vg_attr", "vg"),
        "lvs": _lvm_report("lvs", "vg_name,lv_name,lv_size,lv_attr,segtype,devices", "lv"),
    }


def _linux_mdraid() -> dict[str, object]:
    try:
        mdstat = Path("/proc/mdstat").read_text(encoding="utf-8", errors="replace")[:20000]
    except OSError:
        mdstat = ""
    degraded = False
    arrays: list[str] = []
    for line in mdstat.splitlines():
        if " : " in line and line.lstrip().startswith("md"):
            arrays.append(_safe_text(line, 600))
        status_match = re.search(r"\[([U_]+)\]", line)
        if status_match and "_" in status_match.group(1):
            degraded = True
    scan = _run(("mdadm", "--detail", "--scan"))
    return {
        "raw": mdstat,
        "arrays": arrays,
        "degraded": degraded,
        "scan": str(scan["stdout"]).strip()[:12000] if scan["ok"] else "",
    }


def _linux_network() -> dict[str, object]:
    addresses = _json_command(("ip", "-j", "address", "show"))
    routes = _json_command(("ip", "-j", "route", "show"))
    nmcli = _run(("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"))
    devices = []
    if isinstance(addresses, list):
        for item in addresses:
            if not isinstance(item, dict):
                continue
            addr_info = item.get("addr_info") if isinstance(item.get("addr_info"), list) else []
            devices.append({
                "name": _safe_text(item.get("ifname"), 120),
                "state": _safe_text(item.get("operstate"), 80),
                "mtu": _number(item.get("mtu")),
                "mac": _safe_text(item.get("address"), 120),
                "addresses": [
                    {
                        "family": _safe_text(addr.get("family"), 30),
                        "address": _safe_text(addr.get("local"), 160),
                        "prefix": _number(addr.get("prefixlen")),
                    }
                    for addr in addr_info
                    if isinstance(addr, dict) and addr.get("local")
                ],
            })
    return {
        "devices": devices,
        "routes": routes if isinstance(routes, list) else [],
        "networkmanager": str(nmcli["stdout"]).strip().splitlines()[:100] if nmcli["ok"] else [],
    }


def _powershell_json(script: str) -> object | None:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if not executable:
        return None
    result = _run((Path(executable).name, "-NoProfile", "-NonInteractive", "-Command", script))
    if not result["ok"]:
        return None
    try:
        return json.loads(str(result["stdout"]))
    except (TypeError, json.JSONDecodeError):
        return None


def _windows_snapshot() -> dict[str, object]:
    disks = _powershell_json(
        "Get-Disk | Select-Object Number,FriendlyName,SerialNumber,BusType,HealthStatus,OperationalStatus,Size,PartitionStyle,IsBoot,IsSystem | ConvertTo-Json -Depth 3"
    )
    volumes = _powershell_json(
        "Get-Volume | Select-Object DriveLetter,FileSystemLabel,FileSystem,HealthStatus,Size,SizeRemaining,Path | ConvertTo-Json -Depth 3"
    )
    network = _powershell_json(
        "Get-NetIPConfiguration | Select-Object InterfaceAlias,InterfaceDescription,IPv4Address,IPv6Address,IPv4DefaultGateway | ConvertTo-Json -Depth 5"
    )
    return {
        "blocks": _as_list(disks),
        "mounts": _as_list(volumes),
        "network": {"devices": _as_list(network), "routes": [], "networkmanager": []},
        "lvm": {"pvs": [], "vgs": [], "lvs": []},
        "mdraid": {"raw": "", "arrays": [], "degraded": False, "scan": ""},
    }


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _document_usage(document_root: Path) -> dict[str, object]:
    path = document_root.resolve()
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {"path": str(path), "total": 0, "used": 0, "free": 0, "used_percent": None}
    percent = int(round((usage.used / usage.total) * 100)) if usage.total else 0
    return {
        "path": str(path),
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "used_percent": percent,
    }


def system_snapshot(document_root: str | Path) -> dict[str, object]:
    root = Path(document_root).expanduser().resolve()
    kind = platform_kind()
    base: dict[str, object] = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": {
            "kind": kind,
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "document_usage": _document_usage(root),
        "tools": _tool_inventory() if kind in {"linux", "android"} else [],
    }
    if kind == "windows":
        base.update(_windows_snapshot())
    elif kind in {"linux", "android"}:
        base.update({
            "blocks": _linux_blocks(),
            "mounts": _linux_mounts(),
            "network": _linux_network(),
            "lvm": _linux_lvm() if kind == "linux" else {"pvs": [], "vgs": [], "lvs": []},
            "mdraid": _linux_mdraid() if kind == "linux" else {"raw": "", "arrays": [], "degraded": False, "scan": ""},
        })
    else:
        base.update({
            "blocks": [], "mounts": [], "network": {"devices": [], "routes": [], "networkmanager": []},
            "lvm": {"pvs": [], "vgs": [], "lvs": []},
            "mdraid": {"raw": "", "arrays": [], "degraded": False, "scan": ""},
        })
    base["capabilities"] = capabilities(base)
    return base


def capabilities(snapshot: dict[str, object]) -> dict[str, object]:
    kind = str(snapshot.get("platform", {}).get("kind", "other")) if isinstance(snapshot.get("platform"), dict) else "other"
    available = {
        str(row.get("command")): bool(row.get("available"))
        for row in snapshot.get("tools", [])
        if isinstance(row, dict)
    }
    return {
        "storage_read": kind in {"linux", "android", "windows"},
        "network_read": kind in {"linux", "android", "windows"},
        "storage_linux_full": kind == "linux",
        "mdadm": kind == "linux" and available.get("mdadm", False),
        "lvm": kind == "linux" and all(available.get(name, False) for name in ("pvs", "vgs", "lvs")),
        "luks": kind == "linux" and available.get("cryptsetup", False),
        "networkmanager": kind == "linux" and available.get("nmcli", False),
        "write_actions": False,
        "write_reason": "Destruktive Aktionen sind in dieser Ausbaustufe absichtlich nur als prüfbarer Plan verfügbar.",
    }


class StorageRoleStore:
    """Persist administrator labels for work, backup and archive storage."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "system-storage-roles.json"

    def all(self) -> list[dict[str, str]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = payload.get("roles") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            identifier = _safe_text(row.get("identifier"), 500)
            role = _safe_text(row.get("role"), 40)
            if identifier and role in ROLE_VALUES:
                result.append({
                    "identifier": identifier,
                    "role": role,
                    "group": _safe_text(row.get("group"), 80) or "default",
                    "label": _safe_text(row.get("label"), 160),
                    "updated_at": _safe_text(row.get("updated_at"), 80),
                })
        return result

    def set(self, identifier: str, role: str, group: str = "default", label: str = "") -> None:
        identifier = " ".join(identifier.split()).strip()
        role = role.strip().lower()
        group = " ".join(group.split()).strip()[:80] or "default"
        label = " ".join(label.split()).strip()[:160]
        if not identifier or len(identifier) > 500:
            raise ValueError("Ungültige Laufwerks- oder Mount-Kennung")
        if role not in ROLE_VALUES:
            raise ValueError("Unbekannte Speicherrolle")
        rows = [row for row in self.all() if row["identifier"] != identifier]
        rows.append({
            "identifier": identifier,
            "role": role,
            "group": group,
            "label": label,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        self._write(rows)

    def remove(self, identifier: str) -> None:
        identifier = " ".join(identifier.split()).strip()
        self._write([row for row in self.all() if row["identifier"] != identifier])

    def _write(self, rows: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "roles": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


def _known_identifiers(snapshot: dict[str, object]) -> set[str]:
    values: set[str] = set()
    for row in snapshot.get("blocks", []):
        if not isinstance(row, dict):
            continue
        for key in ("path", "name"):
            value = _safe_text(row.get(key), 500)
            if value:
                values.add(value)
        for mountpoint in row.get("mountpoints", []) if isinstance(row.get("mountpoints"), list) else []:
            if mountpoint:
                values.add(str(mountpoint))
    for row in snapshot.get("mounts", []):
        if not isinstance(row, dict):
            continue
        for key in ("target", "source", "Path", "DriveLetter"):
            value = _safe_text(row.get(key), 500)
            if value:
                values.add(value)
    return values


def health_checks(snapshot: dict[str, object], roles: list[dict[str, str]]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    usage = snapshot.get("document_usage") if isinstance(snapshot.get("document_usage"), dict) else {}
    percent = usage.get("used_percent") if isinstance(usage, dict) else None
    if isinstance(percent, int):
        if percent >= 95:
            checks.append({"severity": "critical", "code": "document-storage-full", "message": f"Arbeitsbereich ist zu {percent}% belegt. Sofort Speicher freigeben oder erweitern."})
        elif percent >= 85:
            checks.append({"severity": "warning", "code": "document-storage-low", "message": f"Arbeitsbereich ist zu {percent}% belegt. Speichererweiterung einplanen."})
    mdraid = snapshot.get("mdraid") if isinstance(snapshot.get("mdraid"), dict) else {}
    if mdraid.get("degraded"):
        checks.append({"severity": "critical", "code": "raid-degraded", "message": "Mindestens ein Linux-Software-RAID ist degradiert. Redundanz ist eingeschränkt."})

    known = _known_identifiers(snapshot)
    groups: dict[str, set[str]] = {}
    for role in roles:
        identifier = role.get("identifier", "")
        group = role.get("group", "default") or "default"
        groups.setdefault(group, set()).add(role.get("role", ""))
        if identifier not in known:
            severity = "critical" if role.get("role") in {"work", "backup"} else "warning"
            checks.append({
                "severity": severity,
                "code": "storage-role-missing",
                "message": f"Markierter Speicher „{role.get('label') or identifier}“ ({role.get('role')}) ist aktuell nicht sichtbar.",
            })
    for group, values in groups.items():
        if "backup" in values and "work" not in values:
            checks.append({"severity": "warning", "code": "backup-without-work", "message": f"Backup-Gruppe „{group}“ hat keinen markierten Arbeitsbereich."})
        if "work" in values and "backup" not in values:
            checks.append({"severity": "warning", "code": "work-without-backup", "message": f"Arbeitsbereich der Gruppe „{group}“ hat kein markiertes Backup-Ziel."})
    if not roles:
        checks.append({"severity": "info", "code": "roles-unconfigured", "message": "Noch keine Laufwerke als Arbeitsbereich, Backup, Archiv oder temporärer Speicher markiert."})
    if not checks:
        checks.append({"severity": "ok", "code": "storage-ok", "message": "Keine offensichtlichen Storage-Probleme erkannt."})
    return sorted(checks, key=lambda row: SEVERITY_ORDER.get(row["severity"], 9))


def validate_device_path(value: str) -> str:
    value = value.strip()
    if not value.startswith("/dev/") or len(value) > 300:
        raise ValueError("Es sind nur Linux-Geräte unter /dev erlaubt")
    parts = Path(value).parts
    if ".." in parts or any(character in value for character in "\x00\r\n\t ;|&$`><"):
        raise ValueError("Ungültiger Gerätepfad")
    return value


def storage_plan(profile: str, devices: Iterable[str], name: str = "data", encrypted: bool = True) -> dict[str, object]:
    """Build a review-only storage plan.  No command returned here is executed."""
    selected = []
    for raw in devices:
        device = validate_device_path(raw)
        if device not in selected:
            selected.append(device)
    if not NAME_RE.fullmatch(name or ""):
        raise ValueError("Der Poolname darf nur Buchstaben, Zahlen, Punkt, Unterstrich und Bindestrich enthalten")
    if profile == "raid1-lvm":
        if len(selected) != 2:
            raise ValueError("RAID1 benötigt genau zwei ausgewählte Geräte")
        md = f"/dev/md/so-{name}"
        vg = f"so_{name.replace('-', '_').replace('.', '_')}"
        block = md
        commands: list[list[str]] = [["mdadm", "--create", md, "--metadata=1.2", "--level=1", "--raid-devices=2", *selected]]
        layers = ["RAID1"]
        if encrypted:
            crypt = f"so-{name}-crypt"
            commands.extend([
                ["cryptsetup", "luksFormat", "--type", "luks2", md],
                ["cryptsetup", "open", md, crypt],
            ])
            block = f"/dev/mapper/{crypt}"
            layers.append("LUKS2")
        commands.extend([
            ["pvcreate", block],
            ["vgcreate", vg, block],
            ["lvcreate", "--name", "data", "--extents", "100%FREE", vg],
            ["mkfs.ext4", "-L", f"so-{name}", f"/dev/{vg}/data"],
        ])
        layers.extend(["LVM", "ext4"])
        return {
            "profile": profile,
            "title": "Sicherer Standard: RAID1 → optional LUKS → LVM",
            "risk": "destructive",
            "executable": False,
            "devices": selected,
            "layers": layers,
            "commands": commands,
            "warnings": [
                "Alle ausgewählten Datenträger würden vollständig überschrieben.",
                "Vor einer späteren Ausführung müssen Mounts, bestehende Signaturen und das Root-/Dokumentenlaufwerk separat geprüft werden.",
                "RAID ersetzt kein zusätzliches Backup. Ein zweites Backup-Ziel bleibt erforderlich.",
            ],
        }
    if profile == "lvm-linear":
        if len(selected) < 2:
            raise ValueError("Ein linearer LVM-Pool benötigt mindestens zwei Geräte")
        vg = f"so_{name.replace('-', '_').replace('.', '_')}"
        commands = [
            ["pvcreate", *selected],
            ["vgcreate", vg, *selected],
            ["lvcreate", "--name", "data", "--extents", "100%FREE", vg, *selected],
        ]
        return {
            "profile": profile,
            "title": "Kapazitätspool: mehrere kleine Datenträger per LVM zusammenfassen",
            "risk": "high",
            "executable": False,
            "devices": selected,
            "layers": ["LVM linear"],
            "commands": commands,
            "warnings": [
                "Fällt ein beteiligter Datenträger aus, kann der gesamte lineare Pool unbrauchbar werden.",
                "Diese Variante ist nur sinnvoll, wenn ein unabhängiges vollständiges Backup vorhanden und überwacht ist.",
            ],
        }
    if profile == "aggregate-mirror":
        if len(selected) < 3:
            raise ValueError("Für die asymmetrische Spiegelung werden mindestens drei Geräte erwartet")
        return {
            "profile": profile,
            "title": "Expertenentwurf: kleine Datenträger bündeln und gegen ein großes Ziel spiegeln",
            "risk": "expert",
            "executable": False,
            "devices": selected,
            "layers": ["kleine Geräte → LVM linear", "Blockgeräte-Spiegelung", "Dateisystem"],
            "commands": [],
            "warnings": [
                "Dieser Aufbau verschachtelt Block-Layer und wird absichtlich nicht automatisch ausgeführt.",
                "Der Ausfall einer kleinen Platte zerstört die gesamte aggregierte Spiegel-Seite und löst einen vollständigen Rebuild aus.",
                "SimpleOffice soll hierfür später erst nach Größen-, Mount-, SMART- und Recovery-Prüfung einen konkreten Plan freigeben.",
            ],
        }
    raise ValueError("Unbekanntes Speicherprofil")


def format_bytes(value: object) -> str:
    amount = float(_number(value))
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
