"""Private, server-saved coloring pictures for the kids area."""
from __future__ import annotations

import json
import math
import re
import secrets
import unicodedata
from datetime import datetime, timezone

from flask import Blueprint, abort, g, jsonify, render_template, request, url_for

from .auth import login_required
from .db import get_db


bp = Blueprint("kids_coloring", __name__, url_prefix="/kids/coloring")
TEMPLATE_ZONES = {
    "butterfly": frozenset({"left", "right", "body"}),
    "cat": frozenset({"head", "body", "left-ear", "right-ear", "left-leg", "right-leg", "tail"}),
    "fish": frozenset({"body", "tail", "fin-top", "fin-bottom"}),
    "flower": frozenset({"petal-top", "petal-left", "petal-right", "petal-bottom", "center", "stem", "leaf-left", "leaf-right"}),
    "dinosaur": frozenset({"body", "head", "tail", "leg-left", "leg-right", "spike-one", "spike-two", "spike-three"}),
    "rocket": frozenset({"body", "window", "fin-left", "fin-right", "flame"}),
    "blank": frozenset(),
}
PALETTE = frozenset({"#ef4444", "#f97316", "#facc15", "#22c55e", "#06b6d4", "#3b82f6", "#8b5cf6", "#ec4899", "#92400e", "#111827", "#ffffff"})
MAX_REQUEST_BYTES = 128 * 1024
MAX_STROKES = 160
MAX_POINTS_PER_STROKE = 1200
MAX_TOTAL_POINTS = 12_000
MAX_DRAWINGS_PER_USER = 100
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@bp.after_request
def _private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _ensure_schema() -> None:
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS kids_coloring_drawing (
        drawing_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        template_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS kids_coloring_owner_updated ON kids_coloring_drawing(user_id, updated_at DESC)")
    db.commit()


def _user_id() -> int:
    return int(g.user["id"])


def _clean_title(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Bitte gib einen Bildnamen mit höchstens 80 Zeichen ein.")
    title = value.strip()
    if not title or len(title) > 80 or any(unicodedata.category(char).startswith("C") for char in title):
        raise ValueError("Bitte gib einen Bildnamen mit höchstens 80 Zeichen ein.")
    return title


def _color(value: object) -> str:
    color = str(value or "").lower()
    if not _COLOR_RE.fullmatch(color) or color not in PALETTE:
        raise ValueError("Diese Farbe kann nicht gespeichert werden.")
    return color


def _number(value: object, upper: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Ungültiger Zeichenpunkt.")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Ungültiger Zeichenpunkt.") from exc
    if not math.isfinite(number) or not 0 <= number <= upper:
        raise ValueError("Ungültiger Zeichenpunkt.")
    return round(number, 2)


def _validate_payload(value: object, template_id: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"fills", "strokes"}:
        raise ValueError("Die Zeichnung hat ein ungültiges Format.")
    fills = value["fills"]
    strokes = value["strokes"]
    if not isinstance(fills, dict) or len(fills) > len(TEMPLATE_ZONES[template_id]):
        raise ValueError("Die Farbflächen sind ungültig.")
    clean_fills = {}
    for zone, color in fills.items():
        if not isinstance(zone, str) or zone not in TEMPLATE_ZONES[template_id]:
            raise ValueError("Diese Malfläche gehört nicht zur Vorlage.")
        clean_fills[zone] = _color(color)
    if not isinstance(strokes, list) or len(strokes) > MAX_STROKES:
        raise ValueError("Zu viele Pinselstriche in einem Bild.")
    total_points = 0
    clean_strokes = []
    for stroke in strokes:
        if not isinstance(stroke, dict) or set(stroke) != {"color", "width", "points"}:
            raise ValueError("Ein Pinselstrich ist ungültig.")
        points = stroke["points"]
        if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS_PER_STROKE:
            raise ValueError("Ein Pinselstrich hat ungültige Punkte.")
        total_points += len(points)
        if total_points > MAX_TOTAL_POINTS:
            raise ValueError("Das Bild enthält zu viele Zeichenpunkte.")
        width = _number(stroke["width"], 36)
        if not 1 <= width <= 36:
            raise ValueError("Die Pinselgröße ist ungültig.")
        clean_points = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("Ein Zeichenpunkt ist ungültig.")
            clean_points.append([_number(point[0], 800), _number(point[1], 600)])
        clean_strokes.append({"color": _color(stroke["color"]), "width": width, "points": clean_points})
    return {"fills": clean_fills, "strokes": clean_strokes}


def _parse_request() -> tuple[str, str, dict]:
    if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
        raise ValueError("Das Bild ist zu groß zum Speichern.")
    if not request.is_json:
        raise ValueError("Die Speicherdaten müssen als JSON gesendet werden.")
    raw = request.stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Das Bild ist zu groß zum Speichern.")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Die Speicherdaten sind ungültig.") from exc
    if not isinstance(body, dict) or set(body) != {"title", "template_id", "drawing"}:
        raise ValueError("Die Speicherdaten sind ungültig.")
    title = _clean_title(body["title"])
    template_id = body["template_id"]
    if not isinstance(template_id, str) or template_id not in TEMPLATE_ZONES:
        raise ValueError("Diese Malvorlage gibt es nicht.")
    drawing = _validate_payload(body["drawing"], template_id)
    encoded = json.dumps(drawing, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("Das Bild ist zu groß zum Speichern.")
    return title, template_id, drawing


@bp.get("")
@bp.get("/")
@login_required
def index():
    _ensure_schema()
    return render_template("kids/coloring.html", drawings_url=url_for("kids_coloring.list_drawings"))


@bp.get("/api/drawings")
@login_required
def list_drawings():
    _ensure_schema()
    rows = get_db().execute(
        "SELECT drawing_id,title,template_id,created_at,updated_at FROM kids_coloring_drawing "
        "WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
        (_user_id(), MAX_DRAWINGS_PER_USER),
    ).fetchall()
    return jsonify(drawings=[dict(row) for row in rows])


@bp.get("/api/drawings/<drawing_id>")
@login_required
def get_drawing(drawing_id: str):
    _ensure_schema()
    row = get_db().execute(
        "SELECT drawing_id,title,template_id,payload,created_at,updated_at FROM kids_coloring_drawing "
        "WHERE drawing_id=? AND user_id=?",
        (drawing_id, _user_id()),
    ).fetchone()
    if row is None:
        abort(404)
    drawing = dict(row)
    drawing["drawing"] = json.loads(drawing.pop("payload"))
    return jsonify(drawing=drawing)


@bp.post("/api/drawings")
@login_required
def create_drawing():
    _ensure_schema()
    try:
        title, template_id, drawing = _parse_request()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM kids_coloring_drawing WHERE user_id=?", (_user_id(),)).fetchone()[0] >= MAX_DRAWINGS_PER_USER:
        return jsonify(error="Du hast schon 100 Bilder gespeichert. Lösche erst ein Bild."), 409
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    drawing_id = secrets.token_urlsafe(18)
    db.execute(
        "INSERT INTO kids_coloring_drawing(drawing_id,user_id,title,template_id,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (drawing_id, _user_id(), title, template_id, json.dumps(drawing, ensure_ascii=False, separators=(",", ":")), now, now),
    )
    db.commit()
    return jsonify(drawing_id=drawing_id, updated_at=now), 201


@bp.put("/api/drawings/<drawing_id>")
@login_required
def update_drawing(drawing_id: str):
    _ensure_schema()
    try:
        title, template_id, drawing = _parse_request()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cursor = get_db().execute(
        "UPDATE kids_coloring_drawing SET title=?,template_id=?,payload=?,updated_at=? WHERE drawing_id=? AND user_id=?",
        (title, template_id, json.dumps(drawing, ensure_ascii=False, separators=(",", ":")), now, drawing_id, _user_id()),
    )
    get_db().commit()
    if not cursor.rowcount:
        abort(404)
    return jsonify(drawing_id=drawing_id, updated_at=now)


@bp.delete("/api/drawings/<drawing_id>")
@login_required
def delete_drawing(drawing_id: str):
    _ensure_schema()
    cursor = get_db().execute(
        "DELETE FROM kids_coloring_drawing WHERE drawing_id=? AND user_id=?",
        (drawing_id, _user_id()),
    )
    get_db().commit()
    if not cursor.rowcount:
        abort(404)
    return jsonify(ok=True)
