"""Bounded collectors. No collector runs in a Flask request."""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_HTTP_BYTES = 1024 * 1024

_LOGGER = logging.getLogger(__name__)

DEFAULT_SENSOR_ALLOWED_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
MAX_SENSOR_ALLOWED_HOSTS = 128
MAX_SENSOR_ALLOWED_HOST_CHARS = 253
MAX_SENSOR_SECRET_CHARS = 2000


def normalize_sensor_allowed_hosts(value: object) -> list[str]:
    """Coerce stored allowed hosts to a bounded list (default loopback set)."""
    items = value if isinstance(value, list) else list(DEFAULT_SENSOR_ALLOWED_HOSTS)
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            _LOGGER.warning("Invalid stored sensor host; ignoring it")
            continue
        text = item.strip().casefold()
        if not text or len(text) > MAX_SENSOR_ALLOWED_HOST_CHARS or text in result:
            _LOGGER.warning("Invalid stored sensor host; ignoring it")
            continue
        result.append(text)
        if len(result) >= MAX_SENSOR_ALLOWED_HOSTS:
            break
    return result or ["localhost"]


def validate_sensor_allowed_hosts(value: object) -> list[str]:
    """Validate allowed hosts for saving; entries must be non-empty strings."""
    if not isinstance(value, list) or not value:
        raise ValueError("Die Sensor-Hostliste darf nicht leer sein.")
    if len(value) > MAX_SENSOR_ALLOWED_HOSTS:
        raise ValueError("Die Sensor-Hostliste darf höchstens 128 Einträge haben.")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Die Sensor-Hostliste darf nur nicht-leere Einträge haben.")
        if len(item.strip()) > MAX_SENSOR_ALLOWED_HOST_CHARS:
            raise ValueError("Ein Sensor-Host ist zu lang.")
    return normalize_sensor_allowed_hosts(value)


def _stored_allowed_hosts(document_root: object) -> list[str]:
    """Read persisted allowed hosts; fail closed to the loopback default."""
    if document_root is None:
        return list(DEFAULT_SENSOR_ALLOWED_HOSTS)
    try:
        from .settings_store import SettingsStore

        section = SettingsStore(document_root).settings().get("datalogger", {})
        hosts = section.get("allowed_hosts", list(DEFAULT_SENSOR_ALLOWED_HOSTS))
        return normalize_sensor_allowed_hosts(hosts)
    except (OSError, ValueError):
        return list(DEFAULT_SENSOR_ALLOWED_HOSTS)


class CollectionError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def json_path(data, path: str):
    current = data
    for part in path.replace("[", ".").replace("]", "").split("."):
        if not part:
            continue
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise CollectionError("json_path_missing") from exc
    return current


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CollectionError("not_numeric") from exc
    if not math.isfinite(result):
        raise CollectionError("not_finite")
    return result


def _bounded_int(value, default: int, minimum: int, maximum: int, code: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CollectionError(code) from exc
    return max(minimum, min(parsed, maximum))


def collect_linux(config):
    metric = str(config.get("metric", "")).strip() or "load1"
    if metric in {"load1", "load5", "load15"}:
        try:
            return os.getloadavg()[{"load1": 0, "load5": 1, "load15": 2}[metric]]
        except (AttributeError, OSError) as exc:
            raise CollectionError("load_unavailable") from exc
    if metric == "memory_used_percent":
        values = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, value = line.split(":", 1)
                values[key] = int(value.split()[0])
            total = values["MemTotal"]
            available = values["MemAvailable"]
        except (OSError, KeyError, ValueError, IndexError) as exc:
            raise CollectionError("memory_unavailable") from exc
        if total <= 0:
            raise CollectionError("memory_unavailable")
        return 100 * (1 - available / total)
    if metric == "disk_used_percent":
        try:
            usage = shutil.disk_usage(str(config.get("path", "")).strip() or "/")
        except OSError as exc:
            raise CollectionError("disk_unavailable") from exc
        if usage.total <= 0:
            raise CollectionError("disk_unavailable")
        return 100 * usage.used / usage.total
    if metric == "temperature_c":
        paths = sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
        if not paths:
            raise CollectionError("temperature_unavailable")
        try:
            value = float(paths[0].read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as exc:
            raise CollectionError("temperature_unavailable") from exc
        if not math.isfinite(value):
            raise CollectionError("temperature_unavailable")
        return value / 1000 if value > 500 else value
    raise CollectionError("linux_metric_unknown")


def _safe_file_path(root, configured):
    root = Path(root).expanduser().resolve()
    raw = Path(str(configured or ".").lstrip("/"))
    lexical = root / raw
    # Reject symlinks in any existing path segment before resolve() follows them.
    current = root
    for part in raw.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise CollectionError("symlink_denied")
        except OSError as exc:
            raise CollectionError("path_unavailable") from exc
    try:
        candidate = lexical.resolve()
    except OSError as exc:
        raise CollectionError("path_unavailable") from exc
    if candidate != root and root not in candidate.parents:
        raise CollectionError("path_outside_root")
    return candidate


def collect_file(root, config):
    path = _safe_file_path(root, config.get("path", "."))
    if not path.exists():
        raise CollectionError("path_missing")
    metric = str(config.get("metric", "")).strip() or "count"
    recursive = bool(config.get("recursive", True))
    maximum = _bounded_int(config.get("max_entries", 100000), 100000, 1, 1_000_000, "entry_limit_invalid")
    count = 0
    size = 0
    root_path = Path(root).expanduser().resolve()
    iterator = path.rglob("*") if recursive and path.is_dir() else (path.iterdir() if path.is_dir() else [path])
    for item in iterator:
        try:
            resolved = item.resolve()
            relative = resolved.relative_to(root_path)
            if relative.parts[:1] == (".simpleoffice-meta",):
                continue
        except (OSError, ValueError):
            continue
        if item.is_symlink():
            continue
        try:
            if item.is_file():
                count += 1
                size += item.stat().st_size
                if count > maximum:
                    raise CollectionError("entry_limit")
        except OSError:
            continue
    if metric == "count":
        return count
    if metric == "total_bytes":
        return size
    if metric == "mtime":
        try:
            return path.stat().st_mtime
        except OSError as exc:
            raise CollectionError("path_unavailable") from exc
    raise CollectionError("file_metric_unknown")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError("redirect_denied")


def _safe_header_name(value: object) -> str:
    name = str(value or "Authorization").strip()[:80]
    if not name or any(char in name for char in "\r\n:"):
        raise CollectionError("header_name_invalid")
    return name


def collect_http(config, document_root=None):
    url = str(config.get("url", ""))
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise CollectionError("url_invalid")
    if parsed.hostname.casefold() not in _stored_allowed_hosts(document_root):
        raise CollectionError("host_denied")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not addresses:
            raise OSError("no address")
        for info in addresses:
            ipaddress.ip_address(info[4][0])
    except (OSError, ValueError) as exc:
        raise CollectionError("dns_failed") from exc
    headers = {"Accept": "application/json", "User-Agent": "SimpleOffice4Me-Datalogger/1"}
    secret = str(config.get("header_secret", ""))
    if secret:
        if len(secret) > MAX_SENSOR_SECRET_CHARS:
            raise CollectionError("secret_invalid")
        headers[_safe_header_name(config.get("header_name", "Authorization"))] = secret
    elif str(config.get("header_env", "")).strip():
        _LOGGER.warning("Ignoring legacy header_env reference; store the secret as header_secret")
    timeout = _bounded_int(config.get("timeout", 5), 5, 1, 15, "timeout_invalid")
    response = None
    try:
        response = build_opener(_NoRedirect).open(Request(url, headers=headers), timeout=timeout)
        content_type = response.headers.get_content_type()
        body = response.read(MAX_HTTP_BYTES + 1)
    except CollectionError:
        raise
    except Exception as exc:
        raise CollectionError("http_failed") from exc
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
    if len(body) > MAX_HTTP_BYTES:
        raise CollectionError("response_too_large")
    if content_type not in {"application/json", "application/problem+json"} and not content_type.endswith("+json"):
        raise CollectionError("content_type_invalid")
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError("json_invalid") from exc
    return _number(json_path(data, str(config.get("json_path", "value"))))


def collect_lm_sensors(config):
    try:
        result = subprocess.run(
            ["sensors", "-j"], stdin=subprocess.DEVNULL, capture_output=True,
            check=True, timeout=10, text=True, errors="replace",
        )
        if len(result.stdout) > MAX_HTTP_BYTES:
            raise CollectionError("response_too_large")
        data = json.loads(result.stdout)
    except FileNotFoundError as exc:
        raise CollectionError("lm_sensors_missing") from exc
    except CollectionError:
        raise
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise CollectionError("lm_sensors_failed") from exc
    return _number(json_path(data, str(config.get("json_path", ""))))


def collect(kind, config, document_root):
    if not isinstance(config, dict):
        raise CollectionError("config_invalid")
    if kind == "linux":
        return collect_linux(config)
    if kind == "file":
        return collect_file(document_root, config)
    if kind == "http_json":
        return collect_http(config, document_root)
    if kind == "lm_sensors":
        return collect_lm_sensors(config)
    raise CollectionError("kind_unknown")
