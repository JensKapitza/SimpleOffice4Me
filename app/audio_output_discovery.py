"""Discover local PipeWire/PulseAudio playback sinks for the audio streamer."""
from __future__ import annotations

import shutil
import subprocess
from typing import Any

_MAX_DISCOVERED_OUTPUTS = 64
_DISCOVERY_TIMEOUT_SECONDS = 5


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
    pactl = shutil.which("pactl")
    if not pactl:
        raise RuntimeError("pactl fehlt; PipeWire-Pulse oder PulseAudio installieren")

    output = _run_pactl(pactl, "list", "short", "sinks")
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
        default_sink = _run_pactl(pactl, "get-default-sink").strip()[:240]
    except RuntimeError:
        pass
    for device in devices:
        device["default"] = device["id"] == default_sink

    return sorted(devices, key=lambda item: (not bool(item["default"]), str(item["id"]).casefold()))
