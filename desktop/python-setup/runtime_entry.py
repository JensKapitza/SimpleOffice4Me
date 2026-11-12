"""Entry point for the Python backend embedded in the Electron desktop app."""
from __future__ import annotations

import os
from pathlib import Path


def _desktop_server(document_root: str | None) -> dict:
    """Effective server settings; the embedded service stays on loopback."""
    from simpleoffice_server_config import strict_server_settings

    try:
        return strict_server_settings(document_root)
    except ValueError as exc:
        raise RuntimeError(f"Invalid server setting: {exc}") from exc


def _persistent_runtime(app) -> Path:
    configured = os.environ.get("SIMPLEOFFICE_DATA_DIR", "").strip()
    if not configured:
        raise RuntimeError("SIMPLEOFFICE_DATA_DIR is required for the desktop runtime")
    root = Path(configured).expanduser().resolve()
    database = root / "database"
    instance = Path(os.environ.get("SIMPLEOFFICE_INSTANCE_DIR", str(root / "instance"))).expanduser().resolve()
    files = database / "files"
    database.mkdir(parents=True, exist_ok=True)
    files.mkdir(parents=True, exist_ok=True)
    instance.mkdir(parents=True, exist_ok=True)

    # app/__init__.py must still be able to resolve packaged templates/static
    # from the PyInstaller image, but mutable state belongs in the user profile.
    app.instance_path = str(instance)
    app.config["DATABASE_FILEDIR"] = str(files)
    app.config["DATABASE"] = str(database / "my.sqlite")
    app.config["DATABASE_TRANSLATION"] = str(database / "translation.sqlite")

    document_root = os.environ.get("SIMPLEOFFICE_DOCUMENT_ROOT", "").strip()
    if not document_root:
        document_root = str(root / "documents")
    documents = Path(document_root).expanduser().resolve()
    documents.mkdir(parents=True, exist_ok=True)
    app.config["DOCUMENT_ROOT"] = str(documents)

    from app.secret_key import load_or_create_secret_key
    app.config["SECRET_KEY"] = load_or_create_secret_key(instance / "session-secret")
    return root


def main() -> None:
    # Keep the embedded service local even if a hostile parent environment tries
    # to widen the listener. Electron communicates with this process via loopback.
    os.environ["SIMPLEOFFICE_HOST"] = "127.0.0.1"
    os.environ.setdefault("SIMPLEOFFICE_BACKGROUND_INDEX", "0")
    os.environ.setdefault("SIMPLEOFFICE_OSM_INDEX", "0")
    os.environ.setdefault("SIMPLEOFFICE_DATALOGGER", "0")

    from app import app
    _persistent_runtime(app)

    # app imports initialize its bundled scratch database once. Re-run the
    # additive authentication bootstrap after switching to persistent paths.
    from app import db
    with app.app_context():
        db.ensure_auth_database()

    server = _desktop_server(app.config["DOCUMENT_ROOT"])
    from waitress import serve
    backend_port = int(server["port"])
    app.config["LAN_PROXY_BACKEND_HOST"] = "127.0.0.1"
    app.config["LAN_PROXY_BACKEND_PORT"] = backend_port
    from app.lan_proxy import configure_lan_proxy, lan_proxy_runtime
    proxy_status = configure_lan_proxy(app, app.config["DOCUMENT_ROOT"], "127.0.0.1", backend_port)
    if proxy_status["state"] == "error":
        print(f"Desktop LAN proxy could not start: {proxy_status['message']}", flush=True)
    try:
        serve(
            app,
            host="127.0.0.1",
            port=backend_port,
            threads=max(2, min(int(server["wsgi_threads"]), 16)),
            channel_timeout=120,
            max_request_body_size=int(app.config["MAX_CONTENT_LENGTH"]),
            expose_tracebacks=False,
            ident="SimpleOffice4Me Desktop",
        )
    finally:
        lan_proxy_runtime.stop(state="stopped", message="Desktop backend stopped")


if __name__ == "__main__":
    main()
