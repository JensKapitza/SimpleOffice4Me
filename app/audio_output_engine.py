"""Local audio renderer for SimpleOffice output nodes.

TTS is generated through a locally installed Piper CLI. Common notification
sounds are generated as WAV in Python so the feature does not depend on bundled
binary assets.
"""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Iterable


SAMPLE_RATE = 48_000


def _cache_key(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _tone_samples(sequence: Iterable[tuple[float, float, float]]) -> list[int]:
    samples: list[int] = []
    amplitude = 0.38 * 32767
    for frequency, duration, gain in sequence:
        count = max(1, int(SAMPLE_RATE * duration))
        for index in range(count):
            if frequency <= 0:
                samples.append(0)
                continue
            fade = min(1.0, index / 400, (count - index) / 400)
            value = amplitude * gain * fade * math.sin(2 * math.pi * frequency * index / SAMPLE_RATE)
            samples.append(int(max(-32767, min(32767, value))))
    return samples


def _preset_sequence(name: str) -> list[tuple[float, float, float]]:
    presets = {
        "gong": [(659.25, 0.35, 1.0), (0, 0.08, 0), (523.25, 0.75, 0.9)],
        "doorbell": [(784.0, 0.22, 1.0), (0, 0.12, 0), (659.25, 0.45, 0.9)],
        "alarm.fire": [(880.0, 0.25, 1.0), (660.0, 0.25, 1.0)] * 6,
        "alarm.warning": [(440.0, 0.45, 1.0), (0, 0.18, 0)] * 5,
        "baby.cry": [(520.0, 0.20, 0.55), (690.0, 0.25, 0.7), (570.0, 0.22, 0.6), (0, 0.15, 0)] * 3,
    }
    if name not in presets:
        raise ValueError("Unbekannter Audio-Preset")
    return presets[name]


def render_preset(name: str, cache_dir: str | Path) -> Path:
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"preset-{_cache_key(name)}.wav"
    if target.exists():
        return target
    samples = _tone_samples(_preset_sequence(name))
    with wave.open(str(target), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(b"".join(struct.pack("<h", value) for value in samples))
    return target


def render_tts(text: str, voice: str, cache_dir: str | Path, *, model: str | None = None) -> Path:
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    selected_model = str(model or os.environ.get("SIMPLEOFFICE_PIPER_MODEL", "")).strip()
    if not selected_model:
        raise RuntimeError("Piper-Modell fehlt: SIMPLEOFFICE_PIPER_MODEL setzen")
    piper = shutil.which("piper")
    if not piper:
        raise RuntimeError("Piper ist nicht installiert")
    target = root / f"tts-{_cache_key(text, voice, selected_model)}.wav"
    if target.exists():
        return target
    subprocess.run(
        [piper, "--model", selected_model, "--output_file", str(target)],
        input=text.encode("utf-8"), check=True, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, timeout=45,
    )
    return target


def playback_command(path: str | Path, device: str = "") -> list[str]:
    file_path = str(Path(path))
    if shutil.which("pw-play"):
        command = ["pw-play"]
        if device:
            command.extend(["--target", device])
        return command + [file_path]
    if shutil.which("aplay"):
        command = ["aplay", "-q"]
        if device:
            command.extend(["-D", device])
        return command + [file_path]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", file_path]
    raise RuntimeError("Kein Audio-Player gefunden (pw-play, aplay oder ffplay)")


def play_file(path: str | Path, device: str = "") -> None:
    subprocess.run(playback_command(path, device), check=True, timeout=300)
