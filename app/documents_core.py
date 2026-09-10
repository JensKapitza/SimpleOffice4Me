"""Authenticated web pages for document versions, notes and audit history."""

from __future__ import annotations

import io
import json
import mimetypes
import os
import shutil
import subprocess
import secrets
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from calendar import month_name, monthcalendar
from datetime import date, datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .document_store import DocumentStore
from .contact_store import ContactStore
from .calendar_store import CalendarStore
from .calendar_collections import CalendarCollections
from .google_calendar_sync import GoogleCalendarError, GoogleCalendarSync
from .caldav_scheduling import SchedulingAccess, local_calendar_address
from .itip import ItipConflict, ItipStore, MAX_MESSAGE_BYTES
from .ics_preview import MAX_PREVIEW_BYTES, preview_ics
from .todo_store import TodoStore
from .settings_store import SettingsStore
from .form_store import FormStore
from .project_store import ProjectStore
from .replication_store import CATEGORIES, ReplicationStore
from .object_store import ObjectStore
from .attachment_security import AttachmentSecurity, ClamAV
from .preview_service import PreviewService
from .db import get_db
from .setup_store import SetupStore
from .mail_client import MailStore, SmtpSubmission


bp = Blueprint("documents", "app.documents", url_prefix="/documents")


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _contacts() -> ContactStore:
    return ContactStore(current_app.config["DOCUMENT_ROOT"])


def _calendar() -> CalendarStore:
    return CalendarStore(current_app.config["DOCUMENT_ROOT"])


def _itip() -> ItipStore:
    return ItipStore(current_app.config["DOCUMENT_ROOT"])


def _calendars() -> CalendarCollections:
    return CalendarCollections(current_app.config["DOCUMENT_ROOT"])


def _google_calendar() -> GoogleCalendarSync:
    return GoogleCalendarSync(current_app.config["DOCUMENT_ROOT"])


def _scheduling_access() -> SchedulingAccess:
    return SchedulingAccess(current_app.config["DOCUMENT_ROOT"])


def _todos() -> TodoStore:
    return TodoStore(current_app.config["DOCUMENT_ROOT"])


def _released_eml_attachments(store: DocumentStore, document: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve only explicitly linked clean attachments; never scan the catalog."""
    result = []
    for document_id in document.get("attributes", {}).get("released_eml_attachments", []):
        try: item = store.get_document(str(document_id))
        except ValueError: continue
        if item.get("attributes", {}).get("malware_scan", {}).get("verdict") == "clean":
            result.append(item)
    return result


def _settings() -> SettingsStore:
    return SettingsStore(current_app.config["DOCUMENT_ROOT"])


def _setup() -> SetupStore:
    return SetupStore(current_app.config["DOCUMENT_ROOT"])


def _mail() -> MailStore:
    secret = current_app.config["SECRET_KEY"]
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return MailStore(current_app.config["DOCUMENT_ROOT"], raw)


def _remote_setup_context(username: str) -> dict[str, Any]:
    """Return display-safe service state and request-derived client URLs."""
    from .ssh_keys import keys_for
    from .webdav import credentials_for

    root = request.url_root.rstrip("/")
    host = request.host.split(":", 1)[0].strip("[]") or "server.example"
    carddav = _contacts().carddav()
    carddav_enabled = any(item.get("username") == username and item.get("enabled") is True for item in carddav.get("accounts", []))
    caldav_enabled = any(item.get("username") == username and item.get("enabled") is True for item in _calendars()._read_auth().get("accounts", []))
    try:
        sftp_port = int(os.environ.get("SIMPLEOFFICE_SFTP_PORT", "2222"))
    except ValueError:
        sftp_port = 2222
    if not 1 <= sftp_port <= 65535:
        sftp_port = 2222
    host_key = Path(os.environ.get("SIMPLEOFFICE_SFTP_HOST_KEY", str(Path(current_app.root_path).parent / "instance" / "sftp_host_rsa_key"))).expanduser()
    try:
        import paramiko  # noqa: F401
        sftp_dependency = True
    except ImportError:
        sftp_dependency = False
    return {
        "username": username, "origin": root, "host": host,
        "secure_transport": request.is_secure or host in {"localhost", "127.0.0.1", "::1"},
        "webdav_url": f"{root}/webdav/files/{username}/",
        "caldav_url": f"{root}/caldav/calendars/{username}/",
        "caldav_task_lists": [{"name": item["name"], "url": f"{root}/caldav/calendars/{username}/" + ("tasks" if item["list_id"] == TodoStore.default_list_id(username) else "tasks-" + item["list_id"]) + "/"} for item in _todos().lists(username) if not item.get("archived")],
        "carddav_url": f"{root}/carddav/addressbooks/{username}/contacts/",
        "webdav_enabled": any(not item.get("expired") for item in credentials_for(username)),
        "caldav_enabled": caldav_enabled, "carddav_enabled": carddav_enabled,
        "sftp_port": sftp_port, "sftp_ready": bool(host_key.is_file() and sftp_dependency),
        "sftp_key_ready": bool(host_key.is_file()), "sftp_dependency": sftp_dependency,
        "ssh_key_count": len([item for item in keys_for(current_app.config["DOCUMENT_ROOT"], username) if not item["expired"]]),
        "sshfs_command": (
            f"sshfs -p {sftp_port} {username}@{host}:/ ~/SimpleOffice -o "
            "IdentityFile=~/.ssh/id_ed25519,IdentitiesOnly=yes,reconnect,"
            "ServerAliveInterval=15,ServerAliveCountMax=3"
        ),
        "rsync_enabled": os.environ.get("SIMPLEOFFICE_RSYNC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        "rsync_pull_command": (
            f"rsync -a --delete -e 'ssh -p {sftp_port} -i ~/.ssh/id_ed25519' "
            f"{username}@{host}:/Projekte/ ./Projekte/"
        ),
        "rsync_push_command": (
            f"rsync -a --delete -e 'ssh -p {sftp_port} -i ~/.ssh/id_ed25519' "
            f"./Projekte/ {username}@{host}:/Projekte/"
        ),
        "nautilus_sftp": f"sftp://{username}@{host}:{sftp_port}/",
    }


def _forms() -> FormStore:
    return FormStore(current_app.config["DOCUMENT_ROOT"])


def _projects() -> ProjectStore:
    return ProjectStore(current_app.config["DOCUMENT_ROOT"])


def _replication() -> ReplicationStore:
    return ReplicationStore(current_app.config["DOCUMENT_ROOT"])


def _objects() -> ObjectStore:
    return ObjectStore(current_app.config["DOCUMENT_ROOT"])


def _attachment_security() -> AttachmentSecurity:
    return AttachmentSecurity(current_app.config["DOCUMENT_ROOT"])


def _security_admin(actor: str) -> bool:
    configured = {item.strip() for item in os.environ.get("SIMPLEOFFICE_SECURITY_ADMINS", "").split(",") if item.strip()}
    return actor in configured


def _form_relation_choices(form: dict, actor: str) -> dict[str, list[tuple[str, str]]]:
    """Resolve form relations, including the canonical contact master data."""
    choices: dict[str, list[tuple[str, str]]] = {}
    for field in form.get("fields", []):
        if field.get("type") != "relation":
            continue
        target_id = field.get("relation_form", "")
        if target_id == "contact":
            choices[field["key"]] = [
                (item["contact_id"], item.get("fields", {}).get("display_name", item["contact_id"]))
                for item in _contacts().contacts(actor)
            ]
            continue
        try:
            target = _forms().definition(target_id)
            choices[field["key"]] = [
                (item["record_id"], item.get("values", {}).get(target["title_field"], item["record_id"]))
                for item in _forms().records(target_id)
            ]
        except ValueError:
            choices[field["key"]] = []
    return choices


def _invoice_products() -> list[dict[str, str]]:
    """Small product projection used by the invoice-position dropdown."""
    try:
        product_form = _forms().definition("product")
    except ValueError:
        return []
    return [
        {
            "id": item["record_id"],
            "name": item.get("values", {}).get(product_form["title_field"], item["record_id"]),
            "description": item.get("values", {}).get("description", ""),
            "unit_price": item.get("values", {}).get("sales_price", ""),
            "tax_rate": item.get("values", {}).get("tax_rate", "19"),
        }
        for item in _forms().records("product")
    ]


def _system_overview() -> dict:
    root = _store().root
    storage = shutil.disk_usage(root)
    # Enumerating every OS mount can block login on unavailable SMB/NFS media.
    # External archives remain available through their explicit discovery UI.
    return {"time": datetime.now().astimezone(), "root": str(root), "storage": storage}


def _calendar_tags() -> list[dict[str, str]]:
    return [
        {"name": tag.strip(), "visibility": visibility}
        for visibility in ("private", "family", "external")
        for tag in request.form.get(f"{visibility}_tags", "").split(",")
        if tag.strip()
    ]


def _calendar_metadata() -> dict[str, Any]:
    conferences = []
    for line in request.form.get("conferences", "").splitlines():
        if not line.strip():
            continue
        uri, label, features = (line.split("|", 2) + ["", ""])[:3]
        conferences.append({
            "uri": uri.strip(),
            "label": label.strip(),
            "features": [item.strip() for item in features.split(",") if item.strip()],
        })
    return {
        "ical_status": request.form.get("ical_status", "confirmed"),
        "transparency": request.form.get("transparency", "opaque"),
        "classification": request.form.get("classification", "private"),
        "priority": request.form.get("priority", "0"),
        "location": request.form.get("location", ""),
        "event_url": request.form.get("event_url", ""),
        "resources": [item.strip() for item in request.form.get("resources", "").split(",") if item.strip()],
        "conferences": conferences,
        "appointment_type": request.form.get("appointment_type", ""),
        "attendance": request.form.get("attendance", ""),
        "billable": request.form.get("billable", ""),
        "billing_description": request.form.get("billing_description", ""),
        "billing_quantity": request.form.get("billing_quantity", "1"),
        "billing_net_price": request.form.get("billing_net_price", "0"),
        "billing_vat_rate": request.form.get("billing_vat_rate", "19"),
        "billing_currency": request.form.get("billing_currency", "EUR"),
    }


def _document_or_404(document_id: str) -> dict:
    try:
        return _store().get_document(document_id)
    except ValueError:
        abort(404)


def _preview_data(document: dict) -> dict[str, str]:
    suffix = Path(str(document.get("last_path", ""))).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}: kind, icon = "image", "fa-file-image"
    elif suffix == ".pdf": kind, icon = "pdf", "fa-file-pdf"
    elif suffix in {".mp3", ".wav", ".ogg", ".m4a", ".flac"}: kind, icon = "audio", "fa-file-audio"
    elif suffix in {".mp4", ".mkv", ".mov", ".avi", ".webm"}: kind, icon = "video", "fa-file-video"
    elif document.get("extracted_text") or document.get("ocr_text"): kind, icon = "text", "fa-file-lines"
    elif suffix in {".doc", ".docx", ".odt", ".rtf"}: kind, icon = "document", "fa-file-word"
    elif suffix in {".xls", ".xlsx", ".ods", ".csv"}: kind, icon = "document", "fa-file-excel"
    else: kind, icon = "file", "fa-file"
    return {"kind": kind, "icon": icon, "mime": mimetypes.guess_type(str(document.get("last_path", "")))[0] or "application/octet-stream"}


def _is_unprocessed(document: dict) -> bool:
    """Inbox contains only new files without a human note, state or relation."""
    return document.get("state", "new") == "new" and not document.get("notes") and not document.get("relationships")


def _document_tree(documents: list[dict]) -> dict:
    """Build a folder tree for a compact, progressively disclosed document view."""
    root = {"folders": {}, "documents": [], "count": 0}
    for document in documents:
        current = root; current["count"] += 1
        parts = [part for part in str(document.get("last_path", "")).split("/") if part]
        for folder in parts[:-1]:
            current = current["folders"].setdefault(folder, {"folders": {}, "documents": [], "count": 0})
            current["count"] += 1
        current["documents"].append(document)
    return root

# Route modules intentionally import private helpers too so existing
# app.documents behavior stays source-compatible after modularization.
__all__ = [name for name in globals() if not name.startswith("__")]
