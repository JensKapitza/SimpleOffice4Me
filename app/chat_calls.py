"""Chat call helpers with zero-config local SIP defaults.

SimpleOffice prefers the automatically started Mini SIP registrar. Administrators
can still override the advertised host for desk phones or an existing PBX. No
SIP password is exposed to the browser.
"""
from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Any

from .chat_call_types import CALL_TYPES
from .mini_services import default_config_path
from .telephony_profiles import TelephonyProfileStore
from simpleoffice_sip_runtime import effective_sip_settings

DEFAULTS = {
    "registrar_port": 5060,
    "transport": "udp",
    "realm": "simpleoffice.local",
    "stun_server": "",
    "audio_rtp_port": 5004,
    "audio_bitrate_kbps": 64,
    "video_enabled": True,
    "video_facing_mode": "user",
}
_SAFE_USER = re.compile(r"^[A-Za-z0-9_.+*-]{1,120}$")


def _store() -> TelephonyProfileStore:
    return TelephonyProfileStore(default_config_path().parent / "telephony")


def _reachable_host(value: str) -> bool:
    value = str(value or "").strip()
    if not value:
        return False
    try:
        return not ipaddress.ip_address(value).is_loopback
    except ValueError:
        return True


def call_settings(_root: str | Path | None = None) -> dict[str, Any]:
    stored = _store().settings()
    automatic = effective_sip_settings(default_config_path())
    host = str(stored.get("registrar_host") or automatic.get("advertised_host") or "").strip()
    port = int(stored.get("registrar_port") or automatic.get("registrar_port") or DEFAULTS["registrar_port"])
    transport = str(stored.get("transport") or automatic.get("transport") or DEFAULTS["transport"])
    return {
        **DEFAULTS,
        "registrar_host": host,
        "registrar_port": port,
        "transport": transport,
        "realm": str(stored.get("realm") or DEFAULTS["realm"]),
        "stun_server": str(stored.get("stun_server") or ""),
        "sip_ready": _reachable_host(host),
        "automatic_registrar": not bool(str(stored.get("registrar_host") or "").strip()),
    }


def sip_uri(root: str | Path, username: str, call_type: str = "audio") -> str:
    call_type = str(call_type or "audio").casefold()
    if call_type not in CALL_TYPES:
        raise ValueError("Unbekannter Anruftyp")
    user = str(username or "").strip()
    if not _SAFE_USER.fullmatch(user):
        raise ValueError("Teilnehmer kann nicht als SIP-Ziel verwendet werden")
    settings = call_settings(root)
    host = settings["registrar_host"]
    if not host or not settings["sip_ready"]:
        return ""
    uri_host = f"[{host}]" if ":" in host else host
    port = int(settings.get("registrar_port") or 5060)
    suffix = f":{port}" if port != 5060 else ""
    return f"sip:{user}@{uri_host}{suffix}"