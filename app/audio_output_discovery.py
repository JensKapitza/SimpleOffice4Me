"""Shared discovery of local audio inputs and outputs."""
from __future__ import annotations

from collections import Counter
import shutil
import platform
import subprocess
import re
from pathlib import Path
from typing import Any

_MAX_DISCOVERED_OUTPUTS = 64
_DISCOVERY_TIMEOUT_SECONDS = 5
_ALSA_ROOT = Path("/proc/asound")


def _run_pactl(pactl: str, *args: str) -> str:
    try:
        result = subprocess.run(
            [pactl, *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=_DISCOVERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Audio-Ausgänge konnten nicht ermittelt werden") from exc
    return result.stdout


def _parse_sink_line(line: str) -> dict[str, Any] | None:
    parts = line.rstrip().split("\t")
    if len(parts) < 2:
        parts = line.split(None, 4)
    if len(parts) < 2:
        return None
    sink_id = parts[1].strip()[:240]
    if not sink_id:
        return None
    return {
        "id": sink_id,
        "driver": parts[2].strip()[:160] if len(parts) > 2 else "",
        "state": parts[4].strip().lower()[:40] if len(parts) > 4 else "",
        "default": False,
    }


def discover_speaker_outputs() -> list[dict[str, Any]]:
    """Return local Pulse/PipeWire sinks usable by paplay."""
    return _discover("sinks", "sink")


def discover_receiver_outputs() -> list[dict[str, Any]]:
    """Receiver targets only: never register the Windows fallback as a Pulse sink."""
    if platform.system() != "Windows":
        return discover_speaker_outputs()
    if not shutil.which("ffplay"):
        raise RuntimeError("FFplay fehlt; Windows-Wiedergabe ist nicht verfügbar")
    return [{"id": "default", "label": "Windows-Systemstandard (Hardware nicht geprüft)",
             "driver": "FFplay", "default": True, "state": "unknown"}]


def discover_microphone_inputs(backend: str = "auto") -> list[dict[str, Any]]:
    """Monitor sources remain manually selectable, but are not microphones."""
    if backend not in {"auto", "pulse", "alsa", "dshow"}:
        raise ValueError("Unbekanntes Capture-Backend")
    if backend == "dshow" or (backend == "auto" and platform.system() == "Windows"):
        return _dshow_microphones()
    if backend == "alsa":
        return _alsa_microphones()
    try:
        devices = [dict(item, backend="pulse") for item in _discover("sources", "source") if not item["id"].endswith(".monitor")]
    except RuntimeError:
        devices = _alsa_microphones() if backend == "auto" else []
        if not devices:
            raise
        return devices
    return devices or (_alsa_microphones() if backend == "auto" else [])


def _dshow_microphones() -> list[dict[str, Any]]:
    """FFmpeg lists DirectShow devices on stderr and normally exits nonzero."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg fehlt; Mikrofone können nicht gesucht werden")
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            shell=False, capture_output=True, encoding="utf-8", errors="replace",
            timeout=_DISCOVERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Windows-Mikrofonsuche fehlgeschlagen") from exc
    devices = []
    friendly_names = Counter()
    pending = None
    audio_section = False
    recognized = False
    for line in result.stderr.splitlines():
        if "DirectShow audio devices" in line:
            audio_section = recognized = True
        elif "DirectShow video devices" in line:
            audio_section = False
            recognized = True
        if "Could not enumerate audio only devices" in line:
            recognized = True
        alternate = re.search(r'Alternative name "(.*)"', line)
        if alternate:
            if pending is not None:
                ident = alternate.group(1)
                # DirectShow splits the input on ':'. Never permit a second input.
                if 1 <= len(ident) <= 1024 and not any(ord(c) < 32 or c in ':=' for c in ident):
                    pending["id"] = ident
            pending = None
            continue
        match = re.search(r'\] "(.*)"(?: \(([^)]*)\))?\s*$', line)
        if not match:
            continue
        label, kind = match.groups()
        recognized = True
        pending = None
        if (kind and "audio" not in kind.split(", ")) or (not kind and not audio_section):
            continue
        friendly_names[label] += 1
        pending = {"id": label, "label": label[:160], "backend": "dshow", "driver": "DirectShow", "state": "available", "default": False}
        devices.append(pending)
    if not recognized:
        raise RuntimeError("FFmpeg unterstützt keine DirectShow-Gerätesuche oder der Zugriff ist gescheitert")
    # Ambiguous friendly names must not silently select the wrong microphone.
    ids = Counter(item["id"] for item in devices)
    return [item for item in devices if ids[item["id"]] == 1
            and friendly_names[item["id"]] <= 1
            and 1 <= len(item["id"]) <= 1024
            and not any(ord(c) < 32 or c in ':=' for c in item["id"])][:_MAX_DISCOVERED_OUTPUTS]


def _alsa_microphones() -> list[dict[str, Any]]:
    """Existing ALSA capture backend: read kernel inventory, no command/tool install."""
    try:
        with (_ALSA_ROOT / "pcm").open(encoding="utf-8") as handle:
            lines = handle.read(65536).splitlines()
    except FileNotFoundError:
        return []
    devices = []
    seen = set()
    for line in lines:
        match = re.match(r"^(\d{2,3})-(\d{2,3}):\s*(.*?)\s*:\s*.*\bcapture\s+[1-9]\d*\b", line)
        if not match:
            continue
        card, device, label = match.groups()
        try:
            # ALSA card IDs survive ordinary numeric card-index reordering.
            card_id = (_ALSA_ROOT / ("card" + str(int(card))) / "id").read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue  # Device removed during the scan; never persist a guessed ID.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", card_id):
            continue
        ident = f"plughw:CARD={card_id},DEV={int(device)}"
        if ident in seen:
            continue
        seen.add(ident)
        devices.append({"id": ident, "label": label[:160], "driver": "ALSA", "backend": "alsa", "state": "available", "default": False})
        if len(devices) >= _MAX_DISCOVERED_OUTPUTS:
            break
    return devices


def _discover(kind: str, default_kind: str) -> list[dict[str, Any]]:
    pactl = shutil.which("pactl")
    if not pactl:
        raise RuntimeError("pactl fehlt; PipeWire-Pulse oder PulseAudio installieren")

    output = _run_pactl(pactl, "list", "short", kind)
    devices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        device = _parse_sink_line(line)
        if device is None or device["id"] in seen:
            continue
        seen.add(device["id"])
        devices.append(device)
        if len(devices) >= _MAX_DISCOVERED_OUTPUTS:
            break

    default_sink = ""
    try:
        default_sink = _run_pactl(pactl, "get-default-" + default_kind).strip()[:240]
    except RuntimeError:
        pass
    for device in devices:
        device["default"] = device["id"] == default_sink

    return sorted(devices, key=lambda item: (not bool(item["default"]), str(item["id"]).casefold()))
