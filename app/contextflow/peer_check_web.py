"""Friend catalog check routes (split from web.py for the file-size gate).

Registers additional endpoints on the shared ``contextflow`` blueprint;
imported for its side effect by ``app/__init__.py`` after ``.web``.
Both routes are read-only, local-only, and click-triggered: they query
previously synchronized per-peer indexes, never the network.
"""
from __future__ import annotations

from flask import request

from ..auth import login_required
from .peer_inventory import peer_inventory_availability
from .peer_stock import peer_availability
from .web import _actor, _gate, _root, bp


@bp.post("/peer-stock-check")
@login_required
def peer_stock_check():
    """Ask opt-in friends' shared document catalogs for one identifier (JSON).

    Read-only and local: only friends (trust edge plus enabled peer
    record plus documents-receive opt-in) are consulted, no network
    is used, no content is stored or forwarded. Results are document
    hints, not inventory ownership proof. Friends without a
    valid index are listed as not_synced/error, never as "no hit".
    """
    _gate()
    actor = _actor()
    try:
        result = peer_availability(
            root=_root(), actor=actor, identifier=request.form.get("identifier", "")
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    return {"ok": True, **result}, 200


@bp.post("/peer-inventory-check")
@login_required
def peer_inventory_check():
    """Ask opt-in friends' synced inventory indexes for one identifier (JSON).

    Read-only and local: only friends (trust edge plus enabled peer
    record plus explicit inventory-receive opt-in, default deny) are
    consulted, no network is used, no content is stored or forwarded.
    Only exact ISBN/GTIN matches count; friends without a valid,
    error-free index are listed as not_synced/error, never as "no hit".
    """
    _gate()
    actor = _actor()
    try:
        result = peer_inventory_availability(
            root=_root(), actor=actor, identifier=request.form.get("identifier", "")
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    return {"ok": True, **result}, 200
