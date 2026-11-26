"""Explicit staging gate for the local two-destination hand shortcut proof."""
import os

from flask import current_app, g, render_template

from . import kids_hand_assets


def enabled() -> bool:
    configured = current_app.config.get("HAND_NAV_STAGING")
    return configured is True or (
        configured is None and os.environ.get("SIMPLEOFFICE_HAND_NAV_STAGING") == "1"
    )


def _asset_directory() -> str:
    return str(
        current_app.config.get("KIDS_HAND_GAME_ASSET_DIR")
        or os.environ.get("SIMPLEOFFICE_KIDS_HAND_GAME_ASSET_DIR", "")
    )


def ready() -> bool:
    return enabled() and kids_hand_assets.ready(_asset_directory())


def response():
    # Gaze is not enabled here; only the staged hand-only surface gets camera permission.
    g.hand_nav_camera_ready = ready()
    page = current_app.make_response(render_template(
        "documents/access_navigation_proof.html",
        hand_nav_ready=g.hand_nav_camera_ready,
        hand_nav_enabled=enabled(),
        hand_nav_simd=g.hand_nav_camera_ready and kids_hand_assets.simd_ready(_asset_directory()),
    ))
    page.headers["Cache-Control"] = "private, no-store"
    return page
