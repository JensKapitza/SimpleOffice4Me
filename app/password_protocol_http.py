"""Experimental compatibility surfaces for existing password-manager clients.

Only protocol pieces that we can answer truthfully are exposed here. Full login
and vault sync remain disabled until protocol-native cryptography and round-trip
client tests are implemented. This prevents accidental claims of compatibility.
"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from .password_protocol_adapters import adapter_capabilities

bp = Blueprint("password_protocol_http", __name__)

BITWARDEN_PBKDF2_ITERATIONS = 600_000


def _enabled(name: str) -> bool:
    key = f"SIMPLEOFFICE_PASSWORD_PROTOCOL_{name.upper().replace('-', '_')}"
    return os.environ.get(key, "0").strip().casefold() in {"1", "true", "yes", "on"}


@bp.get("/password-protocols/v1/capabilities")
def password_protocol_capabilities():
    return jsonify(adapter_capabilities())


@bp.get("/api/config")
def bitwarden_config():
    if not _enabled("bitwarden"):
        return jsonify({"error": "bitwarden adapter disabled"}), 404
    return jsonify({
        "version": "0.1.0-simpleoffice-experimental",
        "server": {"name": "SimpleOffice4Me", "url": request.url_root.rstrip("/")},
        "environment": {
            "api": request.url_root.rstrip("/") + "/api",
            "identity": request.url_root.rstrip("/") + "/identity",
            "notifications": request.url_root.rstrip("/") + "/notifications",
            "webVault": request.url_root.rstrip("/"),
        },
        "featureStates": {},
        "object": "config",
        "simpleOfficeCompatibility": adapter_capabilities("bitwarden"),
    })


@bp.post("/identity/accounts/prelogin")
def bitwarden_prelogin():
    if not _enabled("bitwarden"):
        return jsonify({"error": "bitwarden adapter disabled"}), 404
    # Current Bitwarden clients require at least 600k PBKDF2 iterations.  The
    # value is returned identically for known/unknown users to avoid account
    # enumeration until protocol-native identity storage exists.
    return jsonify({
        "kdf": 0,
        "kdfIterations": BITWARDEN_PBKDF2_ITERATIONS,
        "kdfMemory": None,
        "kdfParallelism": None,
    })


@bp.post("/identity/connect/token")
def bitwarden_token_not_yet_enabled():
    if not _enabled("bitwarden"):
        return jsonify({"error": "bitwarden adapter disabled"}), 404
    return jsonify({
        "error": "unsupported_grant_type",
        "error_description": "SimpleOffice Bitwarden login/sync is experimental and not enabled yet.",
    }), 501


@bp.get("/api/sync")
def bitwarden_sync_not_yet_enabled():
    if not _enabled("bitwarden"):
        return jsonify({"error": "bitwarden adapter disabled"}), 404
    return jsonify({
        "error": "not_implemented",
        "message": "Protocol-native encrypted vault sync is not enabled yet.",
    }), 501


@bp.get("/server/info/")
@bp.get("/server/info")
def psono_info():
    if not _enabled("psono"):
        return jsonify({"error": "psono adapter disabled"}), 404
    # Psono's first login call discovers/authenticates the server. We expose a
    # minimal, explicit experimental descriptor and keep login disabled until
    # the session/public-key exchange has been implemented and fixture-tested.
    return jsonify({
        "title": "SimpleOffice4Me",
        "version": "0.1.0-simpleoffice-experimental",
        "server_signature": "",
        "allow_registration": False,
        "simpleoffice_compatibility": adapter_capabilities("psono"),
    })


@bp.post("/server/authentication/login/")
def psono_login_not_yet_enabled():
    if not _enabled("psono"):
        return jsonify({"error": "psono adapter disabled"}), 404
    return jsonify({
        "error": "not_implemented",
        "message": "Psono session/public-key authentication is not enabled yet.",
    }), 501


def init_app(app) -> None:
    app.register_blueprint(bp)
