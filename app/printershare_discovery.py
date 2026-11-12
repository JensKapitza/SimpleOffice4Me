"""Operating-system printer discovery for Windows and CUPS platforms."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PrinterDiscoveryResult:
    printers: list[dict[str, Any]]
    issue: str | None = None


def printer_id(name: str) -> str:
    material = f"{platform.system().casefold()}\0{name}".encode("utf-8", "replace")
    return hashlib.sha256(material).hexdigest()[:24]


def infer_printer_kind(name: str, driver: str = "") -> str:
    value = f"{name} {driver}".casefold()
    if any(token in value for token in ("zebra", "dymo", "brother ql", "label", "etikett", "thermal", "thermo")):
        return "label"
    return "normal"


def _cups_environment() -> dict[str, str]:
    """Use stable CUPS command output while preserving configured server access."""
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    return environment


def _run_cups(
    executable: str,
    *arguments: str,
) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    try:
        result = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            env=_cups_environment(),
        )
    except subprocess.TimeoutExpired:
        logging.getLogger(__name__).warning("CUPS command timed out (%s)", arguments[0])
        return None, "timeout"
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "CUPS command could not start (%s, %s)",
            arguments[0],
            type(exc).__name__,
        )
        return None, "client_error"
    return result, None


def _cups_command_issue(result: subprocess.CompletedProcess[str], default: str) -> str:
    error_text = f"{result.stderr or ''} {result.stdout or ''}".casefold()
    if any(phrase in error_text for phrase in ("permission denied", "not authorized", "forbidden")):
        return "access_denied"
    return default


def _valid_cups_queue_name(name: str) -> bool:
    return bool(
        name
        and len(name) <= 255
        and name == name.strip()
        and not name.startswith("-")
        and all(char.isprintable() for char in name)
    )


def _discover_cups() -> PrinterDiscoveryResult:
    executable = shutil.which("lpstat")
    if not executable:
        logging.getLogger(__name__).warning("CUPS printer discovery skipped: lpstat is unavailable")
        return PrinterDiscoveryResult([], "client_missing")
    scheduler, scheduler_error = _run_cups(executable, "-r")
    if scheduler_error:
        return PrinterDiscoveryResult([], scheduler_error)
    if scheduler is None or scheduler.returncode or "scheduler is running" not in scheduler.stdout.casefold():
        issue = _cups_command_issue(scheduler, "service_unavailable") if scheduler else "service_unavailable"
        logging.getLogger(__name__).warning("CUPS scheduler is unavailable (%s)", issue)
        return PrinterDiscoveryResult([], issue)

    result, listing_error = _run_cups(executable, "-p")
    if listing_error:
        return PrinterDiscoveryResult([], listing_error)
    if result is None:
        return PrinterDiscoveryResult([], "query_failed")
    no_destinations = "no destinations added" in f"{result.stdout} {result.stderr}".casefold()
    if result.returncode and not no_destinations:
        issue = _cups_command_issue(result, "query_failed")
        logging.getLogger(__name__).warning("CUPS printer listing failed (%s)", issue)
        return PrinterDiscoveryResult([], issue)

    default_name = ""
    default_result, default_error = _run_cups(executable, "-d")
    if default_error:
        logging.getLogger(__name__).info("CUPS default printer lookup unavailable (%s)", default_error)
    if default_result and default_result.returncode == 0:
        default_match = re.match(
            r"^system default destination:\s*(.+?)\s*$",
            default_result.stdout.strip(),
            re.IGNORECASE,
        )
        if default_match and _valid_cups_queue_name(default_match.group(1)):
            default_name = default_match.group(1)
    printers: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for line in result.stdout.splitlines():
        match = re.match(
            r"^printer\s+(\S+)\s+(now printing|is idle|idle|is printing|printing|disabled)\b",
            line.strip(),
            re.IGNORECASE,
        )
        if not match:
            continue
        name = match.group(1).strip()
        if not _valid_cups_queue_name(name) or name in seen_names:
            continue
        seen_names.add(name)
        enabled = match.group(2).casefold() != "disabled"
        printers.append(
            {
                "printer_id": printer_id(name),
                "name": name,
                "driver": "CUPS",
                "port": "",
                "is_default": name == default_name,
                "kind": infer_printer_kind(name, "CUPS"),
                "backend": "cups",
                "is_enabled": enabled,
                "status": "ready" if enabled else "disabled",
            }
        )
    if result.stdout.strip() and not printers and not no_destinations:
        logging.getLogger(__name__).warning("CUPS printer listing returned no parseable queues")
        return PrinterDiscoveryResult([], "query_failed")
    return PrinterDiscoveryResult(printers)


def _discover_windows() -> list[dict[str, Any]]:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not executable:
        return []
    command = (
        "Get-CimInstance Win32_Printer | "
        "Select-Object Name,DriverName,PortName,Shared,Default,WorkOffline | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        return []
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    rows = value if isinstance(value, list) else [value]
    printers = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("Name") or "").strip() or row.get("WorkOffline") is True:
            continue
        name = str(row["Name"]).strip()
        driver = str(row.get("DriverName") or "")
        printers.append(
            {
                "printer_id": printer_id(name),
                "name": name,
                "driver": driver,
                "port": str(row.get("PortName") or ""),
                "is_default": bool(row.get("Default")),
                "kind": infer_printer_kind(name, driver),
                "backend": "windows",
            }
        )
    return printers


def discover_printer_inventory() -> PrinterDiscoveryResult:
    system = platform.system()
    if system == "Windows":
        return PrinterDiscoveryResult(_discover_windows())
    if system in {"Linux", "Darwin"}:
        return _discover_cups()
    return PrinterDiscoveryResult([])


def discover_printers() -> list[dict[str, Any]]:
    """Return just the list for existing printing routes."""
    return discover_printer_inventory().printers
