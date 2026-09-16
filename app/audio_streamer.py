"""Live microphone streaming between SimpleOffice hosts.

The sender captures one local microphone with ffmpeg, encodes Opus once per RTP
output and sends it over UDP. The receiver decodes the RTP/Opus stream and fans
PCM out to local PulseAudio/PipeWire playback devices. A virtual microphone is
implemented as a Pulse/PipeWire null sink; applications select its monitor
source as the microphone.
"""
from __future__ import annotations

import atexit
import ipaddress
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from simpleoffice_service_lifecycle import ServiceState, error_detail
from simpleoffice_sip_runtime import auto_sip_bind_host

_HOST_RE = re.compile(r"[A-Za-z0-9.-]{1,253}\Z")
_SESSION_LOCK = threading.RLock()
_PROCESS_WAIT_SECONDS = 2
_MAX_STREAM_TARGETS = 16


def _port(value: Any) -> int:
    if type(value) is not int and not (isinstance(value, str) and value.strip().isascii() and value.strip().isdigit()):
        raise ValueError("RTP-Port muss eine ganze Zahl sein")
    port = int(value)
    if port < 1024 or port > 65534:
        raise ValueError("RTP-Port muss zwischen 1024 und 65534 liegen; der Folgeport wird für RTCP benötigt")
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
    if len(values) > _MAX_STREAM_TARGETS:
        raise ValueError(f"Maximal {_MAX_STREAM_TARGETS} RTP-Ziele pro Stream")
    result: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("RTP-Ziel muss ein Objekt sein")
        target = (_host(item.get("host")), _port(item.get("port", 5004)))
        if target not in seen:
            seen.add(target)
            result.append(target)
    return result


def normalize_speaker_devices(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("speaker_devices muss eine Liste sein")
    if len(values) > _MAX_STREAM_TARGETS:
        raise ValueError(f"Maximal {_MAX_STREAM_TARGETS} lokale Audio-Ausgaenge")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise ValueError("Audio-Ausgang muss Text sein")
        device = item.strip()[:240]
        if device and device not in seen:
            seen.add(device)
            result.append(device)
    return result


def _rtp_target(host: str, port: int) -> str:
    formatted_host = f"[{host}]" if ":" in host else host
    return f"rtp://{formatted_host}:{port}?pkt_size=1200"


def sender_command(*, source: str, backend: str, destinations: list[tuple[str, int]], bitrate_kbps: int = 64) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg ist nicht installiert")
    backend = str(backend or "pulse").strip().lower()
    source = str(source or "default").strip() or "default"
    bitrate = max(16, min(int(bitrate_kbps), 256))
    if backend == "pulse":
        command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-f", "pulse", "-i", source]
    elif backend == "alsa":
        command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-f", "alsa", "-i", source]
    elif backend == "dshow":
        if source == "default" or len(source) > 1024 or any(ord(c) < 32 or c in ":=" for c in source):
            raise ValueError("Windows-Mikrofon über die Gerätesuche auswählen")
        command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-f", "dshow", "-i", "audio=" + source]
    else:
        raise ValueError("Capture-Backend muss pulse, alsa oder dshow sein")
    for host, port in destinations:
        command += [
            "-map", "0:a:0", "-vn", "-ac", "2", "-ar", "48000",
            "-c:a", "libopus", "-application", "lowdelay", "-frame_duration", "20",
            "-b:a", f"{bitrate}k", "-payload_type", "111", "-f", "rtp",
            _rtp_target(host, port),
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


def decoder_command(sdp_path: str | Path, bind: str = "127.0.0.1") -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg ist nicht installiert")
    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning",
        "-protocol_whitelist", "file,udp,rtp", "-fflags", "nobuffer",
        "-flags", "low_delay", "-localaddr", bind, "-i", str(sdp_path), "-vn",
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


def _close_pipe(pipe: Any) -> None:
    if pipe is None:
        return
    try:
        pipe.close()
    except (OSError, ValueError):
        pass


def _terminate_process(process: subprocess.Popen | None, *, close_stdin: bool = False, close_stdout: bool = False) -> None:
    if process is None:
        return
    try:
        running = process.poll() is None
    except OSError:
        running = False
    if running:
        try:
            process.terminate()
            process.wait(timeout=_PROCESS_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=_PROCESS_WAIT_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass
    if close_stdout:
        _close_pipe(process.stdout)
    if close_stdin:
        _close_pipe(process.stdin)


@dataclass
class SenderSession:
    process: subprocess.Popen
    source: str
    backend: str
    destinations: list[tuple[str, int]]
    bitrate_kbps: int = 64


class ReceiverSession:
    def __init__(self, port: int, outputs: list[str], virtual_sink: str = "", bind: str = ""):
        self.port = _port(port)
        self.outputs = outputs
        self.virtual_sink = virtual_sink
        self.bind = bind or auto_sip_bind_host()
        address = ipaddress.ip_address(self.bind)
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            raise ValueError("Receiver benötigt eine konkrete lokale IPv4-Adresse")
        self.module_id = ""
        self._temp = tempfile.TemporaryDirectory(prefix="simpleoffice-audio-")
        self.sdp_path = Path(self._temp.name) / "stream.sdp"
        self.sdp_path.write_text(receiver_sdp(self.port), encoding="utf-8")
        self.decoder: subprocess.Popen | None = None
        self.players: list[subprocess.Popen] = []
        self.thread: threading.Thread | None = None
        self._runtime_lock = threading.RLock()
        self.bytes_received = 0
        self.last_audio_at = None

    def start(self) -> None:
        devices = list(self.outputs)
        try:
            if self.virtual_sink:
                self.module_id = ensure_virtual_microphone(self.virtual_sink)
                devices.append(self.virtual_sink)
            if not devices:
                raise ValueError("Mindestens Lautsprecher oder virtuelles Mikrofon aktivieren")
            self.decoder = subprocess.Popen(
                decoder_command(self.sdp_path, self.bind),
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
        try:
            while True:
                chunk = decoder.stdout.read(3840)
                if not chunk:
                    break
                self.bytes_received += len(chunk)
                self.last_audio_at = time.time()
                alive: list[subprocess.Popen] = []
                for player in self.players:
                    if player.poll() is not None or player.stdin is None:
                        _terminate_process(player, close_stdin=True)
                        continue
                    try:
                        player.stdin.write(chunk)
                        player.stdin.flush()
                        alive.append(player)
                    except (BrokenPipeError, OSError, ValueError):
                        _terminate_process(player, close_stdin=True)
                        continue
                self.players = alive
                if not alive:
                    break
        except (OSError, ValueError):
            pass
        finally:
            self._shutdown_runtime()

    def _shutdown_runtime(self) -> None:
        with self._runtime_lock:
            players = list(self.players)
            self.players.clear()
            decoder = self.decoder
            self.decoder = None
            module_id = self.module_id
            self.module_id = ""
            thread = self.thread
            self.thread = None
        # Signal every owned child before waiting/closing buffered pipes. A
        # blocked writer must not hold pipe.close() hostage during stop.
        for process in [decoder, *players]:
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        for player in players:
            _terminate_process(player, close_stdin=True)
        _terminate_process(decoder, close_stdout=True)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
        if module_id:
            unload_virtual_microphone(module_id)
        try:
            self._temp.cleanup()
        except OSError:
            pass

    def stop(self) -> None:
        self._shutdown_runtime()


def ensure_virtual_microphone(sink_name: str = "simpleoffice_stream") -> str:
    name = str(sink_name or "simpleoffice_stream").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
        raise ValueError("Name des virtuellen Mikrofons ist ungueltig")
    pactl = shutil.which("pactl")
    if not pactl:
        raise RuntimeError("pactl fehlt; PipeWire-Pulse oder PulseAudio installieren")
    try:
        result = subprocess.run(
            [pactl, "load-module", "module-null-sink", f"sink_name={name}",
             "sink_properties=device.description=SimpleOffice4Me_Stream"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Virtuelles Mikrofon konnte nicht erstellt werden") from exc
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
        self.states = {key: ServiceState("audio-" + key, "Audio " + key) for key in ("sender", "receiver")}
        self.requested = {"sender": None, "receiver": None}
        self.retry_limits = {"sender": 3, "receiver": 3}
        self.monitor_stop = threading.Event()
        self.monitor_thread = None

    def _ensure_monitor(self):
        with _SESSION_LOCK:
            if self.monitor_thread is not None and self.monitor_thread.is_alive():
                return
            self.monitor_stop.clear()
            self.monitor_thread = threading.Thread(target=self._monitor, name="simpleoffice-audio-health", daemon=True)
            self.monitor_thread.start()

    def start_background(self):
        from .audio_streamer_config import settings
        self._ensure_monitor()
        for service in ("sender", "receiver"):
            try:
                value = settings(service)
                if value["enabled"] and value["autostart"]:
                    self.configured_start(service)
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                self.states[service].failed(exc)

    def configured_start(self, service, changes=None, *, restart=False):
        from .audio_streamer_config import settings, validate_settings
        current = settings(service)
        value = validate_settings(service, {**current, **(changes or {})})
        if not value["enabled"]:
            raise ValueError("Audio-Dienst ist deaktiviert")
        if service == "sender" and not value["destinations"]:
            raise ValueError("Mindestens ein Ziel ist erforderlich")
        if service == "receiver" and not value["speaker_devices"] and not value["virtual_microphone"]:
            raise ValueError("Mindestens einen Ausgang oder virtuelles Mikrofon wählen")
        # Persist only validated settings. Starting never turns autostart on.
        settings(service, value)
        self.retry_limits[service] = value["retry_limit"]
        if restart:
            getattr(self, "stop_" + service)()
        arguments = {key: item for key, item in value.items() if key not in {"enabled", "autostart", "retry_limit"}}
        try:
            return getattr(self, "start_" + service)(**arguments)
        except (OSError, RuntimeError) as exc:
            alive = bool(self.sender and self.sender.process.poll() is None) if service == "sender" else self._receiver_alive()
            if alive:
                self.states[service].last_error = error_detail(exc)
                self.states[service].last_error_at = time.time()
            else:
                self.requested[service] = arguments
                if self.states[service].state != "failed":
                    self.states[service].failed(exc)
            self._ensure_monitor()
            raise

    def start_sender(self, *, source: str, backend: str, destinations: Any, bitrate_kbps: int = 64, _retry=False) -> dict[str, Any]:
        from .audio_streamer_config import validate_settings
        clean = validate_settings("sender", {"source": source, "backend": backend, "destinations": destinations, "bitrate_kbps": bitrate_kbps})
        targets = normalize_destinations(clean["destinations"])
        request = {key: clean[key] for key in ("source", "backend", "destinations", "bitrate_kbps")}
        with _SESSION_LOCK:
            if self.requested["sender"] == request and self.sender and self.sender.process.poll() is None:
                return self.status()["sender"]
            # Build and validate before stopping a healthy stream.
            command = sender_command(source=clean["source"], backend=clean["backend"], destinations=targets, bitrate_kbps=clean["bitrate_kbps"])
            self.stop_sender(_clear=False)
            self.requested["sender"] = request
            state = self.states["sender"]
            if not _retry:
                state.retry_count = 0
            state.state = "starting"
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.sender = SenderSession(process, clean["source"], clean["backend"], targets, clean["bitrate_kbps"])
                state.running()
            except (OSError, RuntimeError) as exc:
                state.failed(exc)
                self._ensure_monitor()
                raise
            self._ensure_monitor()
            return self.status()["sender"]

    def stop_sender(self, *, _clear=True) -> None:
        with _SESSION_LOCK:
            self.states["sender"].state = "stopping"
            if self.sender:
                _terminate_process(self.sender.process)
            self.sender = None
            self.states["sender"].state = "stopped"
            self.states["sender"].retry_at = None
            if _clear:
                self.requested["sender"] = None

    def start_receiver(self, *, port: int, speaker_devices: list[str] | None = None,
                       virtual_microphone: bool = True, virtual_sink: str = "simpleoffice_stream",
                       bind: str = "", _retry=False) -> dict[str, Any]:
        from .audio_streamer_config import validate_settings
        clean = validate_settings("receiver", {"port": port, "speaker_devices": speaker_devices or [],
                    "virtual_microphone": virtual_microphone, "virtual_sink": virtual_sink, "bind": bind})
        request = {key: clean[key] for key in ("port", "speaker_devices", "virtual_microphone", "virtual_sink", "bind")}
        if not clean["speaker_devices"] and not clean["virtual_microphone"]:
            raise ValueError("Mindestens einen Ausgang oder virtuelles Mikrofon wählen")
        with _SESSION_LOCK:
            if self.requested["receiver"] == request and self._receiver_alive():
                return self.status()["receiver"]
            # Check executable availability and configuration before replacing.
            decoder_command(Path("stream.sdp"), clean["bind"] or auto_sip_bind_host())
            for device in clean["speaker_devices"]:
                paplay_command(device)
            if clean["virtual_microphone"] and not shutil.which("pactl"):
                raise RuntimeError("pactl fehlt")
            self.stop_receiver(_clear=False)
            self.requested["receiver"] = request
            state = self.states["receiver"]
            if not _retry:
                state.retry_count = 0
            state.state = "starting"
            try:
                session = ReceiverSession(clean["port"], clean["speaker_devices"],
                    clean["virtual_sink"] if clean["virtual_microphone"] else "", clean["bind"])
                session.start()
                self.receiver = session
                state.running()
            except (OSError, RuntimeError, ValueError) as exc:
                state.failed(exc)
                self._ensure_monitor()
                raise
            self._ensure_monitor()
            return self.status()["receiver"]

    def stop_receiver(self, *, _clear=True) -> None:
        with _SESSION_LOCK:
            self.states["receiver"].state = "stopping"
            if self.receiver:
                self.receiver.stop()
            self.receiver = None
            self.states["receiver"].state = "stopped"
            self.states["receiver"].retry_at = None
            if _clear:
                self.requested["receiver"] = None

    def _receiver_alive(self):
        receiver = self.receiver
        return bool(receiver and receiver.decoder and receiver.decoder.poll() is None
                    and receiver.thread and receiver.thread.is_alive()
                    and any(player.poll() is None for player in receiver.players))

    def _monitor(self):
        while not self.monitor_stop.wait(2):
            self.recover()

    def recover(self):
        with _SESSION_LOCK:
            for service in ("sender", "receiver"):
                wanted = self.requested[service]
                if wanted is None:
                    continue
                state = self.states[service]
                alive = bool(self.sender and self.sender.process.poll() is None) if service == "sender" else self._receiver_alive()
                if alive:
                    if state.started_at and time.time() - state.started_at > 60:
                        state.retry_count = 0
                    continue
                if state.state != "failed":
                    getattr(self, "stop_" + service)(_clear=False)
                    state.failed(RuntimeError("Audio-Prozess oder Ausgabe wurde beendet"))
                if state.retry_count > self.retry_limits[service]:
                    state.retry_at = None
                if state.retry_at is not None and time.monotonic() >= state.retry_at:
                    try:
                        getattr(self, "start_" + service)(**wanted, _retry=True)
                    except (OSError, RuntimeError, ValueError) as exc:
                        # Preflight failures happen before start records a failure.
                        if state.retry_at is None or state.retry_at <= time.monotonic():
                            state.failed(exc)

    def stop_all(self) -> None:
        self.monitor_stop.set()
        with _SESSION_LOCK:
            self.stop_sender()
            self.stop_receiver()
        thread = self.monitor_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def status(self) -> dict[str, Any]:
        import os
        with _SESSION_LOCK:
            sender = self.sender
            receiver = self.receiver
            result = {}
            for key in ("sender", "receiver"):
                row = self.states[key].snapshot(os.getpid())
                row["requires"] = ["web"]
                row["owner"] = "web"
                row["running"] = bool(sender and sender.process.poll() is None) if key == "sender" else self._receiver_alive()
                if row["state"] == "running" and not row["running"]:
                    row["state"] = "failed"
                row["health"] = {"ok": row["running"], "message": "Audio-Prozesse aktiv; Signalpegel nicht geprüft" if row["running"] else "Keine aktive Audio-Verbindung"}
                result[key] = row
            result["sender"].update({
                "source": sender.source if sender else "", "backend": sender.backend if sender else "",
                "destinations": [{"host": host, "port": port} for host, port in sender.destinations] if sender else [],
                "pid": sender.process.pid if sender else None,
            })
            result["receiver"].update({
                "port": receiver.port if receiver else None, "bind": receiver.bind if receiver else None,
                "speaker_devices": list(receiver.outputs) if receiver else [],
                "virtual_microphone": f"{receiver.virtual_sink}.monitor" if receiver and receiver.module_id else "",
                "bytes_received": receiver.bytes_received if receiver else 0,
                "last_audio_at": receiver.last_audio_at if receiver else None,
            })
            if result["receiver"]["running"]:
                receiving = receiver.last_audio_at is not None and time.time() - receiver.last_audio_at < 10
                result["receiver"]["health"] = {"ok": receiving, "message": "PCM empfangen und an Player übergeben" if receiving else "Empfang bereit; wartet auf Audiodaten"}
                result["receiver"]["active_connection"] = receiving
                if not receiving:
                    result["receiver"]["state"] = "waiting"
            return result


manager = LiveAudioManager()
atexit.register(manager.stop_all)
