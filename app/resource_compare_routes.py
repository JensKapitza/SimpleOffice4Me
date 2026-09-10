"""HTTP API for staged Resource Commander comparisons."""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, current_app, g, jsonify, request

from .resource_commander_access import api_access_authorized
from .resource_compare import compare_directories, compare_files
from .resource_completeness import verify_complete
from .resource_provider import ProviderError
from .resource_registry import ResourceRegistry

bp = Blueprint("resource_compare", __name__, url_prefix="/resource-commander/api")


def _actor() -> str:
    user = getattr(g, "user", None)
    if user is not None:
        for key in ("username", "email", "id"):
            try:
                value = user[key]
            except (KeyError, TypeError):
                value = None
            if value is not None and str(value).strip():
                return str(value)
    return "federation"


def _secret() -> bytes:
    secret = current_app.config["SECRET_KEY"]
    return secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)


def _registry() -> ResourceRegistry:
    return ResourceRegistry(current_app.config["DOCUMENT_ROOT"], _secret(), _actor())


def _access() -> None:
    if api_access_authorized():
        return
    raise ProviderError("Nicht autorisiert")


def _providers(payload: dict):
    registry = _registry()
    left = registry.get(str(payload.get("left_provider", "self")), smart=bool(payload.get("left_smart")))
    right = registry.get(str(payload.get("right_provider", "self")), smart=bool(payload.get("right_smart")))
    return left, right


@bp.post("/compare-file")
def compare_file():
    _access()
    payload = request.get_json(silent=True) or {}
    left, right = _providers(payload)
    result = compare_files(left, str(payload.get("left_id", "")), right,
                           str(payload.get("right_id", "")),
                           mode=str(payload.get("mode", "metadata")))
    return jsonify(asdict(result))


@bp.post("/compare-directory")
def compare_directory():
    _access()
    payload = request.get_json(silent=True) or {}
    left, right = _providers(payload)
    result = compare_directories(left, str(payload.get("left_path", "")), right,
                                 str(payload.get("right_path", "")),
                                 mode=str(payload.get("mode", "metadata")))
    return jsonify(result)


@bp.post("/completeness")
def completeness():
    """Recursively ensure every source file is present at target; target extras are allowed."""
    _access()
    payload = request.get_json(silent=True) or {}
    left, right = _providers(payload)
    result = verify_complete(left, str(payload.get("left_path", "")), right,
                             str(payload.get("right_path", "")))
    return jsonify(result)


@bp.errorhandler(ProviderError)
def provider_error(error):
    return jsonify({"error": str(error)}), 400