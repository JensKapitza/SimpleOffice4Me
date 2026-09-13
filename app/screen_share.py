"""Cross-platform screen sharing and Miracast/Wi-Fi Display launcher."""
from __future__ import annotations

import functools
import os
import platform
import shutil
import subprocess
from typing import Any

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from .access_control import audit
from .auth import login_required


bp = Blueprint("screen", __name__, url_prefix="/screen")
_WINDOWS_CAPABILITY = "App.WirelessDisplay.Connect~~~~0.0.1.0"


def _system() -> str:
    return platform.system().strip().casefold()


def _android_client() -> bool:
    return "simpleoffice4me-android/" in request.headers.get("User-Agent", "").casefold()


def _flatpak_network_displays() -> bool:
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return False
    try:
        result = subprocess.run(
            [flatpak, "info", "org.gnome.NetworkDisplays"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@functools.lru_cache(maxsize=1)
def _windows_wireless_display_state() -> str:
    if _system() != "windows":
        return "not-applicable"
    dism = shutil.which("dism.exe") or shutil.which("dism")
    if not dism:
        return "unknown"
    try:
        result = subprocess.run(
            [
                dism,
                "/Online",
                "/Get-CapabilityInfo",
                f"/CapabilityName:{_WINDOWS_CAPABILITY}",
                "/English",
            ],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = (result.stdout + "\n" + result.stderr).casefold()
    if "state : installed" in text:
        return "installed"
    if "state : not present" in text:
        return "not-installed"
    return "unknown"


def platform_status() -> dict[str, Any]:
    system = _system()
    native_gnome = shutil.which("gnome-network-displays")
    miracle_sink = shutil.which("miracle-sinkctl")
    miracle_wifi = shutil.which("miracle-wifid")
    return {
        "platform": system or "unknown",
        "browser_capture": True,
        "android_native": _android_client(),
        "windows": {
            "available": system == "windows",
            "wireless_display": _windows_wireless_display_state(),
        },
        "linux": {
            "available": system == "linux",
            "gnome_network_displays": bool(native_gnome),
            "gnome_network_displays_path": native_gnome or "",
            "gnome_network_displays_flatpak": _flatpak_network_displays() if system == "linux" else False,
            "miracle_sinkctl": bool(miracle_sink),
            "miracle_sinkctl_path": miracle_sink or "",
            "miracle_wifid": bool(miracle_wifi),
            "miracle_wifid_path": miracle_wifi or "",
        },
    }


def _open_windows_uri(uri: str) -> None:
    explorer = shutil.which("explorer.exe") or shutil.which("explorer")
    if not explorer:
        raise RuntimeError("Windows Explorer wurde nicht gefunden")
    subprocess.Popen(
        [explorer, uri],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _launch_linux_sender() -> None:
    executable = shutil.which("gnome-network-displays")
    if executable:
        command = [executable]
    elif _flatpak_network_displays():
        flatpak = shutil.which("flatpak")
        if not flatpak:
            raise RuntimeError("Flatpak wurde nicht gefunden")
        command = [flatpak, "run", "org.gnome.NetworkDisplays"]
    else:
        raise RuntimeError("GNOME Network Displays ist nicht installiert")
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _launch_linux_receiver_control() -> None:
    sinkctl = shutil.which("miracle-sinkctl")
    if not sinkctl:
        raise RuntimeError("MiracleCast (miracle-sinkctl) ist nicht installiert")
    terminals = (
        ("x-terminal-emulator", ["-e"]),
        ("kgx", ["--"]),
        ("gnome-terminal", ["--"]),
        ("konsole", ["-e"]),
        ("xterm", ["-e"]),
    )
    for name, prefix in terminals:
        terminal = shutil.which(name)
        if terminal:
            subprocess.Popen(
                [terminal, *prefix, sinkctl, "--uibc"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
    raise RuntimeError("Kein Terminal zum Starten von miracle-sinkctl gefunden")


def _perform_action(action: str) -> str:
    system = _system()
    if action == "windows-send" and system == "windows":
        _open_windows_uri("ms-settings-connectabledevices:devicediscovery")
        return "Windows-Suche nach drahtlosen Anzeigen wurde geöffnet."
    if action == "windows-receive" and system == "windows":
        _open_windows_uri("ms-settings:project")
        return "Windows-Einstellungen für 'Projizieren auf diesen PC' wurden geöffnet."
    if action == "linux-send" and system == "linux":
        _launch_linux_sender()
        return "GNOME Network Displays wurde gestartet."
    if action == "linux-receive" and system == "linux":
        _launch_linux_receiver_control()
        return "MiracleCast Receiver-Steuerung wurde geöffnet."
    raise ValueError("Diese Bildschirm-Aktion ist auf diesem System nicht verfügbar")


@bp.get("")
@login_required
def index():
    return render_template("screen/index.html", screen_status=platform_status())


@bp.get("/status")
@login_required
def status():
    return jsonify(platform_status())


@bp.post("/action/<action>")
@login_required
def action(action: str):
    allowed = {"windows-send", "windows-receive", "linux-send", "linux-receive"}
    if action not in allowed:
        return ("Unbekannte Bildschirm-Aktion", 404)
    try:
        message = _perform_action(action)
    except (OSError, RuntimeError, ValueError) as exc:
        audit(
            "screen_platform_action",
            "screen",
            action,
            outcome="failure",
            detail={"platform": _system(), "error_type": type(exc).__name__},
        )
        flash(str(exc))
    else:
        audit("screen_platform_action", "screen", action, detail={"platform": _system()})
        flash(message)
    return redirect(url_for("screen.index"))
