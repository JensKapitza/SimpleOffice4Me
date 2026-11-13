"""Personal quick-share entries rendered directly into QR codes."""
from __future__ import annotations

import ipaddress
import base64
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from .access_control import audit
from .auth import login_required
from .db import get_db
from .federation_qr_image import render_qr_svg
from .security_controls import protect_value, unprotect_value


bp = Blueprint("quick_share", __name__, url_prefix="/quick-share")
HISTORY_TTL = timedelta(days=30)
MAX_PAYLOAD_BYTES = 1800
MAX_HISTORY_ITEMS = 200


@bp.after_request
def _private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _ensure_schema() -> None:
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS quick_share_item (
            item_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('text','link','event')),
            title TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
            expires_at INTEGER
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS quick_share_owner_expiry "
        "ON quick_share_item(user_id, expires_at, created_at)"
    )
    db.commit()


def _purge_expired(user_id: int) -> None:
    _ensure_schema()
    get_db().execute(
        "DELETE FROM quick_share_item WHERE user_id=? AND expires_at IS NOT NULL AND expires_at<=?",
        (user_id, _now()),
    )
    get_db().commit()


def _clean_text(value: object, field: str, limit: int, *, multiline: bool = False) -> str:
    text = str(value or "").strip()
    if len(text.encode("utf-8")) > limit:
        raise ValueError(f"{field} ist zu lang (maximal {limit} UTF-8-Bytes).")
    for char in text:
        if unicodedata.category(char).startswith("C") and char not in ("\n", "\r", "\t"):
            raise ValueError(f"{field} enthält ein ungültiges Steuerzeichen.")
        if not multiline and char in "\r\n\t":
            raise ValueError(f"{field} darf keine Zeilenumbrüche enthalten.")
    return text


def _validated_url(value: object) -> str:
    url = _clean_text(value, "Link", MAX_PAYLOAD_BYTES)
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port
    except ValueError as exc:
        raise ValueError("Bitte einen gültigen http- oder https-Link eingeben.") from exc
    if (parts.scheme.casefold() not in {"http", "https"} or not host
            or parts.username is not None or parts.password is not None
            or any(unicodedata.category(char).startswith("C") for char in url)):
        raise ValueError("Links müssen mit http:// oder https:// beginnen und dürfen keine Zugangsdaten enthalten.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Bitte einen gültigen Link-Host eingeben.") from exc
        if (len(ascii_host) > 253 or any(
            not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
            or any(not (char.isascii() and (char.isalnum() or char == "-")) for char in label)
            for label in ascii_host.rstrip(".").split(".")
        )):
            raise ValueError("Bitte einen gültigen Link-Host eingeben.")
    else:
        if address.is_unspecified:
            raise ValueError("Der Link darf keine Platzhalter-IP-Adresse verwenden.")
    return url


def _ics_escape(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("\r\n", "\\n")
            .replace("\n", "\\n").replace("\r", "\\n")
            .replace(";", "\\;").replace(",", "\\,"))


def _fold_ics_line(line: str) -> str:
    chunks: list[str] = []
    current = ""
    limit = 75
    size = 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            chunks.append(current)
            current = char
            limit = 74  # The continuation line's leading space uses one octet.
            size = width
        else:
            current += char
            size += width
    chunks.append(current)
    return "\r\n ".join(chunks)


def _event_datetime(value: object, field: str) -> datetime:
    raw = _clean_text(value, field, 32)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field}: bitte Datum und Uhrzeit vollständig eingeben.") from exc
    if parsed.tzinfo is not None:
        raise ValueError(f"{field}: bitte lokale Zeit ohne Zeitzonenangabe verwenden.")
    return parsed


def _make_item(form) -> tuple[str, str, str]:
    kind = str(form.get("kind", "text"))
    if kind not in {"text", "link", "event"}:
        raise ValueError("Bitte Text, Link oder Termin auswählen.")
    label = _clean_text(form.get("title"), "Titel", 160)
    title = label or {
        "text": "Text", "link": "Link", "event": "Termin",
    }[kind]

    if kind == "text":
        payload = _clean_text(form.get("text"), "Text", MAX_PAYLOAD_BYTES, multiline=True)
        if not payload:
            raise ValueError("Bitte einen Text eingeben.")
    elif kind == "link":
        payload = _validated_url(form.get("url"))
    else:
        event_title = _clean_text(form.get("event_title"), "Terminname", 240) or label
        if not event_title:
            raise ValueError("Bitte einen Terminname eingeben.")
        title = label or event_title
        start = _event_datetime(form.get("start"), "Beginn")
        end = _event_datetime(form.get("end"), "Ende")
        if end <= start:
            raise ValueError("Das Ende muss nach dem Beginn liegen.")
        location = _clean_text(form.get("location"), "Ort", 240)
        description = _clean_text(form.get("description"), "Beschreibung", 800, multiline=True)
        uid = secrets.token_urlsafe(18) + "@simpleoffice.local"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines = [
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SimpleOffice4Me//Quick Share//DE",
            "CALSCALE:GREGORIAN", "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%S')}", f"DTEND:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{_ics_escape(event_title)}",
        ]
        if location:
            lines.append(f"LOCATION:{_ics_escape(location)}")
        if description:
            lines.append(f"DESCRIPTION:{_ics_escape(description)}")
        lines.extend(("END:VEVENT", "END:VCALENDAR"))
        payload = "\r\n".join(_fold_ics_line(line) for line in lines) + "\r\n"

    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Der Inhalt ist zu groß für einen gut scannbaren QR-Code. Bitte kürzen.")
    return kind, title, payload


def _owned_item(item_id: str):
    _purge_expired(int(g.user["id"]))
    row = get_db().execute(
        "SELECT * FROM quick_share_item WHERE item_id=? AND user_id=?",
        (item_id, int(g.user["id"])),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def _display_item(row) -> dict:
    item = dict(row)
    item["title"] = unprotect_value(item["title"], "quick-share-title")
    item["created_label"] = datetime.fromtimestamp(
        int(item["created_at"]), timezone.utc,
    ).astimezone().strftime("%Y-%m-%d %H:%M")
    return item


def _display_selected(row) -> dict:
    item = _display_item(row)
    item["payload"] = unprotect_value(item["payload"], "quick-share")
    item["temporary"] = False
    return item


@bp.get("")
@login_required
def index():
    user_id = int(g.user["id"])
    _purge_expired(user_id)
    rows = get_db().execute(
        "SELECT * FROM quick_share_item WHERE user_id=? "
        "ORDER BY pinned DESC, created_at DESC LIMIT ?",
        (user_id, MAX_HISTORY_ITEMS),
    ).fetchall()
    selected = None
    item_id = request.args.get("item", "").strip()
    if item_id:
        row = get_db().execute(
            "SELECT * FROM quick_share_item WHERE item_id=? AND user_id=?",
            (item_id, user_id),
        ).fetchone()
        if row is not None:
            selected = _display_selected(row)
    return render_template(
        "quick_share/index.html", items=[_display_item(row) for row in rows], selected=selected,
        form_values={}, form_error="",
    )


@bp.post("")
@login_required
def create():
    user_id = int(g.user["id"])
    _purge_expired(user_id)
    values = request.form.to_dict(flat=True)
    try:
        kind, title, payload = _make_item(request.form)
        temporary = request.form.get("save_mode") == "temporary"
        if request.form.get("save_mode") not in {"temporary", "history"}:
            raise ValueError("Bitte wählen, ob der QR-Code temporär oder in der History gespeichert werden soll.")
        if temporary:
            svg = render_qr_svg(payload)
            svg_data = base64.b64encode(svg.encode("utf-8") if isinstance(svg, str) else svg).decode("ascii")
            rows = get_db().execute(
                "SELECT * FROM quick_share_item WHERE user_id=? "
                "ORDER BY pinned DESC, created_at DESC LIMIT ?",
                (user_id, MAX_HISTORY_ITEMS),
            ).fetchall()
            selected = {
                "item_id": "", "kind": kind, "title": title, "payload": payload,
                "temporary": True, "pinned": False,
                "qr_src": "data:image/svg+xml;base64," + svg_data,
            }
            if kind == "event":
                selected["ics_href"] = "data:text/calendar;charset=utf-8;base64," + base64.b64encode(
                    payload.encode("utf-8"),
                ).decode("ascii")
            return render_template(
                "quick_share/index.html", items=[_display_item(row) for row in rows],
                selected=selected, form_values={}, form_error="",
            )
        count = get_db().execute(
            "SELECT COUNT(*) FROM quick_share_item WHERE user_id=?",
            (user_id,),
        ).fetchone()[0]
        if count >= MAX_HISTORY_ITEMS:
            raise ValueError("Die History ist voll. Bitte einen alten Eintrag löschen.")
        item_id = secrets.token_urlsafe(18)
        now = _now()
        expiry = now + int(HISTORY_TTL.total_seconds())
        _ensure_schema()
        get_db().execute(
            "INSERT INTO quick_share_item(item_id,user_id,kind,title,payload,created_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (item_id, user_id, kind, protect_value(title, "quick-share-title"), protect_value(payload, "quick-share"), now, expiry),
        )
        get_db().commit()
        audit("quick_share_created", "quick_share", item_id, detail={"kind": kind, "temporary": False})
        return redirect(url_for("quick_share.index", item=item_id) + "#created")
    except ValueError as exc:
        rows = get_db().execute(
            "SELECT * FROM quick_share_item WHERE user_id=? "
            "ORDER BY pinned DESC, created_at DESC LIMIT ?",
            (user_id, MAX_HISTORY_ITEMS),
        ).fetchall()
        return render_template(
            "quick_share/index.html", items=[_display_item(row) for row in rows], selected=None,
            form_values=values, form_error=str(exc),
        ), 400


@bp.get("/<item_id>.svg")
@login_required
def qr_svg(item_id: str):
    row = _owned_item(item_id)
    payload = unprotect_value(row["payload"], "quick-share")
    svg = render_qr_svg(payload)
    return Response(
        svg, content_type="image/svg+xml; charset=utf-8",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@bp.get("/<item_id>.ics")
@login_required
def event_ics(item_id: str):
    row = _owned_item(item_id)
    if row["kind"] != "event":
        abort(404)
    return Response(
        unprotect_value(row["payload"], "quick-share"), content_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="quick-share-event.ics"', "Cache-Control": "private, no-store"},
    )


@bp.post("/<item_id>/pin")
@login_required
def toggle_pin(item_id: str):
    row = _owned_item(item_id)
    pinned = not bool(row["pinned"])
    expires_at = None if pinned else _now() + int(HISTORY_TTL.total_seconds())
    get_db().execute(
        "UPDATE quick_share_item SET pinned=?, expires_at=? WHERE item_id=? AND user_id=?",
        (int(pinned), expires_at, item_id, int(g.user["id"])),
    )
    get_db().commit()
    audit("quick_share_pin_changed", "quick_share", item_id, detail={"pinned": pinned})
    flash("Eintrag angeheftet." if pinned else "Eintrag wird nach 30 Tagen automatisch entfernt.")
    return redirect(url_for("quick_share.index"))


@bp.post("/<item_id>/delete")
@login_required
def delete(item_id: str):
    _owned_item(item_id)
    get_db().execute(
        "DELETE FROM quick_share_item WHERE item_id=? AND user_id=?",
        (item_id, int(g.user["id"])),
    )
    get_db().commit()
    audit("quick_share_deleted", "quick_share", item_id)
    flash("Eintrag gelöscht.")
    return redirect(url_for("quick_share.index"))
