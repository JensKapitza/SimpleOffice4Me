"""Picture-matching game for toddlers plus the Kids dashboard shell."""
from __future__ import annotations

from flask import Blueprint, render_template

from .auth import login_required


bp = Blueprint("kids_matching", __name__, url_prefix="/kids")

# Categories for the 2-3 year matching game. Every item carries a symbol plus a
# German and an English word so the game stays usable without reading skills:
# the prompt is always spoken aloud and every choice is a large picture button.
# The "sounds" category matches an animal to its spoken sound word.
MATCHING_CATEGORIES = {
    "colors": [
        {"emoji": "🔴", "de": "Rot", "en": "red"},
        {"emoji": "🟡", "de": "Gelb", "en": "yellow"},
        {"emoji": "🟢", "de": "Grün", "en": "green"},
        {"emoji": "🔵", "de": "Blau", "en": "blue"},
        {"emoji": "🟠", "de": "Orange", "en": "orange"},
        {"emoji": "🟣", "de": "Lila", "en": "purple"},
        {"emoji": "🩷", "de": "Rosa", "en": "pink"},
        {"emoji": "🤎", "de": "Braun", "en": "brown"},
    ],
    "shapes": [
        {"emoji": "⭕", "de": "Kreis", "en": "circle"},
        {"emoji": "🟥", "de": "Viereck", "en": "square"},
        {"emoji": "🔺", "de": "Dreieck", "en": "triangle"},
        {"emoji": "⭐", "de": "Stern", "en": "star"},
        {"emoji": "❤️", "de": "Herz", "en": "heart"},
        {"emoji": "🌙", "de": "Mond", "en": "moon"},
        {"emoji": "🔷", "de": "Raute", "en": "diamond"},
        {"emoji": "➕", "de": "Kreuz", "en": "cross"},
    ],
    "animals": [
        {"emoji": "🐶", "de": "Hund", "en": "dog"},
        {"emoji": "🐱", "de": "Katze", "en": "cat"},
        {"emoji": "🐰", "de": "Hase", "en": "rabbit"},
        {"emoji": "🐸", "de": "Frosch", "en": "frog"},
        {"emoji": "🐻", "de": "Bär", "en": "bear"},
        {"emoji": "🦊", "de": "Fuchs", "en": "fox"},
        {"emoji": "🐵", "de": "Affe", "en": "monkey"},
        {"emoji": "🐤", "de": "Küken", "en": "chick"},
    ],
    "sounds": [
        {"emoji": "🐶", "de": "Hund", "en": "dog", "sound_de": "Wau wau", "sound_en": "woof woof"},
        {"emoji": "🐱", "de": "Katze", "en": "cat", "sound_de": "Miau", "sound_en": "meow"},
        {"emoji": "🐮", "de": "Kuh", "en": "cow", "sound_de": "Muh", "sound_en": "moo"},
        {"emoji": "🦆", "de": "Ente", "en": "duck", "sound_de": "Quak quak", "sound_en": "quack quack"},
        {"emoji": "🐑", "de": "Schaf", "en": "sheep", "sound_de": "Mäh", "sound_en": "baa"},
        {"emoji": "🐷", "de": "Schwein", "en": "pig", "sound_de": "Grunz grunz", "sound_en": "oink oink"},
        {"emoji": "🐔", "de": "Huhn", "en": "hen", "sound_de": "Gack gack", "sound_en": "cluck cluck"},
        {"emoji": "🦁", "de": "Löwe", "en": "lion", "sound_de": "Roar", "sound_en": "roar"},
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
    return render_template("kids/dashboard.html")


@bp.get("/zuordnen")
@login_required
def matching():
    """Matching game for 2-3 year olds: colors, shapes, animals, sounds."""
    return render_template("kids/matching.html", matching_config=matching_config())
