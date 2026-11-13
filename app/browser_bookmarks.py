"""Private, portable browser bookmark collection with Firefox HTML import/export."""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import json
import secrets
import sqlite3
import time
import unicodedata
from urllib.parse import urlsplit

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.exceptions import NotFound

from .auth import login_required
from .db import get_db


bp = Blueprint("browser_bookmarks", __name__, url_prefix="/bookmarks")
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ITEMS = 5000
IMPORT_DRAFT_TTL = 15 * 60
MAX_DRAFT_BYTES = 4 * 1024 * 1024


@bp.after_request
def _private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _ensure_schema() -> None:
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS browser_bookmark (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
        url TEXT NOT NULL, folder TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
        pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, url, folder))""")
    db.execute("CREATE INDEX IF NOT EXISTS browser_bookmark_owner ON browser_bookmark(user_id, pinned, title)")
    db.execute("""CREATE TABLE IF NOT EXISTS browser_bookmark_import (
        import_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, payload TEXT NOT NULL,
        invalid_count INTEGER NOT NULL, expires_at INTEGER NOT NULL)""")
    db.execute("DELETE FROM browser_bookmark_import WHERE expires_at<=?", (int(time.time()),))
    db.commit()


def _clean(value, label: str, limit: int) -> str:
    result = str(value or "").strip()
    if len(result.encode("utf-8")) > limit or any(unicodedata.category(c).startswith("C") for c in result):
        raise ValueError(f"{label}: ungültig oder zu lang.")
    return result


def _valid_url(value) -> str:
    url = _clean(value, "URL", 4096)
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Nur gültige http://- und https://-Links sind erlaubt.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("Nur gültige http://- und https://-Links ohne Zugangsdaten sind erlaubt.")
    return url


def _user_id() -> int:
    return int(g.user["id"])


@bp.get("/")
@login_required
def index():
    _ensure_schema()
    try:
        query = _clean(request.args.get("q"), "Suche", 200)
    except ValueError:
        query = ""
        flash("Der Suchtext ist ungültig oder zu lang.")
    db = get_db()
    if query:
        rows = db.execute("SELECT * FROM browser_bookmark WHERE user_id=? AND (title LIKE ? OR url LIKE ? OR folder LIKE ? OR tags LIKE ?) ORDER BY pinned DESC, title COLLATE NOCASE", (_user_id(), *([f"%{query}%"] * 4))).fetchall()
    else:
        rows = db.execute("SELECT * FROM browser_bookmark WHERE user_id=? ORDER BY pinned DESC, title COLLATE NOCASE", (_user_id(),)).fetchall()
    return render_template("bookmarks/index.html", bookmarks=rows, query=query, form_error=None)


@bp.post("/add")
@login_required
def add():
    _ensure_schema()
    try:
        title = _clean(request.form.get("title"), "Titel", 500) or "Lesezeichen"
        url = _valid_url(request.form.get("url"))
        folder = _clean(request.form.get("folder"), "Ordner", 256)
        tags = _clean(request.form.get("tags"), "Schlagwörter", 500)
        get_db().execute("INSERT INTO browser_bookmark(id,user_id,title,url,folder,tags) VALUES(?,?,?,?,?,?)", (secrets.token_urlsafe(18), _user_id(), title, url, folder, tags))
        get_db().commit()
        flash("Lesezeichen gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    except sqlite3.IntegrityError as exc:
        # The unique key makes repeated imports/adds harmless.
        if "UNIQUE constraint failed" in str(exc):
            flash("Dieses Lesezeichen ist in diesem Ordner bereits vorhanden.")
        else:
            raise
    return redirect(url_for("browser_bookmarks.index"))


@bp.post("/<bookmark_id>/update")
@login_required
def update(bookmark_id):
    _ensure_schema()
    try:
        title = _clean(request.form.get("title"), "Titel", 500) or "Lesezeichen"
        url = _valid_url(request.form.get("url"))
        folder = _clean(request.form.get("folder"), "Ordner", 256)
        tags = _clean(request.form.get("tags"), "Schlagwörter", 500)
        cur = get_db().execute("UPDATE browser_bookmark SET title=?,url=?,folder=?,tags=? WHERE id=? AND user_id=?", (title,url,folder,tags,bookmark_id,_user_id()))
        get_db().commit()
        if not cur.rowcount:
            abort(404)
        flash("Lesezeichen aktualisiert.")
    except ValueError as exc:
        flash(str(exc))
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed" in str(exc):
            flash("Dieses Lesezeichen ist in diesem Ordner bereits vorhanden.")
        else:
            raise
    return redirect(url_for("browser_bookmarks.index"))


@bp.post("/<bookmark_id>/delete")
@login_required
def delete(bookmark_id):
    _ensure_schema()
    cur = get_db().execute("DELETE FROM browser_bookmark WHERE id=? AND user_id=?", (bookmark_id, _user_id()))
    get_db().commit()
    if not cur.rowcount:
        abort(404)
    flash("Lesezeichen gelöscht.")
    return redirect(url_for("browser_bookmarks.index"))


@bp.post("/<bookmark_id>/pin")
@login_required
def pin(bookmark_id):
    _ensure_schema()
    cur = get_db().execute("UPDATE browser_bookmark SET pinned=1-pinned WHERE id=? AND user_id=?", (bookmark_id,_user_id()))
    get_db().commit()
    if not cur.rowcount:
        abort(404)
    return redirect(url_for("browser_bookmarks.index"))


class _FirefoxParser(HTMLParser):
    """Read only bookmark anchors and their Netscape folder context."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.folder_stack = []
        self.folder_dl_stack = []
        self.pending_folder = None
        self.in_h3 = False
        self.h3_text = []
        self.anchor = None
        self.items = []
        self.too_many = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "h3":
            self.in_h3, self.h3_text = True, []
        elif tag.lower() == "dl" and self.pending_folder is not None:
            pushed = len(self.folder_stack) < 20
            if pushed:
                self.folder_stack.append(self.pending_folder)
            self.pending_folder = None
            self.folder_dl_stack.append(pushed)
        elif tag.lower() == "dl":
            self.folder_dl_stack.append(False)
        elif tag.lower() == "a":
            self.anchor = [attrs.get("href", ""), attrs.get("tags", ""), []]

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "h3" and self.in_h3:
            self.in_h3 = False
            self.pending_folder = "".join(self.h3_text).strip()[:256]
        elif tag == "dl" and self.folder_dl_stack:
            if self.folder_dl_stack.pop() and self.folder_stack:
                self.folder_stack.pop()
        elif tag == "a" and self.anchor:
            href, tags, text = self.anchor
            self.items.append((href, "".join(text).strip() or href, "/".join(self.folder_stack), tags))
            self.anchor = None
            if len(self.items) > MAX_IMPORT_ITEMS:
                self.too_many = True

    def handle_data(self, data):
        if self.in_h3:
            self.h3_text.append(data)
        if self.anchor:
            self.anchor[2].append(data)


@bp.post("/import")
@login_required
def import_firefox():
    _ensure_schema()
    upload = request.files.get("file")
    if upload is None:
        flash("Bitte eine Firefox-Lesezeichen-HTML-Datei auswählen.")
        return redirect(url_for("browser_bookmarks.index"))
    data = upload.stream.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        flash("Die Importdatei ist größer als 2 MiB.")
        return redirect(url_for("browser_bookmarks.index"))
    try:
        parser = _FirefoxParser()
        parser.feed(data.decode("utf-8-sig", errors="replace"))
        if parser.too_many:
            raise ValueError("Die Datei enthält mehr als 5.000 Lesezeichen.")
        db = get_db()
        valid = []
        invalid = 0
        for raw_url, raw_title, folder, raw_tags in parser.items:
            try:
                href = _valid_url(raw_url)
                title = _clean(raw_title, "Titel", 500) or href
                tags = _clean(raw_tags, "Schlagwörter", 500)
                folder = _clean(folder, "Ordner", 256)
            except ValueError:
                invalid += 1
                continue
            valid.append({"title": title, "url": href, "folder": folder, "tags": tags})
        serialized = json.dumps(valid, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > MAX_DRAFT_BYTES:
            raise ValueError("Die aufbereiteten Lesezeichen überschreiten das Größenlimit.")
        import_id = secrets.token_urlsafe(24)
        db.execute("DELETE FROM browser_bookmark_import WHERE user_id=?", (_user_id(),))
        db.execute("INSERT INTO browser_bookmark_import(import_id,user_id,payload,invalid_count,expires_at) VALUES(?,?,?,?,?)", (import_id,_user_id(),serialized,invalid,int(time.time())+IMPORT_DRAFT_TTL))
        db.commit()
        session["browser_bookmark_import_id"] = import_id
        return redirect(url_for("browser_bookmarks.import_preview"))
    except (UnicodeError, ValueError) as exc:
        get_db().rollback()
        flash(f"Import fehlgeschlagen: {exc}")
    return redirect(url_for("browser_bookmarks.index"))


def _current_import_draft():
    import_id = session.get("browser_bookmark_import_id")
    if not isinstance(import_id, str):
        abort(404)
    row = get_db().execute("SELECT * FROM browser_bookmark_import WHERE import_id=? AND user_id=? AND expires_at>?", (import_id,_user_id(),int(time.time()))).fetchone()
    if row is None:
        session.pop("browser_bookmark_import_id", None)
        abort(404)
    payload = json.loads(row["payload"])
    if not isinstance(payload, list) or len(payload) > MAX_IMPORT_ITEMS:
        abort(400)
    return row, payload


@bp.get("/import-preview")
@login_required
def import_preview():
    _ensure_schema()
    try:
        draft, items = _current_import_draft()
    except NotFound:
        flash("Es gibt keine aktive Importvorschau. Bitte wähle die Firefox-Datei erneut aus.")
        return redirect(url_for("browser_bookmarks.index"))
    existing = {
        (row["url"], row["folder"])
        for row in get_db().execute("SELECT url,folder FROM browser_bookmark WHERE user_id=?", (_user_id(),)).fetchall()
    }
    seen = set()
    new_items = []
    duplicate_count = 0
    for item in items:
        key = (item["url"], item["folder"])
        if key in existing or key in seen:
            duplicate_count += 1
        else:
            seen.add(key)
            new_items.append(item)
    return render_template(
        "bookmarks/import_preview.html", new_count=len(new_items),
        duplicate_count=duplicate_count, invalid_count=draft["invalid_count"],
        examples=new_items[:20],
    )


@bp.post("/import-confirm")
@login_required
def import_confirm():
    _ensure_schema()
    draft, items = _current_import_draft()
    db = get_db()
    added = 0
    try:
        db.execute("BEGIN")
        for item in items:
            cur = db.execute(
                "INSERT OR IGNORE INTO browser_bookmark(id,user_id,title,url,folder,tags) VALUES(?,?,?,?,?,?)",
                (secrets.token_urlsafe(18),_user_id(),item["title"],item["url"],item["folder"],item["tags"]),
            )
            added += cur.rowcount
        skipped = len(items) - added + int(draft["invalid_count"])
        db.execute("DELETE FROM browser_bookmark_import WHERE import_id=? AND user_id=?", (draft["import_id"],_user_id()))
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise
    session.pop("browser_bookmark_import_id", None)
    flash(f"Import abgeschlossen: {added} neu, {skipped} übersprungen.")
    return redirect(url_for("browser_bookmarks.index"))


@bp.post("/import-cancel")
@login_required
def import_cancel():
    _ensure_schema()
    import_id = session.pop("browser_bookmark_import_id", None)
    if isinstance(import_id, str):
        get_db().execute("DELETE FROM browser_bookmark_import WHERE import_id=? AND user_id=?", (import_id,_user_id()))
        get_db().commit()
    flash("Importvorschau verworfen.")
    return redirect(url_for("browser_bookmarks.index"))


@bp.get("/export")
@login_required
def export_firefox():
    _ensure_schema()
    rows = get_db().execute("SELECT title,url,folder,tags FROM browser_bookmark WHERE user_id=? ORDER BY id", (_user_id(),)).fetchall()
    tree = {"folders": {}, "items": []}
    for row in rows:
        node = tree
        for folder in filter(None, row["folder"].split("/")):
            node = node["folders"].setdefault(folder, {"folders": {}, "items": []})
        node["items"].append(row)

    def render_node(node, depth=0):
        pad = "    " * depth
        lines = []
        for folder, child in sorted(node["folders"].items(), key=lambda pair: pair[0].casefold()):
            lines.extend([f"{pad}<DT><H3>{escape(folder)}</H3>", f"{pad}<DL><p>"])
            lines.extend(render_node(child, depth + 1))
            lines.append(f"{pad}</DL><p>")
        for row in node["items"]:
            tags = f' TAGS="{escape(row["tags"], quote=True)}"' if row["tags"] else ""
            lines.append(f'{pad}<DT><A HREF="{escape(row["url"], quote=True)}"{tags}>{escape(row["title"])}</A>')
        return lines

    body = "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<META HTTP-EQUIV=\"Content-Type\" CONTENT=\"text/html; charset=UTF-8\">\n<TITLE>Bookmarks</TITLE>\n<H1>Bookmarks</H1>\n<DL><p>\n" + "\n".join(render_node(tree, 1)) + "\n</DL><p>\n"
    return Response(body, content_type="text/html; charset=utf-8", headers={"Content-Disposition": "attachment; filename=\"simpleoffice-bookmarks.html\""})
