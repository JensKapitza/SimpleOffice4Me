"""Live microphone streaming between SimpleOffice hosts.

The sender captures one local microphone with ffmpeg, encodes Opus once per RTP
output and sends it over UDP. The receiver decodes the RTP/Opus stream and fans
PCM out to local PulseAudio/PipeWire playback devices. A virtual microphone is
implemented as a Pulse/PipeWire null sink; applications select its monitor
source as the microphone.
"""
from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HOST_RE = re.compile(r"[A-Za-z0-9.-]{1,253}\Z")
_SESSION_LOCK = threading.RLock()


def _port(value: Any) -> int:
    port = int(value)
    if port < 1024 or port > 65535:
        raise ValueError("Port muss zwischen 1024 und 65535 liegen")
    return port


def _host(value: Any) -> str:
    host = str(value or "").strip()
    if not host:
        raise ValueError("Zielhost fehlt")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        if not _HOST_RE.fullmatch(host) or ".." in host or host.startswith(".") or host.endswith("."):
            raise ValueError("Zielhost ist ungueltig")
        return host


def normalize_destinations(values: Any) -> list[tuple[str, int]]:
    if not isinstance(values, list) or not values:
        raise ValueError("Mindestens ein RTP-Ziel ist erforderlich")
    result: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("RTP-Ziel muss ein Objekt sein")
        target = (_host(item.get("host")), _port(item.get("port", 5004)))
        if target not in seen:
            seen.add(target)
            result.append(target)
    if len(result) > 16:
        raise ValueError("Maximal 16 RTP-Ziele pro Stream")
    return result


def sender_command(*, source: str, backend: str, destinations: list[tuple[str, int]], bitrate_kbps: int = 64) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg ist nicht installiert")
    backend = str(backend or "pulse").strip().lower()
    source = str(source or "default").strip()[:240] or "default"
    bitrate = max(16, min(int(bitrate_kbps), 256))
    if backend == "pulse":
        command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-f", "pulse", "-i", source]
    elif backend == "alsa":
        command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-f", "alsa", "-i", source]
    else:
        raise ValueError("Capture-Backend muss pulse oder alsa sein")
    for host, port in destinations:
        command += [
            "-map", "0:a:0", "-vn", "-ac", "2", "-ar", "48000",
            "-c:a", "libopus", "-application", "lowdelay", "-frame_duration", "20",
            "-b:a", f"{bitrate}k", "-payload_type", "111", "-f", "rtp",
            f"rtp://{host}:{port}?pkt_size=1200",
        ]
    return command


def receiver_sdp(port: int) -> str:
    listen_port = _port(port)
    return (
        "v=0\r\n"
        "o=- 0 0 IN IP4 127.0.0.1\r\n"
        "s=SimpleOffice4Me Live Audio\r\n"
        "c=IN IP4 0.0.0.0\r\n"
        "t=0 0\r\n"
        f"m=audio {listen_port} RTP/AVP 111\r\n"
        "a=rtpmap:111 opus/48000/2\r\n"
        "a=recvonly\r\n"
    )


def decoder_command(sdp_path: str | Path) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg ist nicht installiert")
    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning",
        "-protocol_whitelist", "file,udp,rtp", "-fflags", "nobuffer",
        "-flags", "low_delay", "-i", str(sdp_path), "-vn",
        "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2", "pipe:1",
    ]


def paplay_command(device: str = "") -> list[str]:
    paplay = shutil.which("paplay")
    if not paplay:
        raise RuntimeError("paplay fehlt; PipeWire-Pulse oder PulseAudio installieren")
    command = [paplay]
    if device:
        command.append(f"--device={str(device)[:240]}")
    return command + ["--raw", "--rate=48000", "--channels=2", "--format=s16le"]


@dataclass
class SenderSession:
    process: subprocess.Popen
    source: str
    backend: str
    destinations: list[tuple[str, int]]


class ReceiverSession:
    def __init__(self, port: int, outputs: list[str], virtual_sink: str = ""):
        self.port = _port(port)
        self.outputs = outputs
        self.virtual_sink = virtual_sink
        self.module_id = ""
        self._temp = tempfile.TemporaryDirectory(prefix="simpleoffice-audio-")
        self.sdp_path = Path(self._temp.name) / "stream.sdp"
        self.sdp_path.write_text(receiver_sdp(self.port), encoding="utf-8")
        self.decoder: subprocess.Popen | None = None
        self.players: list[subprocess.Popen] = []
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        devices = list(self.outputs)
        try:
            if self.virtual_sink:
                self.module_id = ensure_virtual_microphone(self.virtual_sink)
                devices.append(self.virtual_sink)
            if not devices:
                raise ValueError("Mindestens Lautsprecher oder virtuelles Mikrofon aktivieren")
            self.decoder = subprocess.Popen(
                decoder_command(self.sdp_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            assert self.decoder.stdout is not None
            for device in devices:
                self.players.append(
                    subprocess.Popen(
                        paplay_command(device),
                        stdin=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                )
            self.thread = threading.Thread(
                target=self._fanout,
                name="simpleoffice-audio-fanout",
                daemon=True,
            )
            self.thread.start()
        except Exception:
            self.stop()
            raise

    def _fanout(self) -> None:
        decoder = self.decoder
        if decoder is None or decoder.stdout is None:
            return
        while True:
            chunk = decoder.stdout.read(3840)
            if not chunk:
                break
            alive: list[subprocess.Popen] = []
            for player in self.players:
                if player.poll() is not None or player.stdin is None:
                    continue
                try:
                    player.stdin.write(chunk)
                    player.stdin.flush()
                    alive.append(player)
                except (BrokenPipeError, OSError):
                    continue
            self.players = alive
            if not alive:
                break

    def stop(self) -> None:
        for player in list(self.players):
            if player.stdin:
                try:
                    player.stdin.close()
                except OSError:
                    pass
            if player.poll() is None:
                try:
                    player.terminate()
                except OSError:
                    pass
        self.players.clear()
        if self.decoder and self.decoder.poll() is None:
            try:
                self.decoder.terminate()
            except OSError:
                pass
        if self.module_id:
            unload_virtual_microphone(self.module_id)
            self.module_id = ""
        self._temp.cleanup()


def ensure_virtual_microphone(sink_name: str = "simpleoffice_stream") -> str:
    name = str(sink_name or "simpleoffice_stream").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
        raise ValueError("Name des virtuellen Mikrofons ist ungueltig")
    pactl = shutil.which("pactl")
    if not pactl:
        raise RuntimeError("pactl fehlt; PipeWire-Pulse oder PulseAudio installieren")
    result = subprocess.run(
        [pactl, "load-module", "module-null-sink", f"sink_name={name}",
         "sink_properties=device.description=SimpleOffice4Me_Stream"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    module_id = result.stdout.strip()
    if not module_id.isdigit():
        raise RuntimeError("Virtuelles Mikrofon konnte nicht erstellt werden")
    return module_id


def unload_virtual_microphone(module_id: str) -> None:
    pactl = shutil.which("pactl")
    if pactl and str(module_id).isdigit():
        try:
            subprocess.run(
                [pactl, "unload-module", str(module_id)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


class LiveAudioManager:
    def __init__(self) -> None:
        self.sender: SenderSession | None = None
        self.receiver: ReceiverSession | None = None

    def start_sender(self, *, source: str, backend: str, destinations: Any, bitrate_kbps: int = 64) -> dict[str, Any]:
        targets = normalize_destinations(destinations)
        with _SESSION_LOCK:
            self.stop_sender()
            process = subprocess.Popen(sender_command(source=source, backend=backend, destinations=targets, bitrate_kbps=bitrate_kbps), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.sender = SenderSession(process=process, source=source, backend=backend, destinations=targets)
            return self.status()["sender"]

    def stop_sender(self) -> None:
        if self.sender and self.sender.process.poll() is None:
            try:
                self.sender.process.terminate()
            except OSError:
                pass
        self.sender = None

    def start_receiver(self, *, port: int, speaker_devices: list[str] | None = None, virtual_microphone: bool = True, virtual_sink: str = "simpleoffice_stream") -> dict[str, Any]:
        if speaker_devices is not None and not isinstance(speaker_devices, list):
            raise ValueError("speaker_devices muss eine Liste sein")
        devices = [str(item).strip()[:240] for item in (speaker_devices or []) if str(item).strip()]
        with _SESSION_LOCK:
            self.stop_receiver()
            session = ReceiverSession(port, devices, virtual_sink if virtual_microphone else "")
            session.start()
            self.receiver = session
            return self.status()["receiver"]

    def stop_receiver(self) -> None:
        if self.receiver:
            self.receiver.stop()
        self.receiver = None

    def status(self) -> dict[str, Any]:
        sender = self.sender
        receiver = self.receiver
        return {
            "sender": None if sender is None else {
                "running": sender.process.poll() is None,
                "source": sender.source,
                "backend": sender.backend,
                "destinations": [{"host": host, "port": port} for host, port in sender.destinations],
            },
            "receiver": None if receiver is None else {
                "running": bool(receiver.decoder and receiver.decoder.poll() is None),
                "port": receiver.port,
                "speaker_devices": list(receiver.outputs),
                "virtual_microphone": f"{receiver.virtual_sink}.monitor" if receiver.virtual_sink else "",
            },
        }


manager = LiveAudioManager()
