"""Bounded collectors. No collector runs in a Flask request."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_HTTP_BYTES = 1024 * 1024


class CollectionError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def json_path(data, path: str):
    current = data
    for part in path.replace("[", ".").replace("]", "").split("."):
        if not part: continue
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise CollectionError("json_path_missing") from exc
    return current


def _number(value):
    try: result = float(value)
    except (TypeError, ValueError) as exc: raise CollectionError("not_numeric") from exc
    if not math.isfinite(result): raise CollectionError("not_finite")
    return result


def collect_linux(config):
    metric = str(config.get("metric", "")).strip() or "load1"
    if metric in {"load1", "load5", "load15"}:
        return os.getloadavg()[{"load1": 0, "load5": 1, "load15": 2}[metric]]
    if metric == "memory_used_percent":
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1); values[key] = int(value.split()[0])
        return 100 * (1 - values["MemAvailable"] / values["MemTotal"])
    if metric == "disk_used_percent":
        usage = shutil.disk_usage(str(config.get("path", "")).strip() or "/"); return 100 * usage.used / usage.total
    if metric == "temperature_c":
        paths = sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
        if not paths: raise CollectionError("temperature_unavailable")
        value = float(paths[0].read_text().strip()); return value / 1000 if value > 500 else value
    raise CollectionError("linux_metric_unknown")


def _safe_file_path(root, configured):
    root = Path(root).resolve(); candidate = (root / str(configured).lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents: raise CollectionError("path_outside_root")
    if candidate.is_symlink(): raise CollectionError("symlink_denied")
    return candidate


def collect_file(root, config):
    path = _safe_file_path(root, config.get("path", "."))
    if not path.exists(): raise CollectionError("path_missing")
    metric, recursive = str(config.get("metric", "")).strip() or "count", bool(config.get("recursive", True))
    maximum, count, size = max(1, min(int(config.get("max_entries", 100000)), 1000000)), 0, 0
    iterator = path.rglob("*") if recursive and path.is_dir() else (path.iterdir() if path.is_dir() else [path])
    for item in iterator:
        try:
            if item.resolve().relative_to(Path(root).resolve()).parts[:1] == (".simpleoffice-meta",):
                continue
        except ValueError:
            continue
        if item.is_symlink(): continue
        if item.is_file():
            count += 1; size += item.stat().st_size
            if count > maximum: raise CollectionError("entry_limit")
    if metric == "count": return count
    if metric == "total_bytes": return size
    if metric == "mtime": return path.stat().st_mtime
    raise CollectionError("file_metric_unknown")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError("redirect_denied")


def _allowed_http_host(host):
    allowed = {x.strip().casefold() for x in os.environ.get("SIMPLEOFFICE_SENSOR_ALLOWED_HOSTS", "localhost,127.0.0.1,::1").split(",") if x.strip()}
    return host.casefold() in allowed


def collect_http(config):
    url = str(config.get("url", "")); parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None: raise CollectionError("url_invalid")
    if not _allowed_http_host(parsed.hostname): raise CollectionError("host_denied")
    try:
        for info in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)):
            ipaddress.ip_address(info[4][0])
    except (OSError, ValueError) as exc: raise CollectionError("dns_failed") from exc
    headers = {"Accept": "application/json", "User-Agent": "SimpleOffice4Me-Datalogger/1"}
    env_name = str(config.get("header_env", ""))
    if env_name:
        if not env_name.startswith("SIMPLEOFFICE_SENSOR_"): raise CollectionError("secret_reference_invalid")
        secret = os.environ.get(env_name)
        if not secret: raise CollectionError("secret_missing")
        headers[str(config.get("header_name", "Authorization"))[:80]] = secret
    try:
        response = build_opener(_NoRedirect).open(Request(url, headers=headers), timeout=max(1, min(int(config.get("timeout", 5)), 15)))
        content_type = response.headers.get_content_type()
        body = response.read(MAX_HTTP_BYTES + 1)
    except CollectionError: raise
    except Exception as exc: raise CollectionError("http_failed") from exc
    if len(body) > MAX_HTTP_BYTES: raise CollectionError("response_too_large")
    if content_type not in {"application/json", "application/problem+json"} and not content_type.endswith("+json"): raise CollectionError("content_type_invalid")
    try: data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise CollectionError("json_invalid") from exc
    return _number(json_path(data, str(config.get("json_path", "value"))))


def collect_lm_sensors(config):
    try:
        result = subprocess.run(["sensors", "-j"], stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=10, text=True)
        data = json.loads(result.stdout)
    except FileNotFoundError as exc: raise CollectionError("lm_sensors_missing") from exc
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc: raise CollectionError("lm_sensors_failed") from exc
    return _number(json_path(data, str(config.get("json_path", ""))))


def collect(kind, config, document_root):
    if kind == "linux": return collect_linux(config)
    if kind == "file": return collect_file(document_root, config)
    if kind == "http_json": return collect_http(config)
    if kind == "lm_sensors": return collect_lm_sensors(config)
    raise CollectionError("kind_unknown")
