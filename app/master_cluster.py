"""DNS authority and self-reachability for a build-configured master cluster."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import dns.exception
import dns.resolver
from flask import current_app

from .build_master import LICENSE_MASTER_MODE, LICENSE_MASTER_URL
from .document_store import CONTROL_DIR, atomic_json_write
from .federation_identity import FederationIdentity
from .revision_history import RevisionHistory

MAX_PROFILE_BYTES = 64 * 1024
TIMEOUT_SECONDS = 3
CLUSTER_MODES = {"active-active", "backup-active", "permanent-master"}
_AUTHORITY_VALUE = re.compile(r"^simpleoffice-master-id=([0-9a-f]{64})$")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def default_txt_record_name(master_url: str | None = None) -> str:
    master_url = LICENSE_MASTER_URL if master_url is None else master_url
    hostname = urlsplit(str(master_url or "")).hostname
    return f"_simpleoffice-master.{hostname}" if hostname else ""


class MasterClusterSettings:
    """Persistent, audited per-node cluster operation settings."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "master-cluster.json"
        self.history = RevisionHistory(self.root)

    def load(self) -> dict:
        default = {"mode": "active-active", "txt_record_name": default_txt_record_name()}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        if not isinstance(data, dict):
            raise ValueError("invalid cluster settings")
        return self.validate({**default, **data})

    @staticmethod
    def validate(value: dict) -> dict:
        mode = str(value.get("mode") or "").strip()
        if mode not in CLUSTER_MODES:
            raise ValueError("invalid cluster operation mode")
        record_name = str(value.get("txt_record_name") or "").strip().rstrip(".").casefold()
        if not record_name or "." not in record_name or len(record_name) > 253 or any(
            not label or len(label) > 63 or not re.fullmatch(r"_?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in record_name.split(".")
        ):
            raise ValueError("invalid cluster authority TXT record name")
        return {"mode": mode, "txt_record_name": record_name}

    def save(self, value: dict, actor: str) -> dict:
        if not str(actor or "").strip():
            raise ValueError("a named administrator is required")
        normalized = self.validate(value)
        atomic_json_write(self.path, normalized)
        self.history.record("master_cluster_settings_updated", actor, "master_cluster", "local", normalized)
        return normalized


def _profile_url(base_url: str) -> str:
    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("master address must be an HTTPS URL without credentials")
    return urlunsplit((parsed.scheme, parsed.netloc, "/.well-known/simpleoffice-federation", "", ""))


def _open_profile(request):
    opener = urllib.request.build_opener(_NoRedirectHandler)
    return opener.open(request, timeout=TIMEOUT_SECONDS)


def resolve_authority_id(record_name: str) -> str:
    """Read the designated node fingerprint from a DNS TXT record."""
    resolver = dns.resolver.Resolver(configure=True)
    resolver.cache = None
    answers = resolver.resolve(record_name.rstrip(".") + ".", "TXT", lifetime=TIMEOUT_SECONDS)
    values = set()
    for answer in answers:
        text = b"".join(answer.strings).decode("ascii", errors="strict").strip()
        match = _AUTHORITY_VALUE.fullmatch(text)
        if match:
            values.add(match.group(1))
    if len(values) != 1:
        raise ValueError("authority TXT record must contain exactly one valid node ID")
    return values.pop()


def _operating_state(mode: str, local_id: str, authority_id: str | None, reachable: bool) -> str:
    if mode == "permanent-master":
        return "active"
    if mode == "active-active":
        return "active" if reachable else "active_partitioned"
    if reachable and authority_id and local_id == authority_id:
        return "active"
    if not reachable:
        return "standby_unreachable"
    return "standby" if authority_id else "standby_unverified"


def inspect_master_address(root) -> dict:
    """Probe the fixed master URL and resolve its DNS-declared authority node."""
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if not LICENSE_MASTER_MODE:
        return {"role": "client", "operating_state": "not_applicable", "checked_at": checked_at}
    try:
        settings = MasterClusterSettings(root).load()
    except (OSError, ValueError) as exc:
        return {"role": "master", "operating_state": "standby_invalid_configuration", "reachability": "not_checked", "authority_error": type(exc).__name__, "checked_at": checked_at}
    mode = settings["mode"]
    if not LICENSE_MASTER_URL:
        return {"role": "master", "mode": mode, "operating_state": _operating_state(mode, "", None, False), "reachability": "address_unconfigured", "checked_at": checked_at}
    try:
        probe_url = _profile_url(LICENSE_MASTER_URL)
    except ValueError as exc:
        return {
            "role": "master",
            "mode": mode,
            "operating_state": _operating_state(mode, "", None, False),
            "reachability": "unreachable",
            "authority_error": type(exc).__name__,
            "checked_at": checked_at,
        }

    local_id = ""
    authority_id = None
    authority_error = ""
    try:
        local_id = FederationIdentity(root).public_identity()["fingerprint"]
    except (OSError, RuntimeError, ValueError):
        authority_error = "local_identity_unavailable"
    if settings["txt_record_name"]:
        try:
            authority_id = resolve_authority_id(settings["txt_record_name"])
        except (dns.exception.DNSException, OSError, UnicodeDecodeError, ValueError) as exc:
            authority_error = type(exc).__name__

    remote_id = ""
    reachable = False
    try:
        url = probe_url
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with _open_profile(request) as response:
            if response.status != 200:
                raise ValueError("master profile returned a non-success status")
            raw = response.read(MAX_PROFILE_BYTES + 1)
        if len(raw) > MAX_PROFILE_BYTES:
            raise ValueError("master profile exceeds the response limit")
        profile = json.loads(raw.decode("utf-8"))
        if not isinstance(profile, dict) or not isinstance(profile.get("master"), dict):
            raise ValueError("master profile is invalid")
        remote_id = str(profile.get("fingerprint") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", remote_id):
            raise ValueError("invalid master fingerprint")
        reachable = profile["master"].get("is_master") is True
        if not reachable:
            remote_id = ""
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError, ValueError, urllib.error.URLError) as exc:
        authority_error = authority_error or type(exc).__name__

    if not authority_id and mode == "backup-active":
        state = "standby_unverified"
    else:
        state = _operating_state(mode, local_id, authority_id, reachable)
    return {
        "role": "master",
        "mode": mode,
        "operating_state": state,
        "reachability": "reachable" if reachable else "unreachable",
        "observed_node_id": remote_id,
        "authority_node_id": authority_id or "",
        "authority_txt_record": settings["txt_record_name"],
        "authority_error": authority_error,
        "checked_at": checked_at,
    }


def record_master_address_status(root) -> dict:
    status = inspect_master_address(root)
    current_app.extensions["simpleoffice_master_address_status"] = status
    current_app.logger.info(
        "master cluster mode=%s state=%s reachability=%s",
        status.get("mode", "client"), status.get("operating_state", "not_applicable"), status.get("reachability", "not_applicable"),
    )
    return status
