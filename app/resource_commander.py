"""Two-pane Resource Commander for local, mail and federation resources."""
from __future__ import annotations

from flask import Blueprint, Response, abort, current_app, g, jsonify, render_template, request, send_file

from .auth import login_required
from .resource_commander_access import api_access_authorized
from .resource_provider import ProviderError
from .resource_registry import ResourceRegistry

bp = Blueprint("resource_commander", __name__, url_prefix="/resource-commander")
MAX_RANGE_BYTES = 1024 * 1024


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


def _api_access() -> None:
    if api_access_authorized():
        return
    abort(401)


def _provider():
    provider_id = request.values.get("provider", "self")
    smart = request.values.get("smart", "0") in {"1", "true", "yes", "on"}
    return _registry().get(provider_id, smart=smart)


def _require(provider, capability: str) -> None:
    if not bool(getattr(provider.capabilities, capability, False)):
        raise ProviderError(f"Provider erlaubt die Aktion '{capability}' nicht")


def _bounded_int(value: str, *, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(str(value or "0"))
    except (TypeError, ValueError) as exc:
        raise ProviderError(f"Ungültiger Wert für {label}") from exc
    if parsed < minimum:
        raise ProviderError(f"{label} darf nicht kleiner als {minimum} sein")
    return min(parsed, maximum)


@bp.get("")
@login_required
def page():
    return render_template("resource_commander.html")


@bp.get("/api/providers")
def providers():
    _api_access()
    return jsonify({"providers": _registry().descriptors()})


@bp.get("/api/list")
def list_entries():
    _api_access()
    provider = _provider()
    entries = [entry.to_dict() for entry in provider.list(request.args.get("path", ""))]
    return jsonify({
        "provider": provider.provider_id,
        "capabilities": provider.capabilities.to_dict(),
        "entries": entries,
    })


@bp.get("/api/stat")
def stat_entry():
    _api_access()
    provider = _provider()
    _require(provider, "metadata")
    return jsonify({
        "entry": provider.stat(request.args.get("id", "")).to_dict(),
        "capabilities": provider.capabilities.to_dict(),
    })


@bp.get("/api/search")
def search_entries():
    _api_access()
    provider = _provider()
    _require(provider, "search")
    entries = [
        entry.to_dict()
        for entry in provider.search(request.args.get("q", ""), request.args.get("path", ""))
    ]
    return jsonify({"entries": entries, "capabilities": provider.capabilities.to_dict()})


@bp.get("/api/download")
def download_entry():
    _api_access()
    provider = _provider()
    _require(provider, "read")
    entry = provider.stat(request.args.get("id", ""))
    stream = provider.open(entry.resource_id)
    return send_file(stream, as_attachment=True, download_name=entry.name, mimetype=entry.mime_type)


@bp.get("/api/range")
def range_entry():
    """Return at most 1 MiB from a file without forcing a complete remote download."""
    _api_access()
    provider = _provider()
    _require(provider, "read")
    resource_id = request.args.get("id", "")
    offset = _bounded_int(
        request.args.get("offset", "0"), minimum=0, maximum=2**63 - 1, label="offset",
    )
    length = _bounded_int(
        request.args.get("length", str(MAX_RANGE_BYTES)),
        minimum=0,
        maximum=MAX_RANGE_BYTES,
        label="length",
    )
    native = getattr(provider, "read_range", None)
    if callable(native):
        data = native(resource_id, offset, length)
    else:
        with provider.open(resource_id) as stream:
            try:
                stream.seek(offset)
            except (AttributeError, OSError):
                remaining = offset
                while remaining:
                    block = stream.read(min(remaining, MAX_RANGE_BYTES))
                    if not block:
                        break
                    remaining -= len(block)
            data = stream.read(length)
    response = Response(bytes(data), mimetype="application/octet-stream")
    response.headers["Content-Length"] = str(len(data))
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.post("/api/upload")
def upload_entry():
    _api_access()
    provider = _provider()
    _require(provider, "write")
    entry = provider.upload(
        request.args.get("path", ""),
        request.stream,
        name=request.args.get("name", "upload.bin"),
    )
    return jsonify({"entry": entry.to_dict()}), 201


@bp.post("/api/mkdir")
def mkdir_entry():
    _api_access()
    payload = request.get_json(silent=True) or {}
    provider = _registry().get(payload.get("provider", "self"))
    _require(provider, "write")
    _require(provider, "folders")
    entry = provider.mkdir(str(payload.get("path", "")), str(payload.get("name", "")))
    return jsonify({"entry": entry.to_dict()}), 201


@bp.post("/api/delete")
def delete_entry():
    _api_access()
    payload = request.get_json(silent=True) or {}
    provider = _registry().get(payload.get("provider", "self"))
    _require(provider, "delete")
    provider.delete(str(payload.get("id", "")))
    return jsonify({"ok": True})


@bp.post("/api/move")
def move_entry():
    _api_access()
    payload = request.get_json(silent=True) or {}
    provider = _registry().get(payload.get("provider", "self"))
    _require(provider, "move")
    entry = provider.move(
        str(payload.get("id", "")),
        str(payload.get("path", "")),
        name=str(payload.get("name") or "") or None,
    )
    return jsonify({"entry": entry.to_dict()})


@bp.post("/api/copy")
def copy_entry():
    _api_access()
    payload = request.get_json(silent=True) or {}
    source = _registry().get(
        str(payload.get("source_provider", "self")),
        smart=bool(payload.get("source_smart")),
    )
    target = _registry().get(str(payload.get("target_provider", "self")))
    _require(source, "read")
    _require(source, "copy")
    _require(target, "write")
    entry = source.stat(str(payload.get("id", "")))
    if entry.kind != "file":
        raise ProviderError("Ordnerkopien werden nur elementweise ausgeführt")
    with source.open(entry.resource_id) as stream:
        created = target.upload(
            str(payload.get("target_path", "")),
            stream,
            name=str(payload.get("name") or entry.name),
            metadata=entry.metadata,
        )
    return jsonify({"entry": created.to_dict()})


@bp.errorhandler(ProviderError)
def provider_error(error):
    return jsonify({"error": str(error)}), 400


def init_app(app) -> None:
    app.register_blueprint(bp)
    from .resource_compare_routes import bp as compare_bp
    app.register_blueprint(compare_bp)