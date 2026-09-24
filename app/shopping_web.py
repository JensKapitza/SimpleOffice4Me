"""Mobile-friendly shopping-list UI backed by ShoppingStore."""
from __future__ import annotations

from flask import Blueprint, Response, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .auth import login_required
from .shopping_store import STATUSES, ShoppingStore, normalize_barcode


bp = Blueprint("shopping", __name__, url_prefix="/shopping")

STATUS_LABELS = {
    "open": "Offen",
    "taken": "Übernommen",
    "bought": "Gekauft",
    "not_found": "Nicht gefunden",
    "unavailable": "Nicht verfügbar",
    "deferred": "Zurückgestellt",
}


def _store() -> ShoppingStore:
    return ShoppingStore(current_app.config["DOCUMENT_ROOT"])


def _actor() -> str:
    return str(g.user["username"])


def _back(list_id: str = ""):
    target = list_id or request.form.get("list_id", "").strip()
    return redirect(url_for("shopping.index", list_id=target) if target else url_for("shopping.index"))


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
    return render_template(
        "shopping/index.html",
        lists=lists,
        selected=selected,
        items=items,
        permissions=permissions,
        status_labels=STATUS_LABELS,
        store_groups=store.items_by_store(actor),
        products=store.products(actor, limit=8),
        show_bought=show_bought,
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


@bp.post("/lists/<list_id>/items")
@login_required
def add_item(list_id: str):
    values = {
        key: request.form.get(key, "")
        for key in (
            "quantity", "unit", "note", "category", "store", "barcode", "priority",
            "brand", "pack_size", "price", "request_id",
        )
    }
    try:
        item = _store().add_item(list_id, request.form.get("name", ""), _actor(), values)
        flash(f"{item['name']} wurde hinzugefügt.")
    except ValueError:
        flash("Artikel konnte nicht hinzugefügt werden. Eingaben und Barcode prüfen.")
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
    known = _store().find_known_barcode(_actor(), code)
    payload = {"ok": True, "barcode": code, "known": bool(known)}
    if known:
        payload["item"] = {
            key: known.get(key, "")
            for key in (
                "name", "quantity", "unit", "category", "store",
                "brand", "pack_size", "price",
            )
        }
    response: Response = jsonify(payload)
    response.headers["Cache-Control"] = "private, no-store"
    return response
