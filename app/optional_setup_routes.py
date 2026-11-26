"""One optional setup entry for adults, with explicit admin-only queue admission."""
from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from werkzeug.wsgi import LimitedStream

from . import kids_hand_assets, kids_piper_assets, optional_hand_game_install, optional_local_setup
from .access_control import audit, is_admin
from .auth import login_required
from .media_settings import PIPER_READER_THORSTEN
from .private_read_aloud import android_speech_available, android_speech_enabled
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
    return render_template(
        "optional_setup/index.html",
        readiness=optional_local_setup.readiness(root, manual_hand_directory=manual),
        optional_skipped=setup["optional_skipped"],
        manage=is_admin(g.user),
        android_speech_available=android_speech_available(),
        android_speech_enabled=android_speech_enabled(),
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
    )


@bp.get("/progress")
@login_required
def progress():
    return jsonify(optional_local_setup.progress_states(current_app.config["DOCUMENT_ROOT"]))


def limit_request() -> None:
    """Apply the small mutation-body cap before session CSRF parses a form."""
    if request.endpoint not in {"optional_setup.install", "optional_setup.preference",
                                "optional_setup.activate_voice", "optional_setup.select_reader_voice",
                                "optional_setup.set_android_speech"}:
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
        "failed": ("Setup could not be started. You can retry.", "Einrichtung konnte nicht starten. Wiederholen ist möglich."),
    }
    flash(messages[state][0 if g.language == "en" else 1])
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


@bp.post("/android-speech")
@login_required
def set_android_speech():
    if not is_admin(g.user) or not android_speech_available():
        abort(403)
    if (request.args or set(request.form) != {"_csrf_token", "action"}
            or len(request.form.getlist("action")) != 1 or len(request.form.getlist("_csrf_token")) != 1):
        abort(400)
    action = request.form["action"]
    if action not in {"enable", "disable"}:
        abort(400)
    try:
        SettingsStore(current_app.config["DOCUMENT_ROOT"]).save(
            {"media": {"android_private_speech": action == "enable"}}, str(g.user["username"]),
        )
    except (OSError, ValueError):
        flash("Voice selection could not be saved." if g.language == "en" else "Die Stimmenauswahl konnte nicht gespeichert werden.")
    else:
        audit("reader_voice_selection", "optional_setup", "android-speech", detail={"action": action})
    return redirect(url_for("optional_setup.index", _anchor="reader-speech"), code=303)


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
