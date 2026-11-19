"""Picture-matching game for toddlers plus the Kids dashboard shell."""
from __future__ import annotations

import secrets

from flask import Blueprint, redirect, render_template, request, session, url_for

from .auth import login_required


bp = Blueprint("kids_matching", __name__, url_prefix="/kids")

# Categories for the 2-3 year matching game. Every item carries a symbol plus a
# German and an English word so the game stays usable without reading skills:
# the prompt is always spoken aloud and every choice is a large picture button.
# The "sounds" category matches an animal to its spoken sound word.
# prompt_audio/word_audio reference pre-generated clips under
# static/audio/kids/games/matching/<lang>/<key>.mp3 (see
# tools/generate_kids_game_audio.py). Slugs derive from the German word.
MATCHING_CATEGORIES = {
    "colors": [
        {"emoji": "🔴", "de": "Rot", "en": "red", "prompt_audio": "find_rot", "word_audio": "w_rot"},
        {"emoji": "🟡", "de": "Gelb", "en": "yellow", "prompt_audio": "find_gelb", "word_audio": "w_gelb"},
        {"emoji": "🟢", "de": "Grün", "en": "green", "prompt_audio": "find_gruen", "word_audio": "w_gruen"},
        {"emoji": "🔵", "de": "Blau", "en": "blue", "prompt_audio": "find_blau", "word_audio": "w_blau"},
        {"emoji": "🟠", "de": "Orange", "en": "orange", "prompt_audio": "find_orange", "word_audio": "w_orange"},
        {"emoji": "🟣", "de": "Lila", "en": "purple", "prompt_audio": "find_lila", "word_audio": "w_lila"},
        {"emoji": "🩷", "de": "Rosa", "en": "pink", "prompt_audio": "find_rosa", "word_audio": "w_rosa"},
        {"emoji": "🤎", "de": "Braun", "en": "brown", "prompt_audio": "find_braun", "word_audio": "w_braun"},
    ],
    "shapes": [
        {"emoji": "⭕", "de": "Kreis", "en": "circle", "prompt_audio": "find_kreis", "word_audio": "w_kreis"},
        {"emoji": "🟥", "de": "Viereck", "en": "square", "prompt_audio": "find_viereck", "word_audio": "w_viereck"},
        {"emoji": "🔺", "de": "Dreieck", "en": "triangle", "prompt_audio": "find_dreieck", "word_audio": "w_dreieck"},
        {"emoji": "⭐", "de": "Stern", "en": "star", "prompt_audio": "find_stern", "word_audio": "w_stern"},
        {"emoji": "❤️", "de": "Herz", "en": "heart", "prompt_audio": "find_herz", "word_audio": "w_herz"},
        {"emoji": "🌙", "de": "Mond", "en": "moon", "prompt_audio": "find_mond", "word_audio": "w_mond"},
        {"emoji": "🔷", "de": "Raute", "en": "diamond", "prompt_audio": "find_raute", "word_audio": "w_raute"},
        {"emoji": "➕", "de": "Kreuz", "en": "cross", "prompt_audio": "find_kreuz", "word_audio": "w_kreuz"},
    ],
    "animals": [
        {"emoji": "🐶", "de": "Hund", "en": "dog", "prompt_audio": "find_hund", "word_audio": "w_hund"},
        {"emoji": "🐱", "de": "Katze", "en": "cat", "prompt_audio": "find_katze", "word_audio": "w_katze"},
        {"emoji": "🐰", "de": "Hase", "en": "rabbit", "prompt_audio": "find_hase", "word_audio": "w_hase"},
        {"emoji": "🐸", "de": "Frosch", "en": "frog", "prompt_audio": "find_frosch", "word_audio": "w_frosch"},
        {"emoji": "🐻", "de": "Bär", "en": "bear", "prompt_audio": "find_baer", "word_audio": "w_baer"},
        {"emoji": "🦊", "de": "Fuchs", "en": "fox", "prompt_audio": "find_fuchs", "word_audio": "w_fuchs"},
        {"emoji": "🐵", "de": "Affe", "en": "monkey", "prompt_audio": "find_affe", "word_audio": "w_affe"},
        {"emoji": "🐤", "de": "Küken", "en": "chick", "prompt_audio": "find_kueken", "word_audio": "w_kueken"},
    ],
    "sounds": [
        {"emoji": "🐶", "de": "Hund", "en": "dog", "sound_de": "Wau wau", "sound_en": "woof woof", "prompt_audio": "ask_hund", "word_audio": "w_hund"},
        {"emoji": "🐱", "de": "Katze", "en": "cat", "sound_de": "Miau", "sound_en": "meow", "prompt_audio": "ask_katze", "word_audio": "w_katze"},
        {"emoji": "🐮", "de": "Kuh", "en": "cow", "sound_de": "Muh", "sound_en": "moo", "prompt_audio": "ask_kuh", "word_audio": "w_kuh"},
        {"emoji": "🦆", "de": "Ente", "en": "duck", "sound_de": "Quak quak", "sound_en": "quack quack", "prompt_audio": "ask_ente", "word_audio": "w_ente"},
        {"emoji": "🐑", "de": "Schaf", "en": "sheep", "sound_de": "Mäh", "sound_en": "baa", "prompt_audio": "ask_schaf", "word_audio": "w_schaf"},
        {"emoji": "🐷", "de": "Schwein", "en": "pig", "sound_de": "Grunz grunz", "sound_en": "oink oink", "prompt_audio": "ask_schwein", "word_audio": "w_schwein"},
        {"emoji": "🐔", "de": "Huhn", "en": "hen", "sound_de": "Gack gack", "sound_en": "cluck cluck", "prompt_audio": "ask_huhn", "word_audio": "w_huhn"},
        {"emoji": "🦁", "de": "Löwe", "en": "lion", "sound_de": "Roar", "sound_en": "roar", "prompt_audio": "ask_loewe", "word_audio": "w_loewe"},
    ],
}

# Difficulty levels: how many large choice buttons per task and how many tasks
# per round. Short rounds keep a full game within about 5-15 minutes.
MATCHING_LEVELS = {
    "easy": {"choices": 2, "rounds": 6},
    "medium": {"choices": 3, "rounds": 8},
    "hard": {"choices": 4, "rounds": 10},
}


def matching_config() -> dict:
    """JSON-safe game configuration embedded into the matching template."""
    return {"categories": MATCHING_CATEGORIES, "levels": MATCHING_LEVELS}


@bp.after_request
def _private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.get("")
@bp.get("/")
@login_required
def dashboard():
    """Kids dashboard shell: large symbol cards linking every kids offer."""
    return render_template("kids/dashboard.html", kids_mode=True)


@bp.get("/zuordnen")
@login_required
def matching():
    """Matching game for 2-3 year olds: colors, shapes, animals, sounds."""
    return render_template("kids/matching.html", matching_config=matching_config(), kids_mode=True)


def _exit_question():
    first = secrets.randbelow(14) + 6
    second = secrets.randbelow(14) + 6
    session["kids_exit_answer"] = first + second
    return first, second


@bp.get("/erwachsene")
@login_required
def exit_gate():
    """Grown-ups gate guarding the way out of the kids area."""
    first, second = _exit_question()
    return render_template(
        "kids/exit_gate.html", first=first, second=second, error=False, kids_mode=True,
    )


@bp.post("/erwachsene")
@login_required
def exit_gate_answer():
    """Leave the kids area after solving a grown-ups arithmetic question.

    This is a toddler gate, not a security boundary: the session is already
    authenticated, so a wrong answer only presents a fresh question instead
    of locking anything.
    """
    expected = session.pop("kids_exit_answer", None)
    try:
        given = int(request.form.get("answer") or "")
    except ValueError:
        given = None
    if expected is not None and given == expected:
        return redirect(url_for("documents.index"))
    first, second = _exit_question()
    return render_template(
        "kids/exit_gate.html", first=first, second=second, error=True, kids_mode=True,
    )
