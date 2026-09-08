"""Operational and UX helpers for PrinterShare.

The core print transport remains in printershare.py/printershare_store.py. This
module adds safe read-only APIs, maintenance helpers and response hardening.
"""
from __future__ import annotations

import csv
import io
import os
import platform
import re
import shutil
import sqlite3
import time
import uuid
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, abort, current_app, g, jsonify, request

from .access_control import is_admin
from .auth import login_required
from .federation_store import FederationStore
from .printershare_store import PrinterShareStore, RETENTION_ORDER, normalize_retention


bp = Blueprint("printershare_quickwins", __name__, url_prefix="/printershare")
JOB_STATUSES = {"queued", "spooled", "failed"}
JOB_SOURCES = {"web", "federation"}
MAX_QUERY_LENGTH = 160
MAX_API_JOBS = 200
MAX_API_OFFSET = 5000
ORPHAN_GRACE_SECONDS = 24 * 60 * 60
TEMP_GRACE_SECONDS = 60 * 60
REQUEST_ID_RE = re.compile(r"[^A-Za-z0-9._:-]+")


def _store() -> PrinterShareStore:
    return PrinterShareStore(current_app.config["DOCUMENT_ROOT"], current_app.config["SECRET_KEY"])


def _federation() -> FederationStore:
    return FederationStore(current_app.config["DOCUMENT_ROOT"])


def _printing_policy(peer: dict[str, Any]) -> dict[str, Any]:
    policy = peer.get("policy") or {}
    value = policy.get("printing", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def _may_send(peer: dict[str, Any]) -> bool:
    return _printing_policy(peer).get("send") is True


def _peer_ceiling(peer: dict[str, Any]) -> str:
    return normalize_retention(_printing_policy(peer).get("retention_ceiling"), "no_store")


def _clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _parse_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    candidate = str(value).strip().casefold()
    if candidate in {"1", "true", "yes", "on"}:
        return True
    if candidate in {"0", "false", "no", "off"}:
        return False
    return None


def _username() -> str:
    try:
        return str(g.user["username"])
    except (KeyError, TypeError):
        return ""


def _admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _db(store: PrinterShareStore) -> sqlite3.Connection:
    db = sqlite3.connect(store.jobs_path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    return db


def _safe_job(row: dict[str, Any], *, admin: bool = False) -> dict[str, Any]:
    now = int(time.time())
    expires_at = int(row.get("expires_at") or 0)
    result = {
        "job_id": str(row.get("job_id") or ""),
        "source": str(row.get("source") or ""),
        "source_peer": str(row.get("source_peer") or ""),
        "printer_id": str(row.get("printer_id") or ""),
        "printer_name": str(row.get("printer_name") or ""),
        "status": str(row.get("status") or ""),
        "retention": normalize_retention(row.get("retention")),
        "expires_at": expires_at,
        "retained": bool(row.get("payload_path")),
        "content_type": str(row.get("content_type") or "application/octet-stream"),
        "payload_size": max(0, int(row.get("payload_size") or 0)),
        "payload_sha256": str(row.get("payload_sha256") or ""),
        "policy_revision": str(row.get("policy_revision") or ""),
        "created_at": int(row.get("created_at") or 0),
        "completed_at": int(row.get("completed_at") or 0),
        "expired": bool(expires_at and expires_at <= now),
        "age_seconds": max(0, now - int(row.get("created_at") or now)),
    }
    error = str(row.get("error") or "")
    if error:
        result["error"] = error[:500]
    if admin and row.get("spool_reference"):
        result["spool_reference"] = str(row.get("spool_reference") or "")[:500]
    return result


def _where_clause(*, admin: bool, username: str) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if not admin:
        clauses.extend(["source='web'", "source_peer=?"])
        params.append(username)

    status = str(request.args.get("status") or "").strip().casefold()
    if status in JOB_STATUSES:
        clauses.append("status=?")
        params.append(status)

    source = str(request.args.get("source") or "").strip().casefold()
    if admin and source in JOB_SOURCES:
        clauses.append("source=?")
        params.append(source)

    retention = str(request.args.get("retention") or "").strip().casefold()
    if retention in RETENTION_ORDER:
        clauses.append("retention=?")
        params.append(retention)

    retained = _parse_bool(request.args.get("retained"))
    if retained is True:
        clauses.append("payload_path<>''")
    elif retained is False:
        clauses.append("payload_path='' ")

    query = " ".join(str(request.args.get("q") or "").split())[:MAX_QUERY_LENGTH]
    if query:
        like = f"%{query}%"
        clauses.append("(job_id LIKE ? OR printer_name LIKE ? OR source_peer LIKE ? OR payload_sha256 LIKE ? OR content_type LIKE ?)")
        params.extend([like, like, like, like, like])
    return clauses, params


def _query_jobs(store: PrinterShareStore, *, admin: bool, username: str) -> tuple[list[dict[str, Any]], int]:
    limit = _clamp_int(request.args.get("limit"), 50, 1, MAX_API_JOBS)
    offset = _clamp_int(request.args.get("offset"), 0, 0, MAX_API_OFFSET)
    clauses, params = _where_clause(admin=admin, username=username)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _db(store) as db:
        total = int(db.execute(f"SELECT COUNT(*) FROM print_job{where}", params).fetchone()[0])
        rows = db.execute(
            f"SELECT * FROM print_job{where} ORDER BY created_at DESC, job_id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return [_safe_job(dict(row), admin=admin) for row in rows], total


def _retained_inventory(store: PrinterShareStore) -> tuple[set[str], int, int]:
    referenced: set[str] = set()
    with _db(store) as db:
        for row in db.execute("SELECT payload_path FROM print_job WHERE payload_path<>''"):
            referenced.add(str(row[0]))
    count = total_bytes = 0
    if store.retained.is_dir():
        for path in store.retained.glob("*.printenc"):
            if not path.is_file():
                continue
            count += 1
            try:
                total_bytes += path.stat().st_size
            except OSError:
                pass
    return referenced, count, total_bytes


def maintenance_snapshot(store: PrinterShareStore) -> dict[str, Any]:
    now = int(time.time())
    with _db(store) as db:
        row = db.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status='spooled' THEN 1 ELSE 0 END) AS spooled,
                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                      SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued,
                      SUM(CASE WHEN source='federation' THEN 1 ELSE 0 END) AS federation,
                      SUM(CASE WHEN payload_path<>'' THEN 1 ELSE 0 END) AS retained,
                      SUM(payload_size) AS payload_bytes,
                      MAX(created_at) AS latest
                 FROM print_job"""
        ).fetchone()
        expired = int(db.execute(
            "SELECT COUNT(*) FROM print_job WHERE retention='ttl' AND expires_at>0 AND expires_at<=? AND payload_path<>''",
            (now,),
        ).fetchone()[0])
    _, retained_files, retained_bytes = _retained_inventory(store)
    return {
        "total": int(row["total"] or 0),
        "spooled": int(row["spooled"] or 0),
        "failed": int(row["failed"] or 0),
        "queued": int(row["queued"] or 0),
        "federation": int(row["federation"] or 0),
        "retained": int(row["retained"] or 0),
        "payload_bytes": int(row["payload_bytes"] or 0),
        "retained_files": retained_files,
        "retained_bytes": retained_bytes,
        "expired_pending_purge": expired,
        "latest_created_at": int(row["latest"] or 0),
    }


def run_maintenance(store: PrinterShareStore, *, apply: bool) -> dict[str, int]:
    now = time.time()
    result = {
        "expired_removed": 0,
        "orphan_files_removed": 0,
        "temporary_files_removed": 0,
        "missing_references_cleared": 0,
        "permissions_fixed": 0,
    }
    if apply:
        result["expired_removed"] = store.purge_expired()

    referenced, _, _ = _retained_inventory(store)
    if store.retained.is_dir():
        for path in store.retained.iterdir():
            if not path.is_file():
                continue
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            relative = str(Path("printershare-retained") / path.name)
            stale_temp = path.suffix == ".tmp" and age >= TEMP_GRACE_SECONDS
            stale_orphan = path.suffix == ".printenc" and relative not in referenced and age >= ORPHAN_GRACE_SECONDS
            if stale_temp:
                if apply:
                    path.unlink(missing_ok=True)
                result["temporary_files_removed"] += 1
            elif stale_orphan:
                if apply:
                    path.unlink(missing_ok=True)
                result["orphan_files_removed"] += 1

    missing: list[str] = []
    with _db(store) as db:
        for row in db.execute("SELECT job_id,payload_path FROM print_job WHERE payload_path<>''"):
            relative = str(row["payload_path"] or "")
            path = (store.control / relative).resolve()
            if store.control not in path.parents or not path.is_file():
                missing.append(str(row["job_id"]))
        if apply and missing:
            db.executemany("UPDATE print_job SET payload_path='' WHERE job_id=?", [(job_id,) for job_id in missing])
    result["missing_references_cleared"] = len(missing)

    if apply:
        for path, mode in ((store.retained, 0o700), (store.settings_path, 0o600), (store.jobs_path, 0o600)):
            try:
                os.chmod(path, mode)
                result["permissions_fixed"] += 1
            except OSError:
                pass
        if store.retained.is_dir():
            for path in store.retained.glob("*.printenc"):
                try:
                    os.chmod(path, 0o600)
                    result["permissions_fixed"] += 1
                except OSError:
                    pass
    return result


@bp.before_app_request
def printershare_request_context() -> None:
    if not request.path.startswith(("/printershare", "/federation/v1/print")):
        return
    supplied = REQUEST_ID_RE.sub("-", request.headers.get("X-Request-ID", "").strip())[:64].strip("-._:")
    g.printershare_request_id = supplied or uuid.uuid4().hex[:16]


@bp.after_app_request
def harden_printershare_response(response: Response) -> Response:
    if not request.path.startswith(("/printershare", "/federation/v1/print")):
        return response
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-PrinterShare-Retention-Contract"] = "no-store-aware"
    request_id = getattr(g, "printershare_request_id", "")
    if request_id:
        response.headers["X-Request-ID"] = request_id
    vary = {item.strip() for item in response.headers.get("Vary", "").split(",") if item.strip()}
    vary.add("Cookie")
    response.headers["Vary"] = ", ".join(sorted(vary))
    return response


@bp.get("/api/status")
@login_required
def api_status():
    store = _store()
    settings = store.settings()
    printers = store.printers()
    peers = [peer for peer in _federation().list_peers() if peer.get("enabled") and _may_send(peer)]
    return jsonify({
        "schema": 2,
        "server_time": int(time.time()),
        "request_id": getattr(g, "printershare_request_id", ""),
        "enabled": settings["enabled"],
        "federation_enabled": settings["federation_enabled"],
        "default_retention": settings["default_retention"],
        "federation_default_retention": settings["federation_default_retention"],
        "ttl_seconds": settings["ttl_seconds"],
        "max_job_bytes": settings["max_job_bytes"],
        "printer_count": len(printers),
        "shared_printer_count": sum(1 for item in printers if item.get("federation_shared")),
        "send_peer_count": len(peers),
        "platform": platform.system() or "unknown",
        "stats": maintenance_snapshot(store),
        "admin": bool(is_admin(g.user)),
    })


@bp.get("/api/health")
@login_required
def api_health():
    store = _store()
    system = platform.system()
    checks = {
        "storage_directory": store.control.is_dir(),
        "storage_writable": os.access(store.control, os.W_OK),
        "database_exists": store.jobs_path.is_file(),
        "printer_detected": bool(store.printers()),
        "cups_lp": bool(shutil.which("lp")) if system in {"Linux", "Darwin"} else None,
        "cups_lpr": bool(shutil.which("lpr")) if system in {"Linux", "Darwin"} else None,
        "powershell": bool(shutil.which("powershell.exe") or shutil.which("pwsh")) if system == "Windows" else None,
    }
    degraded = any(value is False for key, value in checks.items() if key not in {"printer_detected", "cups_lp", "cups_lpr"})
    return jsonify({"schema": 1, "ok": not degraded, "checks": checks, "server_time": int(time.time())})


@bp.get("/api/settings")
@login_required
def api_settings():
    settings = _store().settings()
    return jsonify({
        "schema": 1,
        "enabled": settings["enabled"],
        "federation_enabled": settings["federation_enabled"],
        "default_retention": settings["default_retention"],
        "federation_default_retention": settings["federation_default_retention"],
        "ttl_seconds": settings["ttl_seconds"],
        "max_job_bytes": settings["max_job_bytes"],
        "retention_modes": list(RETENTION_ORDER),
    })


@bp.get("/api/printers")
@login_required
def api_printers():
    printers = [{
        "printer_id": item["printer_id"], "label": item["label"], "name": item["name"],
        "kind": item["kind"], "backend": item["backend"], "is_default": bool(item["is_default"]),
        "federation_shared": bool(item["federation_shared"]),
    } for item in _store().printers()]
    return jsonify({"schema": 1, "count": len(printers), "printers": printers})


@bp.get("/api/peers")
@login_required
def api_peers():
    peers = []
    for peer in _federation().list_peers():
        if not peer.get("enabled") or not _may_send(peer):
            continue
        peers.append({
            "peer_id": str(peer.get("peer_id") or ""),
            "label": str(peer.get("label") or peer.get("peer_id") or ""),
            "retention_ceiling": _peer_ceiling(peer),
            "enabled": True,
        })
    peers.sort(key=lambda item: (item["label"].casefold(), item["peer_id"]))
    return jsonify({"schema": 1, "count": len(peers), "peers": peers})


@bp.get("/api/jobs")
@login_required
def api_jobs():
    store = _store()
    store.purge_expired()
    admin = bool(is_admin(g.user))
    jobs, total = _query_jobs(store, admin=admin, username=_username())
    return jsonify({"schema": 2, "total": total, "count": len(jobs), "jobs": jobs, "server_time": int(time.time())})


@bp.get("/api/jobs/<job_id>")
@login_required
def api_job(job_id: str):
    if len(job_id) > 80:
        abort(404)
    store = _store()
    with _db(store) as db:
        row = db.execute("SELECT * FROM print_job WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        abort(404)
    admin = bool(is_admin(g.user))
    value = dict(row)
    if not admin and (value.get("source") != "web" or str(value.get("source_peer") or "") != _username()):
        abort(404)
    return jsonify({"schema": 1, "job": _safe_job(value, admin=admin)})


@bp.get("/api/jobs.csv")
@login_required
def api_jobs_csv():
    store = _store()
    jobs, _ = _query_jobs(store, admin=bool(is_admin(g.user)), username=_username())
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["job_id", "created_at", "printer", "source", "source_peer", "status", "retention", "retained", "size", "sha256"])
    for job in jobs:
        writer.writerow([
            job["job_id"], job["created_at"], job["printer_name"], job["source"], job["source_peer"],
            job["status"], job["retention"], int(job["retained"]), job["payload_size"], job["payload_sha256"],
        ])
    response = Response(output.getvalue(), mimetype="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = "attachment; filename=printershare-jobs.csv"
    return response


@bp.post("/admin/maintenance")
@_admin_required
def admin_maintenance():
    apply = str(request.form.get("apply") or request.args.get("apply") or "").strip() == "1"
    store = _store()
    result = run_maintenance(store, apply=apply)
    return jsonify({"schema": 1, "applied": apply, "result": result, "stats": maintenance_snapshot(store)})


def init_app(app) -> None:
    if "printershare_quickwins" not in app.blueprints:
        app.register_blueprint(bp)
