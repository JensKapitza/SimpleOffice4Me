"""Short, private audio responses using a configured local speech engine."""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable

from flask import Response, current_app, jsonify, request

from . import android_speech_settings
from .audio_output_engine import render_tts
from .kids_piper_assets import installed_model
from .media_settings import PIPER_READER_THORSTEN
from .settings_store import SettingsStore

MAX_SPEECH_CHARS = 5000
MAX_SPEECH_AUDIO_BYTES = 8 * 1024 * 1024
_MAX_REQUEST_BYTES = 1024
_slots = threading.BoundedSemaphore(2)


class ReadAloudError(ValueError):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


def _private(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def read_aloud_error(code: str, status: int) -> Response:
    response = jsonify(error=code)
    response.status_code = status
    return _private(response)


def limit_read_aloud_request() -> Response | None:
    """Reject oversized/non-JSON submissions before the global CSRF form parser."""
    if not request.is_json:
        return read_aloud_error("invalid_request", 400)
    if request.content_length is not None and request.content_length > _MAX_REQUEST_BYTES:
        return read_aloud_error("request_too_large", 413)
    return None


def read_aloud_request() -> tuple[int, str]:
    """Parse the shared player's small locator envelope; never accept text."""
    page, version, _, _ = _read_locator()
    return page, version


def read_aloud_ocr_request() -> tuple[int, str, int]:
    """Accept an optional fixed quarter-turn only for the explicit Reader OCR."""
    page, version, clockwise, _ = _read_locator(allow_ocr_rotation=True)
    return page, version, clockwise


def _read_locator(
    *, allow_ocr_rotation: bool = False, include_segment: bool = False
) -> tuple[int, str, int, int]:
    if not request.is_json:
        raise ReadAloudError("invalid_request", 400)
    if request.content_length is not None and request.content_length > _MAX_REQUEST_BYTES:
        raise ReadAloudError("request_too_large", 413)
    body = request.stream.read(_MAX_REQUEST_BYTES + 1)
    if len(body) > _MAX_REQUEST_BYTES:
        raise ReadAloudError("request_too_large", 413)
    try:
        value = json.loads(body)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ReadAloudError("invalid_request", 400) from exc
    if not isinstance(value, dict):
        raise ReadAloudError("invalid_request", 400)
    clockwise = value.pop("ocr_clockwise", 0) if allow_ocr_rotation else 0
    keys = {"page", "document_version", "segment"} if include_segment else {"page", "document_version"}
    if set(value) != keys:
        raise ReadAloudError("invalid_request", 400)
    if type(clockwise) is not int or clockwise not in {0, 90, 270}:
        raise ReadAloudError("invalid_rotation", 400)
    page, version = value["page"], value["document_version"]
    if type(page) is not int or not 1 <= page <= 10_000:
        raise ReadAloudError("invalid_page", 400)
    if not isinstance(version, str) or not version or len(version) > 160:
        raise ReadAloudError("invalid_version", 400)
    segment = value.get("segment", 1)
    if type(segment) is not int or not 1 <= segment <= 10_000:
        raise ReadAloudError("invalid_segment", 400)
    return page, version, clockwise, segment


def read_aloud_epub_request() -> tuple[int, str, int]:
    page, version, _, segment = _read_locator(include_segment=True)
    return page, version, segment


def configured_piper_model(language: str) -> str:
    root = current_app.config["DOCUMENT_ROOT"]
    media = SettingsStore(root).settings()["media"]
    locale = language.lower()
    german = locale == "de" or locale.startswith(("de-", "de_"))
    if german and media.get("piper_reader_choice") == PIPER_READER_THORSTEN:
        # Revalidate the same installed pair at point of use. Neither the Kids
        # enable marker nor an administrator-provided path authorizes this choice.
        model = installed_model(root)
        if not model:
            raise ReadAloudError("piper_unavailable", 503)
        return model
    model = media.get("piper_model_en", "") if locale.startswith("en") else ""
    configured = str(model or media.get("piper_model", "")).strip()
    if not configured or "://" in configured or configured.startswith(("//", "\\")):
        raise ReadAloudError("piper_unavailable", 503)
    path = Path(configured).expanduser()
    if path.suffix.casefold() != ".onnx" or not path.is_file():
        raise ReadAloudError("piper_unavailable", 503)
    return str(path.resolve())


def android_speech_available() -> bool:
    if not callable(current_app.config.get("ANDROID_PRIVATE_SPEECH")):
        return False
    # ProxyFix can replace REMOTE_ADDR/HTTP_HOST. A native-only capability
    # must depend on the actual socket and bundled WebView origin.
    original = request.environ.get("werkzeug.proxy_fix.orig", request.environ)
    address = original.get("REMOTE_ADDR")
    host = str(original.get("HTTP_HOST") or "").casefold()
    allowed_hosts = {"127.0.0.1:8765", "localhost:8765", "[::1]:8765"}
    if current_app.testing:
        allowed_hosts |= {"localhost", "127.0.0.1"}
    return (address in {"127.0.0.1", "::1"} and host in allowed_hosts
            and not request.environ.get("simpleoffice.lan_https")
            and not any(name in request.headers for name in (
                "Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Port", "X-Forwarded-Proto",
            )))

def device_preferences() -> dict:
    media = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()["media"]
    return android_speech_settings.effective(media)


def device_voices() -> tuple[dict, ...]:
    inventory = current_app.config.get("ANDROID_PRIVATE_SPEECH_VOICES")
    if not android_speech_available() or not callable(inventory):
        return ()
    try:
        items = inventory()
    except Exception as exc:
        current_app.logger.info("Android voice inventory unavailable (%s)", type(exc).__name__)
        return ()
    if not isinstance(items, (list, tuple)):
        return ()
    voices = {}
    for item in items[:256]:
        if not isinstance(item, dict):
            continue
        voice, label, locale = (item.get(name) for name in ("id", "label", "language"))
        if not voice or not android_speech_settings.valid_voice_id(voice):
            continue
        if not isinstance(locale, str) or len(locale) > 35:
            continue
        language = locale.casefold().replace("_", "-").split("-", 1)[0]
        if language not in android_speech_settings.LANGUAGES:
            continue
        name = label if label and android_speech_settings.valid_voice_id(label) else voice
        voices[(language, voice)] = {"id": voice, "label": name, "language": language}
    return tuple(sorted(voices.values(), key=lambda item: (item["language"], item["label"], item["id"])))


def _speech_choice(scope, language, piper_model):
    if scope is None or not android_speech_available():
        return piper_model(language), None
    settings = device_preferences()
    engine = settings[scope]
    if engine == "disabled":
        raise ReadAloudError("speech_disabled", 503)
    if engine == "android":
        return None, settings
    if engine == "automatic" and settings[scope + "_fallback"]:
        if not shutil.which("piper"):
            return None, settings
        try:
            model = piper_model(language)
            # A model alone is not runnable with the existing Piper CLI.
            if not Path(str(model) + ".json").is_file():
                return None, settings
            return model, None
        except ReadAloudError as exc:
            if exc.code != "piper_unavailable":
                raise
            return None, settings
    return piper_model(language), None


def private_audio_response(
    load_text: Callable[[], str], *, language: str = "", recheck_source: bool = False,
    allow_device_voice: bool = False, device_scope: str | None = None, piper_model=None,
) -> Response:
    """Run a server-selected text loader with local audio and no shared cache."""
    if not _slots.acquire(blocking=False):
        return read_aloud_error("read_aloud_busy", 503)
    try:
        scope = device_scope or ("reader" if allow_device_voice else None)
        model, device = _speech_choice(scope, language, piper_model or configured_piper_model)
        use_device_voice = device is not None
        text = " ".join(load_text().split())
        if not text:
            raise ReadAloudError("no_text", 422)
        if len(text) > MAX_SPEECH_CHARS:
            raise ReadAloudError("too_much_text", 413)
        try:
            text.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ReadAloudError("invalid_text", 422) from exc
        if use_device_voice and len(text.encode("utf-16-le")) // 2 > 4000:
            raise ReadAloudError("too_much_text", 413)
        temp_root = current_app.config.get("ANDROID_PRIVATE_SPEECH_CACHE") if use_device_voice else None
        with tempfile.TemporaryDirectory(prefix="simpleoffice-private-speech-", dir=temp_root) as directory:
            if use_device_voice:
                path = Path(directory) / "speech.wav"
                try:
                    locale = language.replace("_", "-") or "de"
                    lang_key = locale.casefold().split("-", 1)[0]
                    voice = device.get("voice_" + lang_key, "")
                    generated_ok = current_app.config["ANDROID_PRIVATE_SPEECH"](
                        text, locale, path, voice or None, device["rate"], device["pitch"],
                    )
                except Exception as exc:
                    # The Java call may raise an Android/Chaquopy exception.
                    # Do not log the exception message (it may include inputs).
                    current_app.logger.info("Android speech render failed: %s", type(exc).__name__)
                    raise ReadAloudError("android_speech_unavailable", 503) from exc
                if not generated_ok or not path.is_file():
                    raise ReadAloudError("android_speech_unavailable", 503)
            else:
                path = render_tts(text, "private-reader", directory, model=model)
            with path.open("rb") as generated:
                audio = generated.read(MAX_SPEECH_AUDIO_BYTES + 1)
        if len(audio) > MAX_SPEECH_AUDIO_BYTES:
            raise ReadAloudError("audio_too_large", 413)
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ReadAloudError("invalid_audio", 503)
        if recheck_source and " ".join(load_text().split()) != text:
            raise ReadAloudError("document_changed", 409)
        return _private(Response(audio, mimetype="audio/wav"))
    except ReadAloudError as exc:
        return read_aloud_error(exc.code, exc.status)
    except TimeoutError:
        return read_aloud_error("read_aloud_timeout", 503)
    except (KeyError, OSError, ValueError, RuntimeError, wave.Error) as exc:
        current_app.logger.info("Private speech render failed: %s", type(exc).__name__)
        return read_aloud_error("piper_unavailable", 503)
    finally:
        _slots.release()
