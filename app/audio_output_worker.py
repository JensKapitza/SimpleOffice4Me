"""Local announcement consumer owned by the web process; no network listener."""
from __future__ import annotations

import atexit
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import ExitStack

from simpleoffice_service_lifecycle import ServiceState, error_detail, log_service_event
from tools.service_control import exclusive_lease
from .audio_output_discovery import discover_speaker_outputs
from .audio_output_engine import render_preset, render_tts, playback_command, prune_tts_cache, attenuated_audio
from .audio_output_store import AudioOutputStore
from .mini_services import default_config_path


class AudioOutputWorker:
    def __init__(self, root=None):
        self.root = root
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.processes = []
        self.cleaning_players = False
        self.active_job = None
        self.state = ServiceState("audio-output", "Audio-Ausgabe")
        self.scan_result = {"state": "waiting", "count": 0, "updated_at": None}
        self.next_scan = 0
        self.retry_limit = 3

    def store(self):
        return AudioOutputStore(self.root or default_config_path().parent / "audio")

    def settings(self, value=None):
        defaults = {"enabled": True, "autostart": True, "retry_limit": 3}
        if value is None:
            value = {**defaults, **self.store().service_settings("announcements")}
        if not isinstance(value, dict) or set(value) != set(defaults) or any(type(value[key]) is not bool for key in ("enabled", "autostart")) or type(value["retry_limit"]) is not int or not 0 <= value["retry_limit"] <= 6:
            raise ValueError("Ungültige Audio-Ausgabe-Einstellungen")
        return value

    def save_settings(self, value):
        value = self.settings(value)
        self.store().service_settings("announcements", value)
        self.retry_limit = value["retry_limit"]
        if not value["enabled"]:
            self.stop()
        return value

    def start(self):
        with self.lock:
            if self.cleaning_players:
                raise RuntimeError("Audio-Player wird noch beendet; Status aktualisieren")
            if self.stop_event.is_set() and self.processes:
                raise RuntimeError("Audio-Player wird noch beendet; Stop erneut ausführen")
            if self.thread is not None and self.thread.is_alive():
                if self.stop_event.is_set():
                    raise RuntimeError("Audio-Ausgabe wird noch beendet; Status aktualisieren")
                return self.status()
            value = self.settings()
            self.retry_limit = value["retry_limit"]
            if not value["enabled"]:
                self.state.state = "disabled"
                return self.status()
            self.stop_event.clear()
            self.state.state = "starting"
            self.state.retry_count = 0
            self.thread = threading.Thread(target=self._run, name="simpleoffice-announcements", daemon=True)
            self.thread.start()
            return self.status()

    def start_background(self):
        try:
            value = self.settings()
            if value["enabled"] and value["autostart"]:
                self.start()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self.state.failed(exc)
            self.state.retry_at = None  # Cannot assume autostart from unreadable settings.

    def stop(self):
        self.stop_event.set()
        with self.lock:
            processes = list(self.processes)
            thread = self.thread
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
            except OSError as exc:
                self._cleanup_error(exc)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        if not thread or not thread.is_alive():
            self._cleanup_players()
        with self.lock:
            self.state.state = "stopped" if not self.processes and not self.cleaning_players and (not thread or not thread.is_alive()) else "stopping"

    def _cleanup_error(self, exc):
        self.state.last_error = error_detail(exc)
        self.state.last_error_at = time.time()
        log_service_event("audio-output", "player_cleanup_failed", exc=exc)

    def _cleanup_players(self):
        with self.lock:
            if self.cleaning_players:
                return
            self.cleaning_players = True
            processes, self.processes = self.processes, []
        remaining = []
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._cleanup_error(exc)
                try:
                    process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired) as final_error:
                    self._cleanup_error(final_error)
                    remaining.append(process)
        with self.lock:
            self.processes.extend(remaining)
            self.cleaning_players = False
            if remaining:
                self.stop_event.set()
                self.state.state = "stopping"

    def scan(self):
        self.scan_result = {"state": "scanning", "updated_at": time.time(), "count": 0}
        try:
            devices = discover_speaker_outputs()
            self.store().sync_local_outputs(devices)
            self.scan_result = {"state": "completed", "updated_at": time.time(), "count": len(devices)}
        except (RuntimeError, OSError) as exc:
            self.scan_result = {"state": "failed", "updated_at": time.time(), "count": 0, "error": error_detail(exc)}
        self.next_scan = time.monotonic() + 30
        return self.scan_result

    def status(self):
        with self.lock:
            value = self.state.snapshot(os.getpid())
            running = bool(self.thread and self.thread.is_alive() and not self.stop_event.is_set())
            available = self.scan_result.get("state") == "completed" and self.scan_result.get("count", 0) > 0
            value.update(owner="web", requires=["web"], active_job=self.active_job, scan=self.scan_result,
                         health={"ok": running and available, "message": "Lokale Ausgänge verfügbar" if available else "Keine lokal bestätigten Audio-Ausgänge"})
            if value["state"] == "running" and not available:
                value["state"] = "waiting"
            return value

    def _run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    self._run_once()
                    return
                except Exception as exc:
                    self.state.failed(exc)
                    if self.state.retry_count > self.retry_limit:
                        self.state.retry_at = None
                        return
                    delay = min(60, 2 ** self.state.retry_count)
                    self.state.retry_at = time.monotonic() + delay
                    if self.stop_event.wait(delay):
                        return
        finally:
            if self.stop_event.is_set():
                self.state.state = "stopping" if self.processes else "stopped"

    def _run_once(self):
        # Creating/opening storage belongs inside the bounded recovery boundary.
        store = self.store()
        with exclusive_lease(store.root / "announcement-worker.lock") as acquired:
            if not acquired:
                self.state.state = "unavailable"
                return
            store.recover_interrupted()
            self.state.running()
            stable_since = time.monotonic()
            while not self.stop_event.is_set():
                if time.monotonic() - stable_since >= 60:
                    self.state.retry_count = 0
                if time.monotonic() >= self.next_scan:
                    self.scan()
                job = store.claim()
                if job is None:
                    self.stop_event.wait(1)
                    continue
                self.process_job(store, job)

    def process_job(self, store, job):
        self.active_job = job["id"]
        started_playback = False
        playback_files = ExitStack()
        try:
            outputs = store.playback_targets(job["targets"])
            payload = job["payload"]
            cache = store.root / "rendered"
            if job["kind"] == "sound":
                path = render_preset(payload["preset"], cache)
            elif job["kind"] == "tts":
                path = render_tts(payload["text"], payload["voice"], cache, cancel_event=self.stop_event)
            else:
                raise ValueError("Auftragstyp ist für lokale Wiedergabe nicht unterstützt")
            if self.stop_event.is_set():
                store.finish(job["id"], state="cancelled")
                return
            commands = []
            adjusted_files = {}
            for output in outputs:
                if output["node_id"] == "local" and output["output_id"].startswith("discovered-"):
                    player = shutil.which("paplay")
                    if not player:
                        raise RuntimeError("paplay fehlt für den erkannten PulseAudio-Ausgang")
                    commands.append([player, "--device=" + output["device"], "--volume=" + str(round(output["volume"] * 65536 / 100)), str(path)])
                else:
                    volume = output["volume"]
                    if volume not in adjusted_files:
                        adjusted_files[volume] = playback_files.enter_context(attenuated_audio(path, volume, cancel_event=self.stop_event))
                    adjusted = adjusted_files[volume]
                    if output["node_id"] == "local":
                        commands.append(playback_command(adjusted, output["device"]))
                    else:
                        from .audio_output_transport import announcement_command
                        commands.append(announcement_command(adjusted, output["transport"]))
            with self.lock:
                for command in commands:
                    if self.stop_event.is_set():
                        break
                    self.processes.append(subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                    started_playback = True
            deadline = time.monotonic() + 300
            while not self.stop_event.is_set() and any(process.poll() is None for process in self.processes):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Wiedergabe-Timeout")
                self.stop_event.wait(0.1)
            if self.stop_event.is_set():
                store.finish(job["id"], state="cancelled")
            elif not started_playback or any(process.returncode != 0 for process in self.processes):
                raise RuntimeError("Audio-Player hat die Wiedergabe abgebrochen")
            else:
                store.finish(job["id"], delivery="sent-unconfirmed" if any(output["node_id"] != "local" for output in outputs) else "")
        except Exception as exc:
            self.state.last_error = error_detail(exc)
            self.state.last_error_at = time.time()
            # Never replay a possibly partly delivered announcement automatically.
            retry = not started_playback and not isinstance(exc, ValueError) and job["attempts"] <= self.settings()["retry_limit"] and not self.stop_event.is_set()
            message = "Audio-Ziel oder Gruppe prüfen; externe Knoten benötigen einen angebundenen Transport." if isinstance(exc, ValueError) else "Wiedergabe nicht möglich. Audio-Geräte, Player und ggf. Piper-Modell prüfen."
            store.finish(job["id"], state="cancelled" if self.stop_event.is_set() else "queued" if retry else "failed", error=message, retry_seconds=min(60, 2 ** job["attempts"]))
        finally:
            self._cleanup_players()
            self.active_job = None
            try:
                playback_files.close()
                prune_tts_cache(store.root / "rendered")
            except OSError as exc:
                self.state.last_error = error_detail(exc)
                self.state.last_error_at = time.time()


worker = AudioOutputWorker()
atexit.register(worker.stop)
