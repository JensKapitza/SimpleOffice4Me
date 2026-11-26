"""Locally served learning games for children in the Kids area."""
import base64
import io
import ipaddress
import json
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file, url_for

from . import kids_hand_assets, kids_piper_assets
from .auth import login_required
from .federation_qr_image import render_qr_svg
from .private_read_aloud import (
    MAX_SPEECH_AUDIO_BYTES, ReadAloudError, android_speech_available, private_audio_response, read_aloud_error,
)
from .settings_store import SettingsStore


bp = Blueprint("kids_learning", __name__, url_prefix="/kids/lernen")

#: Highest text length the read-aloud endpoint renders in one request.
#: Maze instructions and quiz prompts are short; the cap keeps renders
#: small and responsive on low-end devices.
MAX_KIDS_TTS_CHARS = 500
_MAX_KIDS_TTS_BODY = 4096
_LEGACY_RENDER = threading.BoundedSemaphore(1)

_PIPER_MISSING_MESSAGE = (
    "Vorlesestimme ist nicht eingerichtet. "
    "Bitte in den Medien-Einstellungen ein Piper-Stimmenmodell hinterlegen "
    "(Admin-Bereich, Piper-CLI plus Modell erforderlich)."
)


@bp.after_request
def _private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _kids_tts_model(language: str) -> str:
    """Pick the configured Piper model for a read-aloud language.

    German uses the administrator-configured model if any. English uses its own
    optional model and falls back to the default voice when unset, so the
    endpoint never reaches out to an external network TTS service.
    """
    try:
        media = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()["media"]
    except (OSError, ValueError, KeyError):
        return ""
    english = language.lower().startswith("en")
    model = str(media.get("piper_model_en", "") or "") if english else ""
    if not model:
        model = str(media.get("piper_model", "") or "")
    return model.strip()


def limit_speech_request():
    """Reject invalid Kids POST envelopes before the global CSRF form parser."""
    if request.endpoint != "kids_learning.sprache" or request.method != "POST":
        return None
    if not request.is_json:
        return read_aloud_error("invalid_request", 400)
    if request.content_length is not None and request.content_length > _MAX_KIDS_TTS_BODY:
        return read_aloud_error("request_too_large", 413)
    return None


def _selected_kids_piper(language, text="", purpose=""):
    model = _kids_tts_model(language)
    if not model and language == "de-DE" and purpose == "instructions" and kids_piper_assets.is_maze_instruction(text):
        model = kids_piper_assets.enabled_model(current_app.config["DOCUMENT_ROOT"])
    if not model or "://" in model or model.startswith(("//", "\\")):
        raise ReadAloudError("piper_unavailable", 503)
    path = Path(model).expanduser()
    if path.suffix.casefold() != ".onnx" or not path.is_file():
        raise ReadAloudError("piper_unavailable", 503)
    return str(path.resolve())


def _speech_arguments():
    if request.method == "GET":
        return request.args
    if request.args or not request.is_json:
        raise ReadAloudError("invalid_request", 400)
    # Limit the encoded envelope, including UTF-8 and escaped JSON. Do not use
    # request.get_json() on an unbounded stream without a declared length.
    maximum = _MAX_KIDS_TTS_BODY
    if request.content_length is not None and request.content_length > maximum:
        raise ReadAloudError("request_too_large", 413)
    body = request.stream.read(maximum + 1)
    if len(body) > maximum:
        raise ReadAloudError("request_too_large", 413)
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ReadAloudError("invalid_request", 400) from exc
    if (not isinstance(value, dict) or set(value) != {"text", "lang", "purpose"}
            or not all(isinstance(item, str) for item in value.values())):
        raise ReadAloudError("invalid_request", 400)
    return value


@bp.route("/sprache", methods=("GET", "POST"))
@login_required
def sprache():
    """Speak one local learning prompt; Android speech is authenticated POST only."""
    try:
        args = _speech_arguments()
    except ReadAloudError as exc:
        return read_aloud_error(exc.code, exc.status)
    language = (args.get("lang", "de-DE") or "de-DE").strip()
    if language not in {"de-DE", "en-US"}:
        return jsonify(error="Unbekannte Sprache. Erlaubt sind de-DE und en-US."), 400
    text = " ".join((args.get("text", "") or "").split())
    if not text or len(text) > MAX_KIDS_TTS_CHARS:
        return jsonify(error="Vorlesetext muss 1 bis 500 Zeichen enthalten."), 400
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return jsonify(error="Vorlesetext enthält ungültige Zeichen."), 400
    purpose = args.get("purpose", "")
    if purpose not in {"", "instructions"}:
        return jsonify(error="Unbekannter Einsatzzweck."), 400
    if request.method == "POST":
        return private_audio_response(
            lambda: text, language=language, device_scope="kids",
            piper_model=lambda locale: _selected_kids_piper(locale, text, purpose),
        )
    if android_speech_available():
        return read_aloud_error("method_not_allowed", 405)
    return _piper_kids_audio(text, language, purpose)


def _piper_kids_audio(text, language, purpose):
    model = _kids_tts_model(language)
    root = current_app.config["DOCUMENT_ROOT"]
    managed = ""
    if not model and language == "de-DE" and purpose == "instructions" and kids_piper_assets.is_maze_instruction(text):
        managed = kids_piper_assets.enabled_model(root)
    if not model and not managed:
        return jsonify(error=_PIPER_MISSING_MESSAGE, code="piper_unavailable"), 503
    if not _LEGACY_RENDER.acquire(blocking=False):
        return jsonify(error="Eine Ansage läuft bereits. Bitte erneut versuchen.", code="piper_busy"), 503
    try:
        if managed:
            wav = kids_piper_assets.render_instruction(root, text, managed)
            if wav is None:
                return jsonify(error="Eine Ansage läuft bereits. Bitte erneut versuchen.", code="piper_busy"), 503
        else:
            from .audio_output_engine import render_tts

            # Retain the old short GET contract for desktop clients, but never
            # write their arbitrary rendered text into a persistent WAV cache.
            with tempfile.TemporaryDirectory(prefix="simpleoffice-kids-speech-") as directory:
                audio_path = render_tts(
                    text, "kids-de" if language == "de-DE" else "kids-en", directory,
                    model=model, document_root=root,
                )
                with audio_path.open("rb") as output:
                    wav = output.read(MAX_SPEECH_AUDIO_BYTES + 1)
            if len(wav) > MAX_SPEECH_AUDIO_BYTES:
                return read_aloud_error("audio_too_large", 413)
        return send_file(io.BytesIO(wav), mimetype="audio/wav")
    except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
        current_app.logger.warning("Kids read-aloud failed: %s", type(exc).__name__)
        return jsonify(error=_PIPER_MISSING_MESSAGE, code="piper_unavailable"), 503
    finally:
        _LEGACY_RENDER.release()


@bp.get("")
@bp.get("/")
@login_required
def index():
    vocabulary_path = Path(current_app.static_folder) / "data" / "kids_language_vocabulary.json"
    try:
        language_vocabulary = json.loads(vocabulary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        language_vocabulary = []
    return render_template("kids/learning.html", language_vocabulary=language_vocabulary, kids_mode=True)


@bp.get("/shinobi")
@login_required
def shinobi_mission():
    """Visual working-memory game, independent of any webcam feature."""
    return render_template(
        "kids/shinobi.html", kids_mode=True,
        hand_game_available="kids_learning.hand_game" in current_app.view_functions,
    )
@bp.get("/logikwerkstatt")
@login_required
def logic_workshop():
    """Local, playful Boolean and set operations, never connected to hardware."""
    return render_template("kids/logic_workshop.html", kids_mode=True)


@bp.get("/spielpause")
@login_required
def mini_games():
    """Short, touch-friendly games for a quick break in the Kids area."""
    return render_template("kids/mini_games.html", kids_mode=True)


def _hand_game_asset_directory() -> str:
    from .optional_hand_game_install import asset_directory
    from .optional_local_setup import hand_override

    return asset_directory(current_app.config["DOCUMENT_ROOT"], hand_override(current_app.config))


@bp.get("/handzeichen")
@login_required
def hand_game():
    """Play fantasy hand signs. The camera is only accessible in this page."""
    return render_template("kids/hand_game.html", kids_mode=True,
                           hand_game_ready=kids_hand_assets.ready(_hand_game_asset_directory()))


@bp.get("/handzeichen/assets/<name>")
@login_required
def hand_game_asset(name):
    spec = kids_hand_assets.asset_spec(name)
    data = kids_hand_assets.asset(_hand_game_asset_directory(), name) if spec else None
    if data is None:
        abort(404)
    return send_file(io.BytesIO(data), mimetype=spec[0])


@bp.get("/silben")
@login_required
def syllables():
    """Preschool syllable game: clap, count and rhyme without reading."""
    return render_template("kids/syllables.html", kids_mode=True)


@bp.get("/qr")
@login_required
def share_game_qr():
    """Show a QR shortcut that opens the selected activity on another device."""
    # Keep the standalone QR page useful when opened directly. The game button
    # supplies a selection, while a bare URL gets a safe preschool default.
    game = (request.args.get("game") or "maze").strip()
    level = request.args.get("level", "preschool")
    subject = request.args.get("subject", "")
    valid_games = {"maze", "objects", "letters", "directions", "safety", "logic", "quiz"}
    valid_levels = {"preschool", "1", "2", "3", "4", "5"}
    valid_subjects = {"math", "german", "english", "world", "mixed"}
    if game not in valid_games or level not in valid_levels or (game == "quiz" and subject not in valid_subjects):
        abort(400)
    game_url = url_for("kids_learning.index", game=game, level=level, **({"subject": subject} if game == "quiz" else {}), _external=True)
    parts = urlsplit(game_url)
    hostname = (parts.hostname or "").casefold()
    is_loopback = hostname == "localhost"
    try:
        is_loopback = is_loopback or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        pass
    if is_loopback:
        proxy = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()["lan_proxy"]
        try:
            proxy_address = ipaddress.ip_address(proxy["bind_host"])
        except (KeyError, ValueError):
            proxy_address = ipaddress.ip_address("127.0.0.1")
        if proxy.get("enabled") and not proxy_address.is_loopback and not proxy_address.is_unspecified:
            authority = f"[{proxy_address.compressed}]" if proxy_address.version == 6 else proxy_address.compressed
            scheme = "https" if proxy.get("tls_cert") else "http"
            game_url = urlunsplit((scheme, f"{authority}:{proxy['port']}", parts.path, parts.query, ""))
            is_loopback = False
    qr_data = base64.b64encode(render_qr_svg(game_url).encode("utf-8")).decode("ascii")
    return render_template("kids/share_qr.html", game_url=game_url, qr_data=qr_data, local_only=is_loopback, kids_mode=True)
