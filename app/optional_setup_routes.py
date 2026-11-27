"""One optional setup entry for adults, with explicit admin-only queue admission."""
from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from werkzeug.wsgi import LimitedStream

from . import android_speech_settings, english_voice_assets, kids_hand_assets, kids_piper_assets, optional_hand_game_install, optional_local_setup
from .access_control import audit, is_admin
from .auth import login_required
from .media_settings import PIPER_READER_THORSTEN
from .private_read_aloud import (
    android_speech_available, device_preferences, device_voices, private_audio_response,
)
from .settings_store import SettingsStore
from .setup_store import SetupStore

bp = Blueprint("optional_setup", __name__, url_prefix="/setup/optional")


@bp.after_request
def privacy(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.get("")
@login_required
def index():
    root = current_app.config["DOCUMENT_ROOT"]
    manual = optional_local_setup.hand_override(current_app.config)
    setup = SetupStore(root).status(str(g.user["username"]))
    native_speech = android_speech_available()
    manage = is_admin(g.user)
    return render_template(
        "optional_setup/index.html",
        readiness=optional_local_setup.readiness(root, manual_hand_directory=manual),
        inventory=optional_local_setup.component_inventory(root, manual_hand_directory=manual),
        optional_skipped=setup["optional_skipped"],
        manage=manage,
        android_speech_available=native_speech,
        android_speech=device_preferences() if native_speech else None,
        android_voices=device_voices() if native_speech and manage else (),
        android_speech_status=_device_speech_status() if native_speech else "unavailable",
        manual_hand=bool(manual),
        voice_release=kids_piper_assets.MODEL_REVISION,
        voice_card=kids_piper_assets.MODEL_CARD,
        voice_manifest=kids_piper_assets.asset_manifest(),
        hand_runtime_release=optional_hand_game_install.RUNTIME_RELEASE,
        hand_model_release=optional_hand_game_install.MODEL_RELEASE,
        hand_model_card=optional_hand_game_install.MODEL_CARD,
        hand_package_hash=kids_hand_assets.PACKAGE_SHA256,
        hand_manifest=kids_hand_assets.ASSETS,
        hand_archive_limit=optional_hand_game_install.RUNTIME_MAX_BYTES,
        english_voice_release=english_voice_assets.REVISION,
        english_voice_card=english_voice_assets.MODEL_CARD,
        english_voice_license=english_voice_assets.LICENSE_URL,
        english_voice_manifest=english_voice_assets.asset_manifest(),
    )


@bp.get("/progress")
@login_required
def progress():
    return jsonify(optional_local_setup.progress_states(current_app.config["DOCUMENT_ROOT"]))


@bp.get("/progress/details")
@login_required
def progress_details():
    response = current_app.make_response(jsonify(
        optional_local_setup.progress_details(current_app.config["DOCUMENT_ROOT"]),
    ))
    response.headers["Cache-Control"] = "private, no-store"
    return response


def limit_request() -> None:
    """Apply the small mutation-body cap before session CSRF parses a form."""
    if request.endpoint not in {"optional_setup.install", "optional_setup.preference",
                                "optional_setup.activate_voice", "optional_setup.select_reader_voice",
                                "optional_setup.install_all",
                                "optional_setup.cancel_install",
                                "optional_setup.set_android_speech", "optional_setup.test_android_speech",
                                "optional_setup.english_voice"}:
        return
    if request.mimetype != "application/x-www-form-urlencoded":
        abort(415)
    size = request.content_length
    if size is None:
        abort(411)
    configured = request.max_content_length
    limit = 1024 if configured is None else min(1024, configured)
    if size > limit:
        abort(413)
    if type(request._get_current_object()).max_content_length.fset is not None:
        request.max_content_length = limit
    elif "wsgi.input_terminated" in request.environ:
        request.environ["wsgi.input"] = LimitedStream(request.environ["wsgi.input"], limit, is_max=True)
    request.max_form_memory_size = limit


@bp.post("/install")
@login_required
def install():
    if not is_admin(g.user):
        abort(403)
    if request.args or set(request.form) != {"_csrf_token", "feature"} or len(request.form.getlist("feature")) != 1:
        abort(400)
    feature = request.form["feature"]
    if feature not in optional_local_setup.FEATURES:
        abort(400)
    root = current_app.config["DOCUMENT_ROOT"]
    try:
        state = optional_local_setup.queue(root, feature, manual_hand_directory=optional_local_setup.hand_override(
            current_app.config,
        ))
    except (OSError, ValueError):
        state = "failed"
    audit("optional_local_install_requested", "optional_setup", feature,
          outcome="failure" if state == "failed" else "success",
          detail={"state": state})
    messages = {
        "queued": ("Setup queued. Reload to see progress.", "Einrichtung vorgemerkt. Für den Fortschritt neu laden."),
        "downloading": ("Setup is already downloading.", "Die Einrichtung lädt bereits."),
        "ready": ("These files are already available.", "Diese Dateien sind bereits verfügbar."),
        "manual": ("The administrator configured an external local folder.", "Es ist ein externer lokaler Ordner konfiguriert."),
        "busy": ("Other installs are active. Try again later.", "Andere Installationen laufen. Versuche es später erneut."),
        "insufficient-space": ("There is not enough free space for this component.", "Für diese Komponente ist nicht genügend freier Speicherplatz vorhanden."),
        "unsupported": ("This installer does not support the detected Python, platform or architecture.",
                        "Dieser Installer unterstützt die erkannte Python-Version, Plattform oder Architektur nicht."),
        "failed": ("Setup could not be started. You can retry.", "Einrichtung konnte nicht starten. Wiederholen ist möglich."),
    }
    flash(messages[state][0 if g.language == "en" else 1])
    return redirect(url_for("optional_setup.index", _anchor=feature), code=303)


@bp.post("/install-all")
@login_required
def install_all():
    """Queue verified basics; voice models require their own named action."""
    if not is_admin(g.user):
        abort(403)
    if request.args or set(request.form) != {"_csrf_token"} or len(request.form.getlist("_csrf_token")) != 1:
        abort(400)
    root = current_app.config["DOCUMENT_ROOT"]
    manual = optional_local_setup.hand_override(current_app.config)
    outcomes = {}
    inventory = {
        item["id"]: item
        for item in optional_local_setup.component_inventory(root, manual_hand_directory=manual)["components"]
    }
    features = []
    if inventory.get(optional_local_setup.PIPER_ENGINE, {}).get("install_feature"):
        features.append(optional_local_setup.PIPER_ENGINE)
    features.append("kids-hand")
    for feature in features:
        try:
            outcomes[feature] = optional_local_setup.queue(
                root, feature, manual_hand_directory=manual,
            )
        except (OSError, ValueError):
            outcomes[feature] = "failed"
    audit("optional_local_install_requested", "optional_setup", "recommended-basics",
          outcome="failure" if any(value not in {"queued", "downloading", "ready", "manual", "unsupported"}
                                    for value in outcomes.values()) else "success",
          detail={"features": outcomes})
    labels = {
        optional_local_setup.PIPER_ENGINE: ("Piper runtime" if g.language == "en" else "Piper-Laufzeit"),
        "kids-hand": ("Finger Magic" if g.language == "en" else "Fingerzauber"),
    }
    outcome_text = {
        "queued": ("queued" if g.language == "en" else "vorgemerkt"),
        "downloading": ("already in progress" if g.language == "en" else "läuft bereits"),
        "ready": ("already ready" if g.language == "en" else "bereits bereit"),
        "manual": ("manual folder configured" if g.language == "en" else "manueller Ordner konfiguriert"),
        "unsupported": ("not supported here" if g.language == "en" else "hier nicht unterstützt"),
        "busy": ("another install is active" if g.language == "en" else "eine andere Installation läuft"),
        "insufficient-space": ("not enough free space" if g.language == "en" else "zu wenig freier Speicher"),
        "failed": ("could not start; retry or check the server log" if g.language == "en"
                   else "Start fehlgeschlagen; erneut versuchen oder Serverprotokoll prüfen"),
    }
    summary = "; ".join(
        f"{labels[feature]}: {outcome_text.get(outcome, outcome)}"
        for feature, outcome in outcomes.items()
    )
    summary += (". Voice models remain separate." if g.language == "en"
                else ". Sprachmodelle werden separat ausgewählt.")
    flash(summary)
    return redirect(url_for("optional_setup.index", _anchor="component-manager"), code=303)


@bp.post("/cancel")
@login_required
def cancel_install():
    if not is_admin(g.user):
        abort(403)
    if (request.args or set(request.form) != {"_csrf_token", "feature"}
            or len(request.form.getlist("feature")) != 1):
        abort(400)
    feature = request.form["feature"]
    if feature not in optional_local_setup.FEATURES:
        abort(400)
    try:
        state = optional_local_setup.cancel(current_app.config["DOCUMENT_ROOT"], feature)
    except (OSError, ValueError):
        state = "failed"
    audit("optional_local_install_cancelled", "optional_setup", feature,
          outcome="failure" if state in {"failed", "missing"} else "success", detail={"state": state})
    messages = {
        "cancelling": ("Cancellation requested.", "Abbruch angefordert."),
        "cancelled": ("Setup cancelled. Incomplete files were discarded.", "Einrichtung abgebrochen. Unvollständige Dateien wurden verworfen."),
        "ready": ("The component is already installed.", "Die Komponente ist bereits installiert."),
        "failed": ("Cancellation could not be recorded. Refresh the status.", "Abbruch konnte nicht gespeichert werden. Bitte Status aktualisieren."),
    }
    message = messages.get(state, ("There is no active download.", "Es läuft kein Download."))
    flash(message[0 if g.language == "en" else 1])
    return redirect(url_for("optional_setup.index", _anchor=feature), code=303)


@bp.post("/activate-kids-voice")
@login_required
def activate_voice():
    if not is_admin(g.user):
        abort(403)
    if request.args or set(request.form) != {"_csrf_token"} or len(request.form.getlist("_csrf_token")) != 1:
        abort(400)
    state = kids_piper_assets.set_enabled(current_app.config["DOCUMENT_ROOT"], True, allow_download=False)
    audit("optional_local_install_requested", "optional_setup", "kids-voice",
          outcome="success" if state == "enabled" else "failure", detail={"state": state, "local_only": True})
    messages = ("Existing verified voice enabled.", "Vorhandene geprüfte Stimme aktiviert.") if state == "enabled" else (
        "The local copy could not be enabled. No download was attempted.",
        "Die lokale Kopie ließ sich nicht aktivieren. Es wurde nichts heruntergeladen.",
    )
    flash(messages[0 if g.language == "en" else 1])
    return redirect(url_for("optional_setup.index", _anchor="kids-voice"), code=303)


@bp.post("/reader-voice")
@login_required
def select_reader_voice():
    if not is_admin(g.user):
        abort(403)
    if (request.args or set(request.form) != {"_csrf_token", "action"}
            or len(request.form.getlist("action")) != 1 or len(request.form.getlist("_csrf_token")) != 1):
        abort(400)
    action = request.form["action"]
    if action not in {"select", "restore"}:
        abort(400)
    root = current_app.config["DOCUMENT_ROOT"]
    if action == "select" and not optional_local_setup.reader_choice_available(root):
        flash(("The installed voice or engine is unavailable. No selection or download was made." if g.language == "en"
               else "Stimmdateien oder Engine fehlen. Es wurde nichts ausgewählt oder heruntergeladen."))
        return redirect(url_for("optional_setup.index", _anchor="reader-speech"), code=303)
    try:
        # Partial media write preserves manual defaults and independent English.
        SettingsStore(root).save(
            {"media": {"piper_reader_choice": PIPER_READER_THORSTEN if action == "select" else ""}},
            str(g.user["username"]),
        )
    except (OSError, ValueError):
        flash("Voice selection could not be saved." if g.language == "en" else "Die Stimmenauswahl konnte nicht gespeichert werden.")
    else:
        audit("reader_voice_selection", "optional_setup", "reader-speech", detail={"action": action})
        flash("Reader and Mail selection saved." if g.language == "en" else "Auswahl für Reader und Mail gespeichert.")
    return redirect(url_for("optional_setup.index", _anchor="reader-speech"), code=303)


def _device_speech_status():
    checker = current_app.config.get("ANDROID_PRIVATE_SPEECH_STATUS")
    if callable(checker):
        try:
            status = checker()
            if status in ("loading", "ready", "unavailable"):
                return status
        except Exception as exc:
            current_app.logger.info("Android voice status unavailable (%s)", type(exc).__name__)
    return "unavailable"


def _require_device_speech_admin():
    if not is_admin(g.user) or not android_speech_available():
        abort(403)
    if request.args or any(len(request.form.getlist(key)) != 1 for key in request.form):
        abort(400)


def _device_speech_form():
    fields = {"_csrf_token", "action", "reader", "kids", "voice_de", "voice_en", "rate", "pitch"}
    checkboxes = {"reader_fallback", "kids_fallback"}
    if not fields <= set(request.form) <= fields | checkboxes:
        raise ValueError("invalid speech settings fields")
    result = {key: request.form[key] for key in ("reader", "kids", "voice_de", "voice_en")}
    for key in checkboxes:
        value = request.form.get(key)
        if value is not None and value != "1":
            raise ValueError("invalid fallback choice")
        result[key] = value == "1"
    for key in ("rate", "pitch"):
        result[key] = float(request.form[key])
    saved = device_preferences()
    voices = {(row["language"], row["id"]) for row in device_voices()}
    for language in android_speech_settings.LANGUAGES:
        key = "voice_" + language
        if result[key] and result[key] != saved[key] and (language, result[key]) not in voices:
            raise ValueError("voice not installed for this language")
    return android_speech_settings.validate(result)


@bp.post("/android-speech")
@login_required
def set_android_speech():
    _require_device_speech_admin()
    action = request.form.get("action")
    if action == "refresh" and set(request.form) == {"_csrf_token", "action"}:
        refresh = current_app.config.get("ANDROID_PRIVATE_SPEECH_REFRESH")
        queued = False
        if callable(refresh):
            try:
                queued = bool(refresh())
            except Exception as exc:
                current_app.logger.info("Android voice refresh unavailable (%s)", type(exc).__name__)
        if queued:
            flash("Checking installed voices. Refresh the status in a moment." if g.language == "en"
                  else "Installierte Stimmen werden geprüft. Lade den Status gleich neu.")
        else:
            flash("Voice check unavailable. Return to the app and try again." if g.language == "en"
                  else "Stimmenprüfung nicht verfügbar. Kehre zur App zurück und versuche es erneut.")
        return redirect(url_for("optional_setup.index", _anchor="reader-speech"), code=303)
    try:
        if action == "save":
            settings = _device_speech_form()
            updates = {"android_speech": settings}
        elif action in {"enable", "disable"} and set(request.form) == {"_csrf_token", "action"}:
            # Preserve submissions from the earlier setup page without touching Kids.
            settings = device_preferences()
            settings["reader"] = "android" if action == "enable" else "automatic"
            settings["reader_fallback"] = False
            updates = {"android_private_speech": action == "enable", "android_speech": settings}
        else:
            abort(400)
        SettingsStore(current_app.config["DOCUMENT_ROOT"]).save({"media": updates}, str(g.user["username"]))
    except ValueError:
        abort(400)
    except OSError:
        flash("Voice selection could not be saved." if g.language == "en" else "Die Stimmenauswahl konnte nicht gespeichert werden.")
    else:
        audit("reader_voice_selection", "optional_setup", "android-speech", detail={"action": action})
        flash("Speech settings saved." if g.language == "en" else "Sprachausgabe gespeichert.")
    return redirect(url_for("optional_setup.index", _anchor="reader-speech"), code=303)


@bp.post("/android-speech/test")
@login_required
def test_android_speech():
    _require_device_speech_admin()
    if set(request.form) != {"_csrf_token", "scope", "language"}:
        abort(400)
    scope, language = request.form["scope"], request.form["language"]
    if scope not in android_speech_settings.SCOPES or language not in android_speech_settings.LANGUAGES:
        abort(400)
    sample = "Dies ist die lokale Sprachausgabe von SimpleOffice." if language == "de" else "This is SimpleOffice local speech."
    locale = "de-DE" if language == "de" else "en-US"
    model = None
    if scope == "kids":
        from .kids_learning import _selected_kids_piper as model

    return private_audio_response(lambda: sample, language=locale, device_scope=scope, piper_model=model)


@bp.post("/english-voice")
@login_required
def english_voice():
    if not is_admin(g.user):
        abort(403)
    if (request.args or set(request.form) != {"_csrf_token", "action"}
            or len(request.form.getlist("action")) != 1 or len(request.form.getlist("_csrf_token")) != 1):
        abort(400)
    action = request.form["action"]
    if action not in {"select", "restore"}:
        abort(400)
    root = current_app.config["DOCUMENT_ROOT"]
    model = english_voice_assets.installed_model(root)
    current = str(SettingsStore(root).settings()["media"].get("piper_model_en") or "").strip()
    if action == "select":
        if not model or current or not optional_local_setup.resolve_command("piper", root):
            flash(("The verified model or Piper engine is missing, or an English voice is already configured; settings were not changed."
                   if g.language == "en" else
                   "Das geprüfte Modell oder die Piper-Engine fehlt, oder es ist bereits eine englische Stimme eingerichtet. Die Einstellung blieb unverändert."))
            return redirect(url_for("optional_setup.index", _anchor="english-voice"), code=303)
        value = model
    else:
        if not model or current != model:
            flash(("The managed English model is not selected; settings were not changed."
                   if g.language == "en" else
                   "Das verwaltete englische Modell ist nicht ausgewählt. Die Einstellung blieb unverändert."))
            return redirect(url_for("optional_setup.index", _anchor="english-voice"), code=303)
        value = ""
    try:
        SettingsStore(root).save({"media": {"piper_model_en": value}}, str(g.user["username"]))
    except (OSError, ValueError):
        flash("English voice selection could not be saved." if g.language == "en" else
              "Auswahl der englischen Stimme konnte nicht gespeichert werden.")
    else:
        audit("english_voice_selection", "optional_setup", "english-voice", detail={"action": action})
        flash(("English voice selection saved." if g.language == "en" else "Englische Stimme ausgewählt.")
              if action == "select" else
              ("English default restored." if g.language == "en" else "Englische Standardstimme zurückgesetzt."))
    return redirect(url_for("optional_setup.index", _anchor="english-voice"), code=303)


@bp.post("/preference")
@login_required
def preference():
    if request.args or set(request.form) != {"_csrf_token", "action"} or len(request.form.getlist("action")) != 1:
        abort(400)
    action = request.form["action"]
    if action not in {"skip", "resume"}:
        abort(400)
    actor = str(g.user["username"])
    SetupStore(current_app.config["DOCUMENT_ROOT"]).set_optional_skipped(actor, action == "skip", actor)
    return redirect(url_for("optional_setup.index"), code=303)
