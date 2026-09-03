"""Mobile-first inventory capture for books and tagged physical objects.

The canonical inventory remains ObjectStore. This module only adds scanning,
book metadata lookup, rate-limited Amazon handoff and locally stored photos.
Amazon pages are never scraped or fetched by the server.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlsplit
from urllib.request import Request, urlopen

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from .auth import login_required
from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .object_store import ObjectStore


bp = Blueprint("inventory", __name__, url_prefix="/inventory")

LOOKUP_INTERVAL_SECONDS = 5
MAX_PHOTO_BYTES = 12 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 5
MAX_METADATA_BYTES = 2 * 1024 * 1024
ALLOWED_METADATA_HOSTS = {"www.googleapis.com", "openlibrary.org"}
BOOK_FIELDS = (
    "isbn",
    "barcode",
    "nfc_id",
    "authors",
    "publisher",
    "published_date",
    "page_count",
    "language",
    "categories",
    "market_price",
    "currency",
    "price_source",
    "metadata_source",
    "metadata_checked_at",
)


def _objects() -> ObjectStore:
    return ObjectStore(current_app.config["DOCUMENT_ROOT"])


def _inventory() -> "InventoryEnrichmentStore":
    return InventoryEnrichmentStore(current_app.config["DOCUMENT_ROOT"])


def _single_line(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _isbn13_checksum(prefix: str) -> str:
    total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(prefix))
    return str((10 - total % 10) % 10)


def normalize_isbn(value: Any) -> str:
    """Return a validated canonical ISBN-13, converting valid ISBN-10 values."""
    raw = re.sub(r"[^0-9Xx]", "", str(value or ""))
    if len(raw) == 13 and raw.isdigit():
        if not raw.startswith(("978", "979")):
            raise ValueError("ISBN-13 muss mit 978 oder 979 beginnen")
        if _isbn13_checksum(raw[:12]) != raw[-1]:
            raise ValueError("Ungültige ISBN-Prüfziffer")
        return raw
    if len(raw) == 10 and raw[:9].isdigit() and (raw[-1].isdigit() or raw[-1].upper() == "X"):
        digits = [int(char) for char in raw[:9]] + [10 if raw[-1].upper() == "X" else int(raw[-1])]
        if sum((10 - index) * digit for index, digit in enumerate(digits)) % 11:
            raise ValueError("Ungültige ISBN-Prüfziffer")
        prefix = "978" + raw[:9]
        return prefix + _isbn13_checksum(prefix)
    raise ValueError("ISBN muss eine gültige ISBN-10 oder ISBN-13 sein")


def isbn_from_barcode(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 13 and digits.startswith(("978", "979")):
        try:
            return normalize_isbn(digits)
        except ValueError:
            return ""
    return ""


def _http_json(url: str) -> dict[str, Any]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_METADATA_HOSTS:
        raise ValueError("Nicht erlaubte Metadatenquelle")
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SimpleOffice4Me-inventory/1.0",
        },
    )
    with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310 - allowlisted HTTPS hosts only
        if int(getattr(response, "status", 200)) != 200:
            raise ValueError("Metadatenquelle antwortet nicht erfolgreich")
        data = response.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise ValueError("Metadatenantwort ist zu groß")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Ungültige Metadatenantwort")
    return value


def _google_item_matches_isbn(item: dict[str, Any], isbn: str) -> bool:
    info = item.get("volumeInfo") if isinstance(item.get("volumeInfo"), dict) else {}
    identifiers = info.get("industryIdentifiers") if isinstance(info.get("industryIdentifiers"), list) else []
    normalized: set[str] = set()
    for row in identifiers:
        if not isinstance(row, dict):
            continue
        try:
            normalized.add(normalize_isbn(row.get("identifier", "")))
        except ValueError:
            continue
    # Some Google Books records omit industryIdentifiers entirely. In that case
    # the ISBN query itself is the best available selector; otherwise require an
    # exact normalized identifier so that a neighbouring edition is not stored.
    return not normalized or isbn in normalized


def parse_google_books(payload: dict[str, Any], isbn: str) -> dict[str, Any]:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return {}
    item = next(
        (
            candidate
            for candidate in items
            if isinstance(candidate, dict) and _google_item_matches_isbn(candidate, isbn)
        ),
        None,
    )
    if item is None:
        return {}
    info = item.get("volumeInfo") if isinstance(item.get("volumeInfo"), dict) else {}
    sale = item.get("saleInfo") if isinstance(item.get("saleInfo"), dict) else {}
    price = sale.get("retailPrice") if isinstance(sale.get("retailPrice"), dict) else sale.get("listPrice")
    if not isinstance(price, dict):
        price = {}
    authors = info.get("authors") if isinstance(info.get("authors"), list) else []
    categories = info.get("categories") if isinstance(info.get("categories"), list) else []
    return {
        "isbn": isbn,
        "title": _single_line(info.get("title"), 300),
        "subtitle": _single_line(info.get("subtitle"), 300),
        "authors": "; ".join(_single_line(author, 160) for author in authors if _single_line(author, 160)),
        "publisher": _single_line(info.get("publisher"), 240),
        "published_date": _single_line(info.get("publishedDate"), 40),
        "description": str(info.get("description") or "").strip()[:8000],
        "page_count": str(info.get("pageCount") or ""),
        "language": _single_line(info.get("language"), 20),
        "categories": "; ".join(_single_line(category, 160) for category in categories if _single_line(category, 160)),
        "market_price": str(price.get("amount") or ""),
        "currency": _single_line(price.get("currencyCode"), 3).upper(),
        "price_source": "Google Books" if price.get("amount") is not None else "",
        "metadata_source": "Google Books",
    }


def parse_openlibrary(payload: dict[str, Any], isbn: str) -> dict[str, Any]:
    value = payload.get(f"ISBN:{isbn}") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        return {}
    authors = value.get("authors") if isinstance(value.get("authors"), list) else []
    publishers = value.get("publishers") if isinstance(value.get("publishers"), list) else []
    subjects = value.get("subjects") if isinstance(value.get("subjects"), list) else []
    author_names = [_single_line(item.get("name"), 160) for item in authors if isinstance(item, dict)]
    publisher_names = [_single_line(item.get("name"), 180) for item in publishers if isinstance(item, dict)]
    subject_names = [_single_line(item.get("name"), 160) for item in subjects[:12] if isinstance(item, dict)]
    return {
        "isbn": isbn,
        "title": _single_line(value.get("title"), 300),
        "subtitle": _single_line(value.get("subtitle"), 300),
        "authors": "; ".join(item for item in author_names if item),
        "publisher": "; ".join(item for item in publisher_names if item),
        "published_date": _single_line(value.get("publish_date"), 80),
        "description": "",
        "page_count": str(value.get("number_of_pages") or ""),
        "language": "",
        "categories": "; ".join(item for item in subject_names if item),
        "market_price": "",
        "currency": "",
        "price_source": "",
        "metadata_source": "Open Library",
    }


def merge_book_metadata(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    result = dict(primary or {})
    for key, value in (fallback or {}).items():
        if not result.get(key) and value not in (None, "", []):
            result[key] = value
    sources = []
    for candidate in (
        primary.get("metadata_source") if primary else "",
        fallback.get("metadata_source") if fallback else "",
    ):
        if candidate and candidate not in sources:
            sources.append(candidate)
    if sources:
        result["metadata_source"] = " + ".join(sources)
    return result


def lookup_book_metadata(isbn: str) -> dict[str, Any]:
    google: dict[str, Any] = {}
    openlibrary: dict[str, Any] = {}
    errors: list[str] = []
    try:
        google_payload = _http_json(
            "https://www.googleapis.com/books/v1/volumes?q=" + quote_plus(f"isbn:{isbn}") + "&maxResults=5"
        )
        google = parse_google_books(google_payload, isbn)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(type(exc).__name__)
    # Open Library is used as a fallback and gap filler, never as a high-rate crawler.
    if not google or any(not google.get(key) for key in ("title", "authors", "publisher", "published_date")):
        try:
            open_payload = _http_json(
                "https://openlibrary.org/api/books?bibkeys=" + quote_plus(f"ISBN:{isbn}") + "&format=json&jscmd=data"
            )
            openlibrary = parse_openlibrary(open_payload, isbn)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(type(exc).__name__)
    result = merge_book_metadata(google, openlibrary)
    if result:
        result["metadata_checked_at"] = utc_now()
        result["lookup_errors"] = errors
    return result


def _image_extension(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("Nur JPEG, PNG und WebP werden als Inventarfoto akzeptiert")


def _money(value: Any) -> str:
    raw = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not raw:
        return ""
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Preis ist ungültig") from exc
    if not amount.is_finite() or amount < 0 or amount > Decimal("100000000"):
        raise ValueError("Preis liegt außerhalb des zulässigen Bereichs")
    return f"{amount:.2f}"


def _object_fields(form: Any, isbn: str) -> dict[str, str]:
    values = {
        "isbn": isbn,
        "barcode": _single_line(form.get("barcode"), 80),
        "nfc_id": _single_line(form.get("nfc_id"), 240),
        "authors": _single_line(form.get("authors"), 600),
        "publisher": _single_line(form.get("publisher"), 300),
        "published_date": _single_line(form.get("published_date"), 80),
        "page_count": _single_line(form.get("page_count"), 20),
        "language": _single_line(form.get("language"), 20),
        "categories": _single_line(form.get("categories"), 600),
        "market_price": _money(form.get("market_price")),
        "currency": _single_line(form.get("currency"), 3).upper(),
        "price_source": _single_line(form.get("price_source"), 120),
        "metadata_source": _single_line(form.get("metadata_source"), 160),
        "metadata_checked_at": _single_line(form.get("metadata_checked_at"), 80),
    }
    return {key: value for key, value in values.items() if value}


def _fields_text(fields: dict[str, str]) -> str:
    return "\n".join(f"{key}={_single_line(value, 1000)}" for key, value in fields.items() if value)


def _find_exact(identifier: str = "", nfc_id: str = "") -> dict[str, Any] | None:
    identifier = _single_line(identifier, 120)
    nfc_id = _single_line(nfc_id, 240)
    queries = list(dict.fromkeys(value for value in (identifier, nfc_id) if value))
    if not queries:
        return None
    store = _objects()
    candidates: dict[str, dict[str, Any]] = {}
    for query in queries:
        for item in store.objects(query):
            candidates[str(item.get("object_id", ""))] = item
    for item in candidates.values():
        if identifier and str(item.get("identifier", "")).strip() == identifier:
            return item
        if nfc_id and str(item.get("fields", {}).get("nfc_id", "")).strip() == nfc_id:
            return item
    return None


class InventoryEnrichmentStore:
    """Small sidecar for photos and external lookup audit data.

    ObjectStore remains the authoritative item register. Sidecar records are
    keyed only by ObjectStore object IDs and can be rebuilt/ignored independently.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "inventory"
        self.media_directory = self.directory / "media"
        self.index_path = self.directory / "index.json"
        self.rate_path = self.directory / "rate-limits.json"
        self.lock_path = self.directory / ".inventory-write.lock"

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def object_meta(self, object_id: str) -> dict[str, Any]:
        data = self._read(self.index_path)
        value = data.get("objects", {}).get(str(object_id), {}) if isinstance(data.get("objects"), dict) else {}
        return dict(value) if isinstance(value, dict) else {}

    def object_metas(self) -> dict[str, dict[str, Any]]:
        data = self._read(self.index_path)
        objects = data.get("objects", {}) if isinstance(data.get("objects"), dict) else {}
        return {
            str(object_id): dict(value)
            for object_id, value in objects.items()
            if isinstance(value, dict)
        }

    def record_snapshot(self, object_id: str, fields: dict[str, str], actor: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            data = self._read(self.index_path)
            objects = data.setdefault("objects", {})
            entry = objects.setdefault(object_id, {})
            entry["book"] = dict(fields)
            entry["updated_at"] = utc_now()
            entry["updated_by"] = actor
            data["version"] = 1
            atomic_json_write(self.index_path, data)

    def save_photo(self, object_id: str, upload: Any, actor: str) -> dict[str, Any] | None:
        if upload is None or not getattr(upload, "filename", ""):
            return None
        data = upload.stream.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            raise ValueError("Inventarfoto ist größer als 12 MiB")
        extension = _image_extension(data)
        digest = hashlib.sha256(data).hexdigest()
        filename = f"{uuid.uuid4().hex}{extension}"
        target_dir = self.media_directory / object_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        target.write_bytes(data)
        photo = {
            "filename": filename,
            "sha256": digest,
            "size": len(data),
            "created_at": utc_now(),
            "created_by": actor,
        }
        with exclusive_file_lock(self.lock_path):
            index = self._read(self.index_path)
            objects = index.setdefault("objects", {})
            entry = objects.setdefault(object_id, {})
            photos = entry.setdefault("photos", [])
            if not isinstance(photos, list):
                photos = []
                entry["photos"] = photos
            photos.append(photo)
            entry["updated_at"] = utc_now()
            entry["updated_by"] = actor
            index["version"] = 1
            atomic_json_write(self.index_path, index)
        return photo

    def media_path(self, object_id: str, filename: str) -> Path:
        meta = self.object_meta(object_id)
        allowed = {
            str(photo.get("filename"))
            for photo in meta.get("photos", [])
            if isinstance(photo, dict) and photo.get("filename")
        }
        if filename not in allowed or not re.fullmatch(r"[0-9a-f]{32}\.(?:jpg|png|webp)", filename):
            raise ValueError("Unbekanntes Inventarfoto")
        path = (self.media_directory / object_id / filename).resolve()
        base = self.media_directory.resolve()
        if base not in path.parents or not path.is_file() or path.is_symlink():
            raise ValueError("Inventarfoto ist nicht verfügbar")
        return path

    def consume_rate_limit(self, actor: str, action: str, interval: int = LOOKUP_INTERVAL_SECONDS) -> tuple[bool, int]:
        now = time.time()
        key = hashlib.sha256(actor.encode("utf-8")).hexdigest()[:24]
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read(self.rate_path)
            actions = state.setdefault(action, {})
            try:
                last = float(actions.get(key, 0))
            except (TypeError, ValueError):
                last = 0.0
            remaining = interval - (now - last)
            if remaining > 0:
                return False, max(1, int(math.ceil(remaining)))
            actions[key] = now
            state["version"] = 1
            atomic_json_write(self.rate_path, state)
        return True, 0


@bp.get("")
@login_required
def index():
    items = _objects().objects()
    rows = []
    sidecar = _inventory()
    metadata = sidecar.object_metas()
    for item in reversed(items[-80:]):
        rows.append({**item, "inventory": metadata.get(item["object_id"], {})})
    return render_template("inventory/index.html", inventory_rows=rows)


@bp.get("/lookup")
@login_required
def book_lookup():
    try:
        isbn = normalize_isbn(request.args.get("isbn", ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    allowed, retry_after = _inventory().consume_rate_limit(str(g.user["username"]), "book-metadata")
    if not allowed:
        response = jsonify({
            "ok": False,
            "error": "Metadatenabruf ist auf einen Klick je 5 Sekunden begrenzt.",
            "retry_after": retry_after,
        })
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        response.headers["Cache-Control"] = "no-store"
        return response
    metadata = lookup_book_metadata(isbn)
    if not metadata:
        response = jsonify({"ok": False, "error": "Keine Buchmetadaten gefunden."})
        response.status_code = 404
        response.headers["Cache-Control"] = "no-store"
        return response
    metadata["ok"] = True
    metadata["amazon_search"] = url_for("inventory.amazon_search", q=isbn)
    response = jsonify(metadata)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/find")
@login_required
def find_item():
    identifier = _single_line(request.args.get("identifier", ""), 120)
    nfc_id = _single_line(request.args.get("nfc", ""), 240)
    item = _find_exact(identifier, nfc_id)
    if not item:
        return jsonify({"found": False})
    return jsonify({
        "found": True,
        "object_id": item["object_id"],
        "display_id": item.get("display_id", ""),
        "name": item.get("name", ""),
        "url": url_for("documents.object_detail", object_id=item["object_id"]),
    })


@bp.get("/amazon")
@login_required
def amazon_search():
    query = _single_line(request.args.get("q", ""), 160)
    if not query:
        abort(400)
    allowed, retry_after = _inventory().consume_rate_limit(str(g.user["username"]), "amazon-search")
    if not allowed:
        return (
            render_template("inventory/rate_limit.html", retry_after=retry_after),
            429,
            {"Retry-After": str(retry_after), "Cache-Control": "no-store"},
        )
    # Deliberately redirect the signed-in human to normal Amazon search. The
    # server neither downloads nor parses Amazon HTML and stores no Amazon data.
    response = redirect("https://www.amazon.de/s?k=" + quote_plus(query), code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/books")
@login_required
def create_book():
    actor = str(g.user["username"])
    barcode = _single_line(request.form.get("barcode", ""), 80)
    isbn_value = request.form.get("isbn", "") or isbn_from_barcode(barcode)
    isbn = ""
    if isbn_value:
        try:
            isbn = normalize_isbn(isbn_value)
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("inventory.index"))
    identifier = isbn or barcode
    nfc_id = _single_line(request.form.get("nfc_id", ""), 240)
    duplicate = _find_exact(identifier, nfc_id)
    if duplicate and request.form.get("allow_duplicate") != "1":
        flash(
            f"Bereits vorhanden: #{duplicate.get('display_id', '')} "
            f"{duplicate.get('name', '')}. Kein Duplikat angelegt."
        )
        return redirect(url_for("documents.object_detail", object_id=duplicate["object_id"]))
    try:
        fields = _object_fields(request.form, isbn)
        title = _single_line(request.form.get("title", ""), 300)
        if not title:
            title = f"Buch {isbn or barcode}" if (isbn or barcode) else "Buch"
        tags = {"Buch"}
        tags.update(_single_line(request.form.get("tags", ""), 500).split(","))
        tags = {tag.strip() for tag in tags if tag.strip()}
        if isbn:
            tags.add("ISBN")
        item = _objects().create(
            {
                "name": title,
                "type": "book",
                "status": "active",
                "description": str(request.form.get("description", "") or "").strip()[:8000],
                "identifier": identifier,
                "location": _single_line(request.form.get("location", ""), 300),
                "expires_at": "",
                "tags": ",".join(sorted(tags, key=str.casefold)),
                "fields": _fields_text(fields),
            },
            actor,
        )
        sidecar = _inventory()
        sidecar.record_snapshot(item["object_id"], fields, actor)
        photo_error = ""
        try:
            sidecar.save_photo(item["object_id"], request.files.get("photo"), actor)
        except (OSError, ValueError) as exc:
            photo_error = str(exc)
        flash(
            f"Inventarobjekt #{item.get('display_id', '')} wurde angelegt."
            + (f" Foto nicht gespeichert: {photo_error}" if photo_error else "")
        )
        return redirect(url_for("inventory.index", created=item["object_id"]))
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("inventory.index"))


@bp.post("/<object_id>/photo")
@login_required
def add_photo(object_id: str):
    try:
        item = _objects().object(object_id)
        photo = _inventory().save_photo(
            item["object_id"],
            request.files.get("photo"),
            str(g.user["username"]),
        )
        if photo is None:
            raise ValueError("Kein Foto ausgewählt")
        flash("Inventarfoto wurde gespeichert.")
        return redirect(url_for("inventory.index", created=item["object_id"]))
    except (OSError, ValueError):
        abort(400)


@bp.get("/media/<object_id>/<filename>")
@login_required
def inventory_media(object_id: str, filename: str):
    try:
        _objects().object(object_id)
        path = _inventory().media_path(object_id, filename)
    except ValueError:
        abort(404)
    response = send_file(path, conditional=True)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response
