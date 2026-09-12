"""Configuration model for app-free network audio receivers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

SUPPORTED_RECEIVERS = ("rtp", "airplay", "spotify", "dlna", "browser")


@dataclass(frozen=True)
class AudioReceiver:
    receiver_id: str
    kind: str
    target: str
    name: str
    enabled: bool = True
    bind: str = "127.0.0.1"
    port: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if not text or len(text) > 120 or any(char not in allowed for char in text):
        raise ValueError(f"{label} ist ungueltig")
    return text


def normalize_receiver(value: dict[str, Any]) -> AudioReceiver:
    if not isinstance(value, dict):
        raise ValueError("Receiver muss ein Objekt sein")
    receiver_id = _clean_id(value.get("receiver_id"), "receiver_id")
    kind = str(value.get("kind") or "").strip().lower()
    if kind not in SUPPORTED_RECEIVERS:
        raise ValueError("Unbekannter Receiver-Typ")
    target = _clean_id(value.get("target"), "target")
    name = " ".join(str(value.get("name") or receiver_id).split())[:200]
    bind = str(value.get("bind") or "127.0.0.1").strip()[:200]
    port = int(value.get("port") or 0)
    if port < 0 or port > 65535:
        raise ValueError("Port ist ungueltig")
    return AudioReceiver(
        receiver_id=receiver_id,
        kind=kind,
        target=target,
        name=name,
        enabled=bool(value.get("enabled", True)),
        bind=bind,
        port=port,
    )


def normalize_receivers(values: Any) -> list[dict[str, Any]]:
    if values in (None, ""):
        return []
    if not isinstance(values, list):
        raise ValueError("Receiver muessen eine Liste sein")
    receivers = [normalize_receiver(item) for item in values]
    ids = [item.receiver_id for item in receivers]
    if len(ids) != len(set(ids)):
        raise ValueError("Receiver-IDs muessen eindeutig sein")
    return [item.to_dict() for item in receivers]


def receiver_capabilities(kind: str) -> dict[str, Any]:
    kind = str(kind or "").strip().lower()
    if kind == "rtp":
        return {"discovery": False, "direct_stream": True, "mobile_friendly": True}
    if kind == "airplay":
        return {"discovery": True, "direct_stream": True, "mobile_friendly": True}
    if kind == "spotify":
        return {"discovery": True, "direct_stream": False, "mobile_friendly": True}
    if kind == "dlna":
        return {"discovery": True, "direct_stream": True, "mobile_friendly": True}
    if kind == "browser":
        return {"discovery": False, "direct_stream": True, "mobile_friendly": True}
    raise ValueError("Unbekannter Receiver-Typ")
