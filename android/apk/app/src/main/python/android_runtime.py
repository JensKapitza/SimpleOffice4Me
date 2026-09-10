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


def _serve(runtime_root: Path) -> None:
    global _SERVER
    os.chdir(runtime_root)
    sys.path.insert(0, str(runtime_root))

    os.environ["PYTHONUTF8"] = "1"
    os.environ["SIMPLEOFFICE_DESKTOP"] = "0"
    os.environ["SIMPLEOFFICE_HOST"] = "127.0.0.1"
    os.environ["SIMPLEOFFICE_PORT"] = "8765"
    os.environ["SIMPLEOFFICE_DOCUMENT_ROOT"] = str(runtime_root / "database" / "documents")
    os.environ["SIMPLEOFFICE_BACKGROUND_INDEX"] = "0"
    os.environ["SIMPLEOFFICE_OSM_INDEX"] = "0"
    os.environ["SIMPLEOFFICE_DATALOGGER"] = "0"
    os.environ["SIMPLEOFFICE_MCP"] = "0"

    from app import app

    app.config["TEMPLATES_AUTO_RELOAD"] = False
    app.config["MAX_CONTENT_LENGTH"] = min(int(app.config["MAX_CONTENT_LENGTH"]), 256 * 1024 * 1024)

    _SERVER = make_server(
        "127.0.0.1",
        8765,
        app,
        server_class=ReuseServer,
        handler_class=QuietRequestHandler,
    )
    _SERVER.serve_forever(poll_interval=0.25)


def start(runtime_root: str) -> bool:
    """Start the local Flask server once and return immediately."""
    global _THREAD
    root = Path(runtime_root).resolve()
    if not (root / "app" / "__init__.py").is_file():
        raise RuntimeError(f"SimpleOffice4Me sources missing under {root}")
    if _THREAD is not None and _THREAD.is_alive():
        return True

    _THREAD = threading.Thread(
        target=_serve,
        args=(root,),
        name="simpleoffice-flask",
        daemon=True,
    )
    _THREAD.start()
    return True
