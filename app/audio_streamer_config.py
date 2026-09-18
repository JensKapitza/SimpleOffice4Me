"""Validated audio settings in the existing audio database."""
from copy import deepcopy
import ipaddress
import platform
import re

from .audio_output_store import AudioOutputStore
from .mini_services import default_config_path

DEFAULTS = {
    "sender": {"enabled": True, "autostart": False, "source": "default", "backend": "pulse",
               "destinations": [], "bitrate_kbps": 64, "retry_limit": 3},
    "receiver": {"enabled": True, "autostart": False, "port": 5004, "bind": "",
                 "speaker_devices": [], "virtual_microphone": True,
                 "virtual_sink": "simpleoffice_stream", "retry_limit": 3},
}


def default_settings(service):
    data = deepcopy(DEFAULTS[service])
    if service == "receiver" and platform.system() == "Windows":
        data.update(speaker_devices=["default"], virtual_microphone=False)
    return data


def validate_settings(service, value):
    from .audio_streamer import normalize_destinations, normalize_speaker_devices, _port
    if service not in DEFAULTS or not isinstance(value, dict) or set(value) - set(DEFAULTS[service]):
        raise ValueError("Unbekannte Audio-Einstellungen")
    data = {**default_settings(service), **value}
    for key in ("enabled", "autostart"):
        if type(data[key]) is not bool:
            raise ValueError("Aktiviert/Autostart muss boolesch sein")
    if type(data["retry_limit"]) is not int or not 0 <= data["retry_limit"] <= 6:
        raise ValueError("Wiederholungen müssen zwischen 0 und 6 liegen")
    if service == "sender":
        if data["backend"] not in {"pulse", "alsa", "dshow"}:
            raise ValueError("Capture-Backend muss pulse, alsa oder dshow sein")
        if not isinstance(data["source"], str) or not 1 <= len(data["source"].strip()) <= (1024 if data["backend"] == "dshow" else 240) or any(ord(c) < 32 for c in data["source"]):
            raise ValueError("Audio-Quelle ist ungültig")
        data["source"] = data["source"].strip()
        if data["backend"] == "dshow" and (data["source"] == "default" or any(c in data["source"] for c in ":=")):
            raise ValueError("Windows-Mikrofon über die Gerätesuche auswählen")
        if not isinstance(data["destinations"], list):
            raise ValueError("Ziele müssen eine Liste sein")
        data["destinations"] = [{"host": host, "port": port} for host, port in normalize_destinations(data["destinations"])] if data["destinations"] else []
        if type(data["bitrate_kbps"]) is not int or not 16 <= data["bitrate_kbps"] <= 256:
            raise ValueError("Bitrate muss zwischen 16 und 256 kbit/s liegen")
        if data["autostart"] and not data["destinations"]:
            raise ValueError("Autostart benötigt mindestens ein Ziel")
    else:
        data["port"] = _port(data["port"])
        if not isinstance(data["bind"], str):
            raise ValueError("Bind-Adresse ist ungültig")
        data["bind"] = data["bind"].strip()
        if data["bind"]:
            address = ipaddress.ip_address(data["bind"])
            if address.version != 4 or address.is_unspecified or address.is_multicast:
                raise ValueError("Receiver benötigt eine konkrete lokale IPv4-Adresse")
        if type(data["virtual_microphone"]) is not bool:
            raise ValueError("Virtuelles Mikrofon muss boolesch sein")
        if not isinstance(data["virtual_sink"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", data["virtual_sink"]):
            raise ValueError("Name des virtuellen Mikrofons ist ungültig")
        data["speaker_devices"] = normalize_speaker_devices(data["speaker_devices"])
    return data


def settings(service, value=None):
    store = AudioOutputStore(default_config_path().parent / "audio")
    if value is not None:
        return store.service_settings(service, validate_settings(service, value))
    return validate_settings(service, store.service_settings(service))
