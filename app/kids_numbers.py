"""Counting and quantities game for preschool children in the Kids area."""
from __future__ import annotations

from flask import Blueprint, render_template

from .auth import login_required


bp = Blueprint("kids_numbers", __name__, url_prefix="/kids/numbers")

# Difficulty levels for the 4-5 year counting game. Every round plays a short,
# fixed number of random tasks so a full game fits into about 5-15 minutes.
# "max" is the highest quantity shown, "choices" the number of large answer
# buttons, "tasks" the tasks per round, "compare" enables more/less tasks.
NUMBERS_LEVELS = {
    "easy": {"max": 3, "choices": 2, "tasks": 8, "compare": False},
    "medium": {"max": 5, "choices": 3, "tasks": 8, "compare": True},
    "hard": {"max": 10, "choices": 3, "tasks": 8, "compare": True},
}

# Countable picture sets. Every item carries a symbol plus German and English
# singular/plural words so prompts stay speakable without reading skills.
NUMBERS_ITEMS = [
    {"emoji": "🐶", "de": "Hund", "de_plural": "Hunde", "en": "dog", "en_plural": "dogs"},
    {"emoji": "🐱", "de": "Katze", "de_plural": "Katzen", "en": "cat", "en_plural": "cats"},
    {"emoji": "🐰", "de": "Hase", "de_plural": "Hasen", "en": "rabbit", "en_plural": "rabbits"},
    {"emoji": "🐸", "de": "Frosch", "de_plural": "Frösche", "en": "frog", "en_plural": "frogs"},
    {"emoji": "🐞", "de": "Käfer", "de_plural": "Käfer", "en": "ladybug", "en_plural": "ladybugs"},
    {"emoji": "🦋", "de": "Schmetterling", "de_plural": "Schmetterlinge", "en": "butterfly", "en_plural": "butterflies"},
    {"emoji": "🐟", "de": "Fisch", "de_plural": "Fische", "en": "fish", "en_plural": "fish"},
    {"emoji": "🐤", "de": "Küken", "de_plural": "Küken", "en": "chick", "en_plural": "chicks"},
    {"emoji": "🍎", "de": "Apfel", "de_plural": "Äpfel", "en": "apple", "en_plural": "apples"},
    {"emoji": "⭐", "de": "Stern", "de_plural": "Sterne", "en": "star", "en_plural": "stars"},
]


@bp.after_request
def _private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.get("")
@bp.get("/")
@login_required
def index():
    """Counting game: grasp quantities, count and meet first numbers."""
    numbers_config = {"levels": NUMBERS_LEVELS, "items": NUMBERS_ITEMS}
    return render_template("kids/numbers.html", numbers_config=numbers_config, kids_mode=True)
