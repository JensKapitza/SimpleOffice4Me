"""Safe, cached runtime inventory for the administrator UI."""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import sqlite3
import subprocess
import time
from functools import lru_cache
from pathlib import Path

from flask import current_app

from .host_automation import HostAutomationStore, recommended_idle_rule
from .host_services import host_services_snapshot
from .system_identity import application_version, system_info
from .system_management import (
    COCKPIT_PARITY,
    QNAP_PARITY,
    StorageRoleStore,
    format_bytes,
    health_checks,
    system_snapshot,
)

PACKAGE_NAMES = (
    "Flask", "Werkzeug", "Jinja2", "beautifulsoup4", "Pillow",
    "reportlab", "pypdf", "waitress", "cryptography", "paramiko", "pip-audit",
)

EXTERNAL_TOOLS = (
    ("ClamAV-Daemonprüfung", "ClamAV daemon scan", ("clamdscan", "--version")),
    ("ClamAV-Dateiprüfung", "ClamAV file scan", ("clamscan", "--version")),
    ("ClamAV-Signaturen", "ClamAV signatures", ("freshclam", "--version")),
    ("Ghostscript / PDF-A", "Ghostscript / PDF-A", ("gs", "--version")),
    ("veraPDF", "veraPDF", ("verapdf", "--version")),
    ("Java / EN16931", "Java / EN16931", ("java", "-version")),
    ("LibreOffice", "LibreOffice", ("libreoffice", "--version")),
    ("ImageMagick", "ImageMagick", ("magick", "--version")),
    ("ImageMagick (alt)", "ImageMagick legacy", ("convert", "--version")),
    ("Poppler-PDF-Text", "Poppler PDF text", ("pdftotext", "-v")),
    ("Poppler-PDF-Bilder", "Poppler PDF images", ("pdfimages", "-v")),
    ("Poppler-Vorschau", "Poppler preview", ("pdftoppm", "-v")),
    ("Tesseract-Texterkennung", "Tesseract OCR", ("tesseract", "--version")),
    ("FFmpeg", "FFmpeg", ("ffmpeg", "-version")),
    ("Git-Historie", "Git history", ("git", "--version")),
    ("rsync", "rsync", ("rsync", "--version")),
    ("restic-Sicherung", "restic backup", ("restic", "version")),
    ("OpenSSH-Client", "OpenSSH client", ("ssh", "-V")),
)


def _safe_line(value: str) -> str:
    cleaned = " ".join(value.replace("\x00", "").split())
    return cleaned[:400]


def _tool_version(label_de: str, label_en: str, command: tuple[str, ...]) -> dict[str, str]:
    if os.name == "nt" and command[0] == "convert":
        return {"label_de": label_de, "label_en": label_en, "command": command[0], "status": "missing", "version": ""}
    executable = shutil.which(command[0])
    if executable is None:
        return {"label_de": label_de, "label_en": label_en, "command": command[0], "status": "missing", "version": ""}
    safe_environment = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "NO_COLOR": "1"}
    try:
        result = subprocess.run(
            (executable, *command[1:]), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, errors="replace", timeout=4,
            check=False, env=safe_environment,
        )
    except subprocess.TimeoutExpired:
        return {"label_de": label_de, "label_en": label_en, "command": Path(executable).name, "status": "timeout", "version": ""}
    except OSError:
        return {"label_de": label_de, "label_en": label_en, "command": Path(executable).name, "status": "error", "version": ""}
    lines = [line for line in (result.stdout + "\n" + result.stderr).splitlines() if line.strip()]
    version = _safe_line(lines[0]) if lines else ""
    status = "available" if version or result.returncode == 0 else "error"
    return {"label_de": label_de, "label_en": label_en, "command": Path(executable).name, "status": status, "version": version}


def _package_versions() -> list[dict[str, str]]:
    result = []
    for name in PACKAGE_NAMES:
        try:
            version = importlib.metadata.version(name)
            status = "available"
        except importlib.metadata.PackageNotFoundError:
            version = ""
            status = "missing"
        result.append({"name": name, "version": version, "status": status})
    return result


@lru_cache(maxsize=4)
def _cached_inventory(process_id: int, five_minute_bucket: int, document_root: str, database_path: str) -> dict[str, object]:
    app = current_app._get_current_object()
    blueprints = []
    for name, blueprint in sorted(app.blueprints.items()):
        route_count = sum(1 for rule in app.url_map.iter_rules() if rule.endpoint.startswith(name + "."))
        blueprints.append({"name": name, "url_prefix": blueprint.url_prefix or "–", "routes": route_count})

    host = system_snapshot(document_root)
    storage_roles = StorageRoleStore(document_root).all()
    host["storage_roles"] = storage_roles
    host["health_checks"] = health_checks(host, storage_roles)
    usage = host.get("document_usage", {}) if isinstance(host.get("document_usage"), dict) else {}
    host["document_usage_display"] = {
        "total": format_bytes(usage.get("total", 0)),
        "used": format_bytes(usage.get("used", 0)),
        "free": format_bytes(usage.get("free", 0)),
    }
    host["cockpit_parity"] = [{"key": key, "label": label, "status": status} for key, label, status in COCKPIT_PARITY]
    host["qnap_parity"] = [{"key": key, "label": label, "status": status} for key, label, status in QNAP_PARITY]
    host["services"] = host_services_snapshot()
    host["automations"] = HostAutomationStore(document_root).all()
    host["recommended_idle_rule"] = recommended_idle_rule()

    return {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "system": system_info(include_request=False),
        "application": {
            "version": application_version(),
            "environment": "development" if app.debug else "production",
            "wsgi": type(app.wsgi_app).__name__,
            "sqlite": sqlite3.sqlite_version,
            "document_root": document_root,
            "database": database_path,
            "upload_limit_mib": int(app.config["MAX_CONTENT_LENGTH"]) // (1024 * 1024),
            "mcp_enabled": bool(app.config.get("MCP_ENABLED")),
            "webdav_clamav": bool(app.config.get("WEBDAV_UPLOAD_SCAN")),
        },
        "modules": blueprints,
        "packages": _package_versions(),
        "tools": [_tool_version(label_de, label_en, command) for label_de, label_en, command in EXTERNAL_TOOLS],
        "host_management": host,
    }


def runtime_inventory() -> dict[str, object]:
    return _cached_inventory(
        os.getpid(), int(time.monotonic() // 300),
        str(Path(current_app.config["DOCUMENT_ROOT"]).resolve()),
        str(Path(current_app.config["DATABASE"]).resolve()),
    )


def clear_runtime_inventory() -> None:
    _cached_inventory.cache_clear()


# ``runtime_inventory`` is imported by app.admin during application bootstrap.
# Register the administrator-only service blueprints here to keep optional
# service management isolated from the large central application module.
from . import app as _flask_app
from .mini_services_admin import bp as _mini_services_admin_bp
from .remote_admin import bp as _remote_admin_bp
for _service_bp in (_mini_services_admin_bp, _remote_admin_bp):
    if _service_bp.name not in _flask_app.blueprints:
        _flask_app.register_blueprint(_service_bp)
