"""Validated device-only speech preferences for the embedded Android backend."""
from __future__ import annotations

import math
import unicodedata

ENGINES = ("automatic", "android", "piper", "disabled")
SCOPES = ("reader", "kids")
LANGUAGES = ("de", "en")
DEFAULTS = {
    "reader": "automatic",
    "kids": "automatic",
    "reader_fallback": False,
    "kids_fallback": False,
    "voice_de": "",
    "voice_en": "",
    "rate": 1.0,
    "pitch": 1.0,
}


def valid_voice_id(value) -> bool:
    return isinstance(value, str) and len(value) <= 160 and all(
        not unicodedata.category(char).startswith("C") for char in value
    )


def validate(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError("invalid Android speech preferences")
    result = dict(value)
    for profile in SCOPES:
        if result[profile] not in ENGINES or type(result[profile + "_fallback"]) is not bool:
            raise ValueError("invalid Android speech policy")
    for language in LANGUAGES:
        if not valid_voice_id(result["voice_" + language]):
            raise ValueError("invalid Android voice")
    for parameter in ("rate", "pitch"):
        number = result[parameter]
        if type(number) not in {float, int} or not 0.5 <= number <= 2.0 or not math.isfinite(number):
            raise ValueError("invalid Android speech adjustment")
        result[parameter] = float(number)
    return result


def effective(media: dict) -> dict:
    try:
        settings = validate(media.get("android_speech"))
    except ValueError:
        settings = None
    if settings is not None:
        return settings
    return {**DEFAULTS, "reader": "android" if media.get("android_private_speech") is True else "automatic"}
