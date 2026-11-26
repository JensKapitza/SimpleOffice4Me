"""Explicit staging gate for the local two-destination hand shortcut proof."""
import os

from flask import current_app, g, render_template

from . import kids_hand_assets
from .kids_learning import _hand_game_asset_directory


def enabled() -> bool:
    configured = current_app.config.get("HAND_NAV_STAGING")
    return configured is True or (
        configured is None and os.environ.get("SIMPLEOFFICE_HAND_NAV_STAGING") == "1"
    )


def response():
    # The same lookup feeds media readiness and its authenticated asset GETs.
    # The stage flag alone never grants camera or WASM permissions.
    active = enabled()
    directory = _hand_game_asset_directory() if active else ""
    base_ready = active and kids_hand_assets.ready(directory)
    nav_ready = base_ready and kids_hand_assets.simd_ready(directory)
    g.hand_nav_camera_ready = nav_ready
    page = current_app.make_response(render_template(
        "documents/access_navigation_proof.html",
        hand_nav_ready=nav_ready,
        hand_nav_base_ready=base_ready,
        hand_nav_enabled=active,
    ))
    page.headers["Cache-Control"] = "private, no-store"
    return page
