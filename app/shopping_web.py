"""Mobile-friendly shopping-list UI backed by ShoppingStore."""
from __future__ import annotations

import io
import base64
import re
import uuid
from pathlib import Path
from urllib.parse import quote_plus

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, make_response, redirect, render_template, request, send_file, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

from .auth import login_required
from .federation_qr_image import render_qr_svg
from .shopping_pricing import display_eur, estimated_total, parse_decimal
from .shopping_store import STATUSES, ShoppingStore, normalize_barcode
from .shopping_suggest import suggest_shopping_groups


bp = Blueprint("shopping", __name__, url_prefix="/shopping")

PRODUCT_PHOTO_MAX_BYTES = 8 * 1024 * 1024
PRODUCT_PHOTO_MAX_PIXELS = 24_000_000
PRODUCT_PHOTO_MAX_DIMENSION = 1600
_PRODUCT_PHOTO_ID = re.compile(r"^[0-9a-f]{32}\.jpg$")

STATUS_LABELS = {
    "open": "Offen",
    "taken": "Übernommen",
    "bought": "Gekauft",
    "not_found": "Nicht gefunden",
    "unavailable": "Nicht verfügbar",
    "deferred": "Zurückgestellt",
}
_ITEM_SEARCH_FIELDS = ("name", "brand", "category", "store", "pack_size", "barcode")


def _store() -> ShoppingStore:
    return ShoppingStore(current_app.config["DOCUMENT_ROOT"])


def _actor() -> str:
    return str(g.user["username"])


def _back(list_id: str = ""):
    target = list_id or request.form.get("list_id", "").strip()
    options = {"list_id": target} if target else {}
    query = _item_search_query(request.form.get("q", ""))
    if target and query:
        options["q"] = query
    if target and request.form.get("show_bought") == "1":
        options["show_bought"] = "1"
    return redirect(url_for("shopping.index", **options))


def _item_search_query(value: str) -> str:
    return value[:150].strip()


def _matches_item(row: dict, terms: list[str]) -> bool:
    searchable = " ".join(str(row.get(key) or "") for key in _ITEM_SEARCH_FIELDS).casefold()
    return all(term in searchable for term in terms)


def _search_links(row: dict) -> dict[str, str]:
    query = " ".join(str(row.get(key) or "").strip() for key in ("brand", "name", "pack_size") if str(row.get(key) or "").strip())
    encoded = quote_plus(query)
    return {
        "amazon_search_url": f"https://www.amazon.de/s?k={encoded}",
        "ebay_search_url": f"https://www.ebay.de/sch/i.html?_nkw={encoded}",
    }


def _with_estimated_line_total(row: dict) -> dict:
    result = dict(row)
    try:
        price = parse_decimal(row.get("price", ""))
        quantity = parse_decimal(row.get("quantity", ""))
        result["estimated_line_total"] = display_eur(price * (quantity if quantity is not None else 1)) if price is not None else ""
    except ValueError:
        result["estimated_line_total"] = ""
    return result


def _photo_path(store: ShoppingStore, photo_id: str) -> Path:
    if not _PRODUCT_PHOTO_ID.fullmatch(str(photo_id or "")):
        raise ValueError("invalid shopping photo identifier")
    return store.photo_dir / photo_id


def _save_product_photo(store: ShoppingStore, upload) -> str:
    source = getattr(upload, "stream", upload)
    raw = source.read(PRODUCT_PHOTO_MAX_BYTES + 1)
    if not raw:
        raise ValueError("empty product photo")
    if len(raw) > PRODUCT_PHOTO_MAX_BYTES:
        raise ValueError("product photo too large")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            width, height = int(probe.width), int(probe.height)
            if width < 1 or height < 1 or width * height > PRODUCT_PHOTO_MAX_PIXELS:
                raise ValueError("product photo dimensions are invalid")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as source_image:
            image = ImageOps.exif_transpose(source_image)
            image.thumbnail((PRODUCT_PHOTO_MAX_DIMENSION, PRODUCT_PHOTO_MAX_DIMENSION))
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                image = flattened
            else:
                image = image.convert("RGB")
            store.photo_dir.mkdir(parents=True, exist_ok=True)
            photo_id = f"{uuid.uuid4().hex}.jpg"
            target = _photo_path(store, photo_id)
            image.save(target, format="JPEG", quality=88, optimize=True)
            return photo_id
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("invalid product photo") from exc


@bp.get("/photos/<photo_id>")
@login_required
def product_photo(photo_id: str):
    store = _store()
    try:
        target = _photo_path(store, photo_id)
    except ValueError:
        abort(404)
    if not store.can_read_photo(_actor(), photo_id) or not target.is_file():
        abort(404)
    response = send_file(target, mimetype="image/jpeg", conditional=True, max_age=3600)
    response.headers["Cache-Control"] = "private, max-age=3600"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("/")
@login_required
def index():
    actor = _actor()
    store = _store()
    lists = store.lists(actor)
    selected_id = request.args.get("list_id", "").strip()
    if selected_id and all(row.get("list_id") != selected_id for row in lists):
        selected_id = ""
    if not selected_id and lists:
        selected_id = str(lists[0]["list_id"])
    selected = next((row for row in lists if row.get("list_id") == selected_id), None)
    show_bought = request.args.get("show_bought") == "1"
    items = store.items(actor, list_id=selected_id, include_bought=show_bought) if selected_id else []
    permissions = store.permissions(actor, selected_id) if selected_id else set()
    total = estimated_total(items)
    query = _item_search_query(request.args.get("q", ""))
    terms = query.casefold().split()
    merge_suggestions = suggest_shopping_groups(items) if not terms else []
    if terms:
        items = [row for row in items if _matches_item(row, terms)]
    products = [_search_links(row) | row for row in store.products(actor, limit=8)]
    items = [_search_links(row) | _with_estimated_line_total(row) for row in items]
    return render_template(
        "shopping/index.html",
        lists=lists,
        selected=selected,
        items=items,
        permissions=permissions,
        status_labels=STATUS_LABELS,
        store_groups=store.items_by_store(actor),
        products=products,
        show_bought=show_bought,
        item_query=query,
        merge_suggestions=merge_suggestions,
        estimated_total=display_eur(total["amount"]),
        priced_items=total["priced_items"],
        missing_prices=total["missing_prices"],
        public_qr_active=store.has_public_read_link(selected_id, actor) if selected_id else False,
    )


@bp.post("/lists")
@login_required
def create_list():
    try:
        row = _store().create_list(
            request.form.get("name", ""),
            _actor(),
            store=request.form.get("store", ""),
        )
        flash("Einkaufsliste angelegt.")
        return _back(str(row["list_id"]))
    except ValueError:
        flash("Die Einkaufsliste konnte nicht angelegt werden. Name und Laden prüfen.")
        return _back()


@bp.post("/lists/<list_id>/archive")
@login_required
def archive_list(list_id: str):
    try:
        _store().archive_list(list_id, _actor(), True)
        flash("Einkaufsliste archiviert.")
    except ValueError:
        flash("Die Einkaufsliste konnte nicht archiviert werden.")
    return redirect(url_for("shopping.index"))


@bp.post("/lists/<list_id>/share-qr")
@login_required
def create_share_qr(list_id: str):
    store = _store()
    try:
        share = store.create_public_read_link(list_id, _actor())
    except ValueError:
        flash("QR-Freigabe konnte nicht erstellt werden.")
        return _back(list_id)
    share_url = url_for("shopping.public_share", token=share["token"], _external=True)
    qr = base64.b64encode(render_qr_svg(share_url).encode("utf-8")).decode("ascii")
    response = make_response(render_template(
        "shopping/share_qr.html",
        list_name=next((row["name"] for row in store.lists(_actor(), include_archived=True) if row["list_id"] == list_id), "Einkaufsliste"),
        share_url=share_url,
        qr_data=qr,
    ))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.post("/lists/<list_id>/share-qr/revoke")
@login_required
def revoke_share_qr(list_id: str):
    try:
        _store().revoke_public_read_link(list_id, _actor())
        flash("QR-Freigabelink widerrufen.")
    except ValueError:
        flash("QR-Freigabelink konnte nicht widerrufen werden.")
    return _back(list_id)


@bp.get("/s/<token>")
def public_share(token: str):
    shared = _store().public_read_list(token)
    if shared is None:
        abort(404)
    total = estimated_total(shared["items"])
    shared["items"] = [_with_estimated_line_total(row) for row in shared["items"]]
    response = make_response(render_template(
        "shopping/public_share.html",
        shared=shared,
        estimated_total=display_eur(total["amount"]),
        missing_prices=total["missing_prices"],
    ))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.post("/lists/<list_id>/items")
@login_required
def add_item(list_id: str):
    store = _store()
    actor = _actor()
    values = {
        key: request.form.get(key, "")
        for key in (
            "quantity", "unit", "note", "category", "store", "barcode", "priority",
            "brand", "pack_size", "price", "request_id", "photo_id",
        )
    }
    sync_request = request.headers.get("X-Shopping-Sync", "") == "1"
    requested_photo_id = str(values.get("photo_id", "") or "").strip()
    if requested_photo_id and not store.can_read_photo(actor, requested_photo_id):
        values["photo_id"] = ""
    uploaded = request.files.get("product_photo")
    created_photo_id = ""
    if uploaded is not None and uploaded.filename:
        try:
            created_photo_id = _save_product_photo(store, uploaded)
            values["photo_id"] = created_photo_id
        except ValueError:
            if sync_request:
                return jsonify({"ok": False, "error": "Produktfoto konnte nicht verarbeitet werden."}), 400
            flash("Produktfoto konnte nicht verarbeitet werden. Erlaubt sind gültige Bilder bis 8 MiB.")
            return _back(list_id)
    try:
        item = store.add_item(list_id, request.form.get("name", ""), actor, values)
        if sync_request:
            return jsonify({
                "ok": True,
                "item_id": item["item_id"],
                "redirect": url_for("shopping.index", list_id=list_id),
            })
        flash(f"{item['name']} wurde hinzugefügt.")
    except ValueError:
        if created_photo_id:
            try:
                _photo_path(store, created_photo_id).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        if sync_request:
            return jsonify({
                "ok": False,
                "error": "Artikel konnte nicht hinzugefügt werden. Eingaben und Rechte prüfen.",
            }), 409
        flash("Artikel konnte nicht hinzugefügt werden. Eingaben und Barcode prüfen.")
    return _back(list_id)


@bp.post("/items/<item_id>/quantity")
@login_required
def item_quantity(item_id: str):
    list_id = request.form.get("list_id", "").strip()
    quantity = request.form.get("quantity", "").strip()
    unit = request.form.get("unit", "").strip()
    price = request.form.get("price", "").strip()
    if not quantity:
        flash("Bitte eine Anzahl oder Menge angeben.")
        return _back(list_id)
    try:
        _store().update_item(item_id, _actor(), {"quantity": quantity, "unit": unit, "price": price})
        flash("Menge und Preis aktualisiert.")
    except ValueError:
        flash("Menge oder Preis konnte nicht geändert werden.")
    return _back(list_id)


@bp.post("/products/<product_id>/restock")
@login_required
def product_restock(product_id: str):
    list_id = request.form.get("list_id", "").strip()
    try:
        item = _store().restock_product(
            product_id, list_id, _actor(), request.form.get("quantity", "1").strip() or "1"
        )
        flash(f"{item['name']} wurde in der vorhandenen Listenposition nachgelegt.")
    except ValueError:
        flash("Der Artikel konnte nicht nachgelegt werden. Liste und Menge prüfen.")
    return _back(list_id)


@bp.post("/items/<item_id>/status")
@login_required
def item_status(item_id: str):
    status = request.form.get("status", "").strip()
    if status not in STATUSES:
        flash("Ungültiger Einkaufsstatus.")
        return _back()
    actor = _actor()
    values: dict[str, str] = {"status": status}
    if status == "open":
        values["assigned_to"] = ""
    elif status in {"taken", "bought", "not_found", "unavailable"}:
        values["assigned_to"] = actor
    try:
        _store().update_item(item_id, actor, values)
        flash(f"Status: {STATUS_LABELS[status]}.")
    except ValueError:
        flash("Der Artikel konnte nicht geändert werden.")
    return _back()


@bp.post("/products/<product_id>/favorite")
@login_required
def product_favorite(product_id: str):
    favorite = request.form.get("favorite", "1").strip() == "1"
    try:
        _store().set_product_favorite(product_id, _actor(), favorite)
        flash("Produkt-Favorit aktualisiert.")
    except ValueError:
        flash("Produkt konnte nicht geändert werden.")
    return _back()


@bp.get("/barcode")
@login_required
def barcode_lookup():
    raw = request.args.get("code", "")
    try:
        code = normalize_barcode(raw)
    except ValueError:
        return jsonify({"ok": False, "error": "Ungültiger EAN/UPC/GTIN-Code."}), 400
    if not code:
        return jsonify({"ok": False, "error": "Barcode fehlt."}), 400
    store = _store()
    actor = _actor()
    known = store.find_known_barcode(actor, code)
    payload = {"ok": True, "barcode": code, "known": bool(known)}
    if known:
        item = {
            key: known.get(key, "")
            for key in (
                "name", "quantity", "unit", "category", "store",
                "brand", "pack_size", "price", "photo_id",
            )
        }
        photo_id = str(item.get("photo_id", "") or "")
        if photo_id and store.can_read_photo(actor, photo_id):
            item["photo_url"] = url_for("shopping.product_photo", photo_id=photo_id)
        payload["item"] = item
    response: Response = jsonify(payload)
    response.headers["Cache-Control"] = "private, no-store"
    return response
