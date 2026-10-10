"""Two-pane Resource Commander for local, mail and federation resources."""
from __future__ import annotations

from io import BytesIO
import re
from urllib.parse import urlsplit

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



def _query_text(key: str, default: str = "", *, maximum: int = 4096) -> str:
    value = request.args.get(key, default)
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        abort(400, description=f"Invalid {key}")
    return value



def _json_object() -> dict:
    """Reject arrays/scalars and oversized fields before any resource mutation."""
    if not request.is_json:
        abort(400, description="JSON object required")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="JSON object required")
    return payload


def _json_text(payload: dict, key: str, default: str = "", *, maximum: int = 4096) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        abort(400, description=f"Invalid {key}")
    return value


def _json_boolean(payload: dict, key: str, default: bool = False) -> bool:
    value = payload.get(key, default)
    if type(value) is not bool:
        abort(400, description=f"Invalid {key}")
    return value


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
    entries = [entry.to_dict() for entry in provider.list(_query_text("path"))]
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
        "entry": provider.stat(_query_text("id")).to_dict(),
        "capabilities": provider.capabilities.to_dict(),
    })


@bp.get("/api/search")
def search_entries():
    _api_access()
    provider = _provider()
    _require(provider, "search")
    entries = [
        entry.to_dict()
        for entry in provider.search(_query_text("q", maximum=1000), _query_text("path"))
    ]
    return jsonify({"entries": entries, "capabilities": provider.capabilities.to_dict()})


@bp.get("/api/download")
def download_entry():
    _api_access()
    provider = _provider()
    _require(provider, "read")
    entry = provider.stat(_query_text("id"))
    stream = provider.open(entry.resource_id)
    return send_file(stream, as_attachment=True, download_name=entry.name, mimetype=entry.mime_type)


@bp.get("/api/range")
def range_entry():
    """Return at most 1 MiB from a file without forcing a complete remote download."""
    _api_access()
    provider = _provider()
    _require(provider, "read")
    resource_id = _query_text("id")
    offset = _bounded_int(
        _query_text("offset", "0", maximum=24), minimum=0, maximum=2**63 - 1, label="offset",
    )
    length = _bounded_int(
        _query_text("length", str(MAX_RANGE_BYTES), maximum=24),
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
        _query_text("path"),
        request.stream,
        name=_query_text("name", "upload.bin", maximum=255),
    )
    return jsonify({"entry": entry.to_dict()}), 201


@bp.post("/api/web-link")
def create_web_link():
    """Save a website as a local .url shortcut; never fetch the supplied URL."""
    _api_access()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ProviderError("Ungültige Eingabe")
    url = payload.get("url", "")
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise ProviderError("Ungültige URL")
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ProviderError("Nur HTTP(S)-Webadressen ohne Zugangsdaten sind erlaubt")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ProviderError("Ungültiger URL-Port") from exc
    title = payload.get("name") or parsed.hostname
    if not isinstance(title, str):
        raise ProviderError("Ungültiger Linkname")
    name = re.sub(r"[^\w .()-]", "_", title, flags=re.UNICODE).strip(" .")
    if not name or len(name) > 100 or name in {".", ".."}:
        raise ProviderError("Ungültiger Linkname")
    if name.lower().endswith(".url"):
        name = name[:-4].rstrip(" .")
    if not name:
        raise ProviderError("Ungültiger Linkname")
    provider = _registry().get("self")
    _require(provider, "write")
    data = ("[InternetShortcut]\r\nURL=" + url + "\r\n").encode("utf-8")
    entry = provider.upload(str(payload.get("path", "")), BytesIO(data), name=name + ".url")
    return jsonify({"entry": entry.to_dict()}), 201


@bp.post("/api/mkdir")
def mkdir_entry():
    _api_access()
    payload = _json_object()
    provider = _registry().get(_json_text(payload, "provider", "self", maximum=120))
    _require(provider, "write")
    _require(provider, "folders")
    entry = provider.mkdir(_json_text(payload, "path"), _json_text(payload, "name"))
    return jsonify({"entry": entry.to_dict()}), 201


@bp.post("/api/delete")
def delete_entry():
    _api_access()
    payload = _json_object()
    provider = _registry().get(_json_text(payload, "provider", "self", maximum=120))
    _require(provider, "delete")
    provider.delete(_json_text(payload, "id"))
    return jsonify({"ok": True})


@bp.post("/api/move")
def move_entry():
    _api_access()
    payload = _json_object()
    provider = _registry().get(_json_text(payload, "provider", "self", maximum=120))
    _require(provider, "move")
    entry = provider.move(
        _json_text(payload, "id"),
        _json_text(payload, "path"),
        name=_json_text(payload, "name") or None,
    )
    return jsonify({"entry": entry.to_dict()})


@bp.post("/api/copy")
def copy_entry():
    _api_access()
    payload = _json_object()
    source = _registry().get(
        _json_text(payload, "source_provider", "self", maximum=120),
        smart=_json_boolean(payload, "source_smart"),
    )
    target = _registry().get(_json_text(payload, "target_provider", "self", maximum=120))
    _require(source, "read")
    _require(source, "copy")
    _require(target, "write")
    entry = source.stat(_json_text(payload, "id"))
    if entry.kind != "file":
        raise ProviderError("Ordnerkopien werden nur elementweise ausgeführt")
    with source.open(entry.resource_id) as stream:
        created = target.upload(
            _json_text(payload, "target_path"),
            stream,
            name=_json_text(payload, "name", entry.name),
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