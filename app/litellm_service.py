"""LiteLLM container lifecycle through the existing Mini Service control lease."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time

from simpleoffice_mini_core import _atomic_write
from tools.service_control import exclusive_lease
from .litellm_config import DEFAULTS, gateway_url, persist, prepare, public, secret, settings, settings_path, validate
from .litellm_gateway import probe


def directory():
    return settings_path().parent / "litellm"


def _project():
    return "simpleoffice-litellm-" + hashlib.sha256(str(directory().resolve()).encode()).hexdigest()[:12]


def _docker(args, *, environment=None, timeout=12):
    if not shutil.which("docker"):
        raise RuntimeError("Docker mit Compose v2 fehlt; externen Betrieb wählen oder Docker installieren.")
    result = subprocess.run(["docker", *args], env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError("Docker-Aktion fehlgeschlagen; Installation, Image und Dienstrechte prüfen.")
    return result.stdout


def _running():
    if not shutil.which("docker"):
        return False
    data = _docker(["ps", "--filter", "label=com.docker.compose.project=" + _project(),
                    "--filter", "label=com.docker.compose.service=gateway", "--format", "{{.ID}}"], timeout=3)
    return bool(data.strip())


def _compose(config):
    path = directory() / "compose.json"
    model_config = {"model_list": [{"model_name": config["model"], "litellm_params": {
        "model": config["provider_model"], "api_key": "os.environ/SIMPLEOFFICE_LITELLM_PROVIDER_KEY"}}],
        "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY", "disable_error_logs": True},
        "litellm_settings": {"num_retries": 0, "request_timeout": config["timeout"], "telemetry": False},
        "router_settings": {"num_retries": 0, "timeout": config["timeout"]}}
    _atomic_write(directory() / "config.yaml", json.dumps(model_config).encode())
    compose = {"services": {"gateway": {
        "image": "docker.litellm.ai/berriai/litellm:v" + config["version"],
        "command": ["--config", "/app/config.yaml", "--port", "4000"],
        "ports": [f"127.0.0.1:{config['port']}:4000"],
        "volumes": [str((directory() / "config.yaml").resolve()) + ":/app/config.yaml:ro"],
        "environment": {"LITELLM_MASTER_KEY": "${LITELLM_MASTER_KEY:?required}",
                        "SIMPLEOFFICE_LITELLM_PROVIDER_KEY": "${SIMPLEOFFICE_LITELLM_PROVIDER_KEY:-}",
                        "LITELLM_TELEMETRY": "False"},
        "restart": "on-failure:3" if config["autostart"] else "no",
        "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
        "pids_limit": 128, "mem_limit": "1g",
        # Gateway diagnostics can include provider payloads. Keep them out of the shared journal.
        "logging": {"driver": "none"}}}}
    _atomic_write(path, json.dumps(compose).encode())
    return path


def _environment(config):
    environment = os.environ.copy()
    # No accidental database or debug settings inherited by the container.
    environment["LITELLM_MASTER_KEY"] = secret(config, "api_key")
    environment["SIMPLEOFFICE_LITELLM_PROVIDER_KEY"] = (
        secret(config, "provider_key") if config["provider_key_enc"] else "")
    return environment


def _stop():
    # Label-based ownership, no config parsing/decryption: corrupt config remains stoppable.
    identifiers = _docker(["ps", "-aq", "--filter", "label=com.docker.compose.project=" + _project(),
                           "--filter", "label=com.docker.compose.service=gateway", "--format", "{{.ID}}"])
    ids = identifiers.decode().split()
    if ids:
        if any(len(i) > 64 or not all(c in "0123456789abcdef" for c in i) for i in ids):
            raise RuntimeError("Ungültige Docker-Dienstkennung.")
        _docker(["stop", "--time", "5", *ids])
        _docker(["rm", *ids])


def _record(health):
    _atomic_write(directory() / "health.json", json.dumps({**health, "updated_at": time.time()}).encode())


def record_error(exc):
    # Safe diagnostics only; exception payloads may contain credentials.
    try:
        _record({"ok": False, "code": type(exc).__name__,
                 "message": "Dienstaktion fehlgeschlagen; Konfiguration, Docker und Dateirechte prüfen."})
    except OSError:
        from simpleoffice_service_lifecycle import log_service_event
        log_service_event("litellm", "diagnostic_write_failed", exc=exc)


def autostart():
    config = settings()
    if config["enabled"] and config["mode"] == "local" and config["autostart"]:
        return action("start")
    return None


def status():
    try:
        config = settings()
        path = directory() / "health.json"
        health = json.loads(path.read_text()) if path.exists() else {}
        if not config["enabled"]:
            health = {"ok": False, "message": "LiteLLM deaktiviert.", "code": "disabled"}
        elif health.get("updated_at", 0) < time.time() - 60:
            health = {"ok": False, "message": "Verbindungstest erforderlich.", "code": "unknown"}
        return {"id": "litellm", "name": "LiteLLM Gateway", "settings": public(config),
                "config": public(config), "owner": "docker-compose" if config["mode"] == "local" else "external",
                "state": "disabled" if not config["enabled"] else ("running" if health.get("ok") else "degraded"),
                "capabilities": ["start", "stop", "restart", "scan", "settings"] if config["mode"] == "local" else ["scan", "settings"],
                "health": health, "last_error": None if health.get("ok") or not config["enabled"] else {
                    "message": health.get("message", "Noch kein Verbindungstest."),
                    "action": "Gateway-Key, Modell, Netzwerk und Dienst prüfen."}, "gateway_url": gateway_url(config)}
    except (ValueError, OSError, TypeError):
        return {"id": "litellm", "name": "LiteLLM Gateway", "state": "failed", "settings": {},
                "config": {}, "owner": "docker-compose", "capabilities": ["stop"],
                "health": {"ok": False, "message": "Konfiguration ungültig; Sicherung wiederherstellen."}}


def save_settings(value):
    with exclusive_lease(directory() / "control.lock") as acquired:
        if not acquired:
            raise RuntimeError("LiteLLM-Aktion läuft bereits.")
        clean = prepare(value)
        previous = settings()
        if previous["mode"] == "local" and _running():
            if clean["enabled"]:
                raise ValueError("Lokalen Dienst vor Konfigurationsänderungen stoppen.")
            _stop()
        persist(clean)
        _record({"ok": False, "code": "unknown", "message": "Konfiguration geändert; Verbindungstest erforderlich."})
        return public(clean)


def action(name):
    if name not in {"install", "start", "stop", "restart", "scan"}:
        raise ValueError("Unbekannte LiteLLM-Aktion.")
    with exclusive_lease(directory() / "control.lock") as acquired:
        if not acquired:
            raise RuntimeError("LiteLLM-Aktion läuft bereits.")
        if name == "stop":
            _stop()
            _record({"ok": False, "code": "stopped", "message": "Lokaler Dienst gestoppt."})
            return status()
        config = settings()
        if name == "scan":
            health = probe(config)
            _record(health)
            return {"state": "completed" if health["ok"] else "failed", "health": health,
                    "count": int(health["ok"]), "scope": "LiteLLM Liveness, Readiness, Key und Standardmodell"}
        if config["mode"] != "local" or not config["enabled"]:
            raise ValueError("Aktivierten lokalen Betrieb auswählen.")
        if name == "install":
            if _running():
                raise ValueError("Vor Installation oder Upgrade lokalen Dienst stoppen.")
            _docker(["pull", "docker.litellm.ai/berriai/litellm:v" + config["version"]], timeout=55)
        else:
            if name == "restart":
                _stop()
            path = _compose(config)
            _docker(["compose", "--project-name", _project(), "--file", str(path),
                     "up", "--detach", "--pull", "never"], environment=_environment(config))
            _record({"ok": False, "code": "starting", "message": "Container gestartet; Verbindungstest nach Initialisierung ausführen."})
        return status()


def backup():
    return json.dumps({"schema": 1, "settings": settings()}).encode()


def restore(payload):
    if not isinstance(payload, dict) or set(payload) != {"schema", "settings"} or payload["schema"] != 1:
        raise ValueError("Unbekanntes Backupformat.")
    clean = validate(payload["settings"])
    for key in ("api_key", "provider_key"):
        if clean[key + "_enc"]:
            secret(clean, key)
    clean["enabled"] = False
    clean["autostart"] = False
    with exclusive_lease(directory() / "control.lock") as acquired:
        if not acquired:
            raise RuntimeError("LiteLLM-Aktion läuft bereits.")
        try:
            previous = settings()
        except (ValueError, OSError):
            previous = {**DEFAULTS, "mode": "local"}
        if previous["enabled"] or (previous["mode"] == "local" and _running()):
            raise ValueError("LiteLLM vor Restore deaktivieren.")
        persist(clean)
        _record({"ok": False, "code": "disabled", "message": "Sicherung wiederhergestellt; LiteLLM bleibt deaktiviert."})
    return public(clean)
