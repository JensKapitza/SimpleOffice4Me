"""Start the SimpleOffice4Me Flask app inside the Android process."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler

_SERVER = None
_THREAD = None


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        return


class ReuseServer(WSGIServer):
    allow_reuse_address = True


def _configure_environment(runtime_root: Path, error_report_url: str = "") -> None:
    os.chdir(runtime_root)
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))

    os.environ["SIMPLEOFFICE_DESKTOP"] = "0"
    os.environ["SIMPLEOFFICE_HOST"] = "127.0.0.1"
    os.environ["SIMPLEOFFICE_PORT"] = "8765"
    os.environ["SIMPLEOFFICE_DOCUMENT_ROOT"] = str(runtime_root / "database" / "documents")
    os.environ["SIMPLEOFFICE_BACKGROUND_INDEX"] = "0"
    os.environ["SIMPLEOFFICE_OSM_INDEX"] = "0"
    os.environ["SIMPLEOFFICE_DATALOGGER"] = "0"
    os.environ["SIMPLEOFFICE_MCP"] = "0"

    report_url = str(error_report_url or "").strip()
    if report_url and not report_url.startswith("https://"):
        raise RuntimeError("SimpleOffice error report URL must use HTTPS")
    if report_url:
        os.environ["SIMPLEOFFICE_ERROR_REPORT_URL"] = report_url
    else:
        os.environ.pop("SIMPLEOFFICE_ERROR_REPORT_URL", None)


def _serve() -> None:
    assert _SERVER is not None
    _SERVER.serve_forever(poll_interval=0.25)


def start(runtime_root: str, error_report_url: str = "") -> bool:
    """Import the app and bind the local server before returning.

    Import and bind errors intentionally happen on the calling thread so
    Chaquopy forwards the real Python exception to Android instead of hiding
    it behind a generic backend timeout.
    """
    global _SERVER, _THREAD

    root = Path(runtime_root).resolve()
    if not (root / "app" / "__init__.py").is_file():
        raise RuntimeError(f"SimpleOffice4Me sources missing under {root}")
    if not (root / "simpleoffice_version.py").is_file():
        raise RuntimeError(f"simpleoffice_version.py missing under {root}")
    if not (root / "tools" / "launcher.py").is_file():
        raise RuntimeError(f"tools/launcher.py missing under {root}")

    if _THREAD is not None and _THREAD.is_alive() and _SERVER is not None:
        return True

    _configure_environment(root, error_report_url)

    try:
        from app import app
    except Exception as exc:
        raise RuntimeError(f"SimpleOffice4Me import failed: {type(exc).__name__}: {exc}") from exc

    app.config["TEMPLATES_AUTO_RELOAD"] = False
    app.config["MAX_CONTENT_LENGTH"] = min(int(app.config["MAX_CONTENT_LENGTH"]), 256 * 1024 * 1024)

    try:
        _SERVER = make_server(
            "127.0.0.1",
            8765,
            app,
            server_class=ReuseServer,
            handler_class=QuietRequestHandler,
        )
    except Exception as exc:
        raise RuntimeError(f"Local backend bind failed: {type(exc).__name__}: {exc}") from exc

    _THREAD = threading.Thread(
        target=_serve,
        name="simpleoffice-flask",
        daemon=True,
    )
    _THREAD.start()
    return True
