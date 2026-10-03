"""SFTP mini-service facade over the existing Paramiko process and OS service.

System OpenSSH uses its own accounts and filesystem. It is never advertised as
providing SimpleOffice's virtual filesystem or its app-password permissions.
"""
from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from simpleoffice_mini_core import _atomic_write, default_config_path
from tools import service_control
from tools import sftp_setup

DEFAULTS = {"mode": "integrated", "enabled": False, "autostart": False,
            "bind": "127.0.0.1", "port": 2222, "system_port": 22}


def settings_path():
    return default_config_path().parent / "sftp-service.json"


def validate(value):
    if not isinstance(value, dict):
        raise ValueError("SFTP-Einstellungen müssen ein Objekt sein.")
    result = {**DEFAULTS, **value}
    if not isinstance(result["mode"], str) or result["mode"] not in {"integrated", "system"}:
        raise ValueError("SFTP-Betriebsart prüfen.")
    for key in ("enabled", "autostart"):
        if type(result[key]) is not bool:
            raise ValueError("Aktiviert und Autostart müssen boolesche Werte sein.")
    result["bind"] = str(ipaddress.ip_address(str(result["bind"]).strip()))
    for key in ("port", "system_port"):
        if type(result[key]) not in (int, str):
            raise ValueError("SFTP-Port prüfen.")
        result[key] = int(result[key])
        if not 1 <= result[key] <= 65535:
            raise ValueError("SFTP-Port muss zwischen 1 und 65535 liegen.")
    return {key: result[key] for key in DEFAULTS}


def settings():
    path = settings_path()
    if not path.exists():
        return validate({**DEFAULTS, "bind": os.environ.get("SIMPLEOFFICE_SFTP_BIND", DEFAULTS["bind"]),
                         "port": os.environ.get("SIMPLEOFFICE_SFTP_PORT", DEFAULTS["port"])})
    return validate(json.loads(path.read_text(encoding="utf-8")))


def system_service():
    """Inspect fixed local service names, without privileged changes or a shell."""
    if os.name == "nt":
        if not shutil.which("sc.exe"):
            return {"available": False, "running": False, "unit": ""}
        command = ["sc.exe", "query", "sshd"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=2, check=False)
        return {"available": result.returncode == 0,
                "running": "RUNNING" in result.stdout, "unit": "sshd"}
    if shutil.which("systemctl"):
        for unit in ("ssh.service", "sshd.service"):
            result = subprocess.run(
                ["systemctl", "show", unit, "--property=LoadState,ActiveState"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if result.returncode == 0 and "LoadState=loaded" in result.stdout:
                return {"available": True, "running": "ActiveState=active" in result.stdout, "unit": unit}
    return {"available": False, "running": False, "unit": ""}


def status():
    config = settings()
    try:
        system = system_service()
    except (OSError, subprocess.TimeoutExpired):
        system = {"available": False, "running": False, "unit": ""}
    record = service_control.read("sftp")
    running = bool(record and service_control.process_matches(record))
    mode = config["mode"]
    if mode == "system":
        running = system["running"]
        message = "Systemdienst: Betriebssystem-Konten und dessen Dateirechte gelten."
        available = system["available"]
    else:
        message = "Paramiko: SimpleOffice-App-Passwörter, SSH-Schlüssel und Ordnerrechte; keine Shell."
        try:
            available = sftp_setup.dependency() is not None
        except RuntimeError:
            available = False
            message = "Paramiko fehlt. Den plattformspezifischen SFTP-Starter zur Einrichtung verwenden."
    return {"id": "sftp", "name": "SFTP", "settings": config, "config": config,
            "state": "running" if running else ("stopped" if available else "unavailable"),
            "health": {"ok": running, "message": message}, "system_service": system,
            "owner": "system" if mode == "system" else "sftp-process",
            "capabilities": ["start", "stop", "restart", "scan", "settings"],
            "last_error": None}


def safe_status():
    try:
        return status()
    except (ValueError, RuntimeError, OSError):
        return {"id": "sftp", "name": "SFTP", "state": "unavailable", "settings": dict(DEFAULTS),
                "capabilities": ["stop", "scan", "settings"], "health": {"ok": False,
                "message": "SFTP-Konfiguration ist nicht lesbar oder ungültig. Standardwerte werden angezeigt; vollständiges Speichern ersetzt die beschädigte Konfiguration."}}


def save_settings(value):
    with service_control.exclusive_lease(service_control.RUN_DIR / "sftp-control.lock") as acquired:
        if not acquired:
            raise RuntimeError("SFTP-Aktion läuft bereits. Kurz warten.")
        if isinstance(value, dict) and set(DEFAULTS).issubset(value):
            # An explicit complete replacement can repair a corrupt file. Partial
            # updates must still read it so unrelated settings are never reset.
            clean = validate(value)
        else:
            clean = validate({**settings(), **value} if isinstance(value, dict) else value)
        record = service_control.read("sftp")
        if record and service_control.process_matches(record):
            raise RuntimeError("SFTP vor Konfigurationsänderungen stoppen.")
        if clean["mode"] == "system" and not system_service()["available"]:
            raise ValueError("Kein unterstützter SSH-Systemdienst erkannt.")
        _atomic_write(settings_path(), (json.dumps(clean) + "\n").encode())
        return clean


def _ready(config):
    address = ipaddress.ip_address(config["bind"])
    host = ("127.0.0.1" if address.version == 4 else "::1") if address.is_unspecified else str(address)
    try:
        with socket.create_connection((host, config["port"]), timeout=.3) as connection:
            return connection.recv(255).startswith(b"SSH-2.0-paramiko")
    except OSError:
        return False


def _cleanup_failed_start(child):
    """Reap only our new child; never signal a process found via a PID file."""
    if child.poll() is None:
        try:
            child.terminate()
        except ProcessLookupError:
            pass  # The child exited between poll and terminate; wait still reaps it.
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                child.kill()
            except ProcessLookupError:
                pass
            child.wait(timeout=2)
    service_control.unregister("sftp", child.pid)


def _start(config, document_root):
    record = service_control.read("sftp")
    if record and service_control.process_matches(record):
        return
    sftp_setup.initialize(sftp_setup.key_path())
    environment = os.environ.copy()
    environment.update(SIMPLEOFFICE_SFTP_BIND=config["bind"], SIMPLEOFFICE_SFTP_PORT=str(config["port"]),
                       SIMPLEOFFICE_DOCUMENT_ROOT=str(document_root), SIMPLEOFFICE_SFTP_HOST_KEY=str(sftp_setup.key_path()))
    options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    service_control.RUN_DIR.mkdir(parents=True, exist_ok=True)
    with (service_control.RUN_DIR / "sftp.log").open("ab") as log:
        child = subprocess.Popen([sys.executable, "-m", "tools.sftp_setup", "run"],
                                 cwd=sftp_setup.ROOT, env=environment, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, **options)
    ready = False
    try:
        deadline = time.monotonic() + 8
        while child.poll() is None and time.monotonic() < deadline:
            record = service_control.read("sftp")
            if record and int(record["pid"]) == child.pid and _ready(config):
                ready = True
                return
            time.sleep(.1)
        raise RuntimeError("SFTP startet nicht. Portbelegung, Schlüssel und Dienstprotokoll prüfen.")
    finally:
        if not ready:
            _cleanup_failed_start(child)


def action(name, document_root):
    if name not in {"start", "stop", "restart", "scan"}:
        raise ValueError("Unbekannte SFTP-Aktion.")
    if name == "scan":
        row = safe_status()
        return {"state": "completed", "count": 1, "updated_at": time.time(),
                "scope": "Lokaler SFTP-Dienst und Systemdienst", "targets": [row]}
    with service_control.exclusive_lease(service_control.RUN_DIR / "sftp-control.lock") as acquired:
        if not acquired:
            raise RuntimeError("SFTP-Aktion läuft bereits. Kurz warten.")
        try:
            config = settings()
        except (ValueError, OSError):
            if name != "stop":
                raise
            # A damaged config must not make our registered child unstoppable.
            # Never infer an OS service selection from corrupt data.
            if not service_control.stop(timeout=3, roles=["sftp"]):
                raise RuntimeError("SFTP wurde nicht rechtzeitig beendet.")
            return safe_status()
        if name != "stop" and not config["enabled"]:
            raise ValueError("SFTP zuerst in den Einstellungen aktivieren.")
        if config["mode"] == "system":
            system = system_service()
            if not system["available"]:
                raise ValueError("SSH-Systemdienst nicht verfügbar.")
            if os.name == "nt":
                commands = [["sc.exe", "stop", "sshd"], ["sc.exe", "start", "sshd"]] if name == "restart" else [["sc.exe", name, "sshd"]]
            else:
                commands = [["systemctl", "--no-ask-password", name, system["unit"]]]
            for command in commands:
                result = subprocess.run(command, capture_output=True, timeout=5, check=False)
                if result.returncode:
                    raise RuntimeError("Systemdienst konnte nicht gesteuert werden. Dienstrechte im Betriebssystem prüfen.")
        else:
            if name in {"stop", "restart"} and not service_control.stop(timeout=3, roles=["sftp"]):
                raise RuntimeError("SFTP wurde nicht rechtzeitig beendet.")
            if name in {"start", "restart"}:
                _start(config, document_root)
        return safe_status()


def autostart(document_root):
    config = settings()
    # OS services retain their own startup policy; do not mutate it implicitly.
    if config["mode"] == "integrated" and config["enabled"] and config["autostart"]:
        return action("start", document_root)
    return None
