"""Extra ContextFlow routes (split from web.py for the file-size gate).

Registers additional endpoints on the shared ``contextflow`` blueprint;
imported for its side effect by ``app/__init__.py`` after ``.web``.
"""

from __future__ import annotations

from urllib.parse import quote

from flask import abort, redirect, request

from ..auth import login_required
from ..contact_store import ContactStore
from ..object_store import ObjectStore
from .contracts import INVOICE_EDITOR_URL
from .position_hints import find_catalog_duplicate, parse_article_request
from .web import _actor, _gate, _root, bp


@bp.post("/position-article")
@login_required
def position_article():
    """Create one invoice-enabled catalog article from a repeated position."""
    _gate()
    actor = _actor()
    form = request.form
    contact_id = str(form.get("contact_id", "")).strip()
    draft_id = str(form.get("invoice_id", "")).strip()
    try:
        fields = parse_article_request(
            {key: form.get(key, "") for key in ("description", "unit", "net_price", "vat_rate")}
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    try:
        contact = ContactStore(_root()).get(contact_id, actor)
        if not ContactStore(_root()).can_manage_contact(contact, actor):
            raise PermissionError("Artikelanlage braucht Kontakt-Verwaltungsrecht.")
    except (PermissionError, ValueError, KeyError):
        abort(403)
    duplicate = find_catalog_duplicate(_root(), fields["description"])
    if duplicate is not None:
        abort(409, description=f"Artikel existiert bereits (#{duplicate}).")
    # Note: catalog articles carry no unit yet; invoice_unit is stored in
    # fields for future catalog-unit support. The editor still picks the
    # unit per line (default Stück).
    ObjectStore(_root()).create(
        {
            "name": fields["description"],
            "type": "object",
            "description": fields["description"],
            "fields": {"invoice_unit": fields["unit"]},
            "use_in_invoice": True,
            "invoice_description": fields["description"],
            "net_price": fields["net_price"],
            "vat_rate": fields["vat_rate"],
        },
        actor,
    )
    back = INVOICE_EDITOR_URL.format(contact_id=quote(contact_id, safe=""))
    if draft_id:
        back += f"?invoice_id={quote(draft_id, safe='')}"
    return redirect(back)
