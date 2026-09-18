"""Lifecycle adapter for HTTP boot routes owned by the existing Flask server."""
from __future__ import annotations

import os
import time
from flask import current_app
from simpleoffice_mini_core import default_config_path
from simpleoffice_network_boot import load_boot_settings, save_boot_settings, safe_asset_path, list_assets
from simpleoffice_service_lifecycle import application_version, error_detail


def status():
    row = {"id": "http-boot", "name": "HTTP / PXE", "state": "stopped", "version": application_version(), "schema_version": 1,
           "owner": "web", "worker_id": os.getpid(), "started_at": None, "uptime_seconds": None,
           "last_error": None, "last_error_at": None, "requires": ["web"],
           "optional_requires": ["dhcp", "tftp"], "provides": ["http-boot"], "ports": [],
           "capabilities": ["start", "stop", "restart", "scan"], "settings": {},
           "health": {"ok": False, "message": "HTTP-Boot ist deaktiviert."}}
    try:
        config = load_boot_settings(default_config_path())
        row["config"] = config
        row["settings"] = {"enabled": config["enabled"]}
        if "network_boot_http" not in current_app.blueprints:
            row.update(state="unavailable", health={"ok": False, "message": "HTTP-Boot-Routen sind nicht registriert."})
        elif config["enabled"]:
            profiles = {p["id"]: p for p in config["profiles"] if p["enabled"]}
            profile = profiles.get(config["default_profile"])
            if not profile:
                row.update(state="waiting", health={"ok": False, "message": "Aktives Standard-Bootprofil auswählen."})
            else:
                for key in ("kernel", "initrd", "iso"):
                    if profile[key]:
                        safe_asset_path(profile[key], default_config_path())
                row.update(state="running", health={"ok": True, "message": "HTTP-Boot-Routen und lokale Dateien des Standardprofils verfügbar. Externe Chain-Ziele nicht geprüft."})
    except (OSError, ValueError) as exc:
        row.update(state="degraded", last_error=error_detail(exc),
                   health={"ok": False, "message": "Boot-Konfiguration und benötigte Dateien prüfen."})
    return row


def action(action_name):
    path = default_config_path()
    if action_name == "scan":
        targets = list_assets(path, include_hash=False, max_entries=512)
        return {"state": "completed", "updated_at": time.time(), "count": len(targets),
                "targets": targets, "limit": 512, "limited": len(targets) == 512,
                "scope": "Lokale Bootdateien (maximal 512); kein Hashen großer Images."}
    config = load_boot_settings(path)
    # These routes have no dedicated process. Stop persists the serving gate;
    # restart validates/reloads settings without restarting the shared web app.
    if action_name in {"start", "restart"}:
        if "network_boot_http" not in current_app.blueprints:
            raise RuntimeError("HTTP-Boot-Routen sind nicht registriert")
        config["enabled"] = True
    elif action_name == "stop":
        config["enabled"] = False
    else:
        raise ValueError("Unbekannte HTTP-Boot-Aktion")
    save_boot_settings(config, path)
    return status()
