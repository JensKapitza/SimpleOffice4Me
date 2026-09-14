"""Start the SimpleOffice4Me Flask app inside the Android process."""
from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler

_SERVER = None
_THREAD = None
MAX_SERVER_WORKERS = 6
MAX_PENDING_REQUESTS = 12


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        return


class PooledServer(WSGIServer):
    """Small bounded request pool suited to an embedded mobile WebView."""

    allow_reuse_address = True
    request_queue_size = MAX_PENDING_REQUESTS

    def __init__(self, *args, **kwargs):
        self._request_slots = threading.BoundedSemaphore(MAX_PENDING_REQUESTS)
        self._request_pool = ThreadPoolExecutor(
            max_workers=MAX_SERVER_WORKERS,
            thread_name_prefix="simpleoffice-http",
        )
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        self._request_slots.acquire()
        try:
            future = self._request_pool.submit(self._process_request, request, client_address)
        except Exception:
            self._request_slots.release()
            self.shutdown_request(request)
            raise
        future.add_done_callback(lambda _future: self._request_slots.release())

    def _process_request(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def server_close(self):
        super().server_close()
        self._request_pool.shutdown(wait=False, cancel_futures=True)


def _configure_environment(
    runtime_root: Path,
    error_report_url: str = "",
    account_email: str = "",
    bootstrap_token: str = "",
) -> None:
    os.chdir(runtime_root)
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))

    os.environ["SIMPLEOFFICE_DESKTOP"] = "0"
    os.environ["SIMPLEOFFICE_ANDROID"] = "1"
    os.environ["SIMPLEOFFICE_HOST"] = "127.0.0.1"
    os.environ["SIMPLEOFFICE_PORT"] = "8765"
    os.environ["SIMPLEOFFICE_DOCUMENT_ROOT"] = str(runtime_root / "database" / "documents")
    os.environ["SIMPLEOFFICE_BACKGROUND_INDEX"] = "0"
    os.environ["SIMPLEOFFICE_OSM_INDEX"] = "0"
    os.environ["SIMPLEOFFICE_DATALOGGER"] = "0"
    os.environ["SIMPLEOFFICE_MCP"] = "0"

    email = str(account_email or "").strip().casefold()
    token = str(bootstrap_token or "").strip()
    if len(email) > 320 or any(character.isspace() for character in email):
        raise RuntimeError("Invalid Android account identity")
    if token and len(token) < 32:
        raise RuntimeError("Android bootstrap token is too short")
    if email:
        os.environ["SIMPLEOFFICE_ANDROID_ACCOUNT"] = email
    else:
        os.environ.pop("SIMPLEOFFICE_ANDROID_ACCOUNT", None)
    if token:
        os.environ["SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN"] = token
    else:
        os.environ.pop("SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN", None)

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


def start(
    runtime_root: str,
    error_report_url: str = "",
    account_email: str = "",
    bootstrap_token: str = "",
) -> bool:
    """Import the app and bind the local server before returning.

    Import and bind errors intentionally happen on the calling thread so
    Chaquopy forwards the real Python exception to Android instead of hiding
    it behind a generic backend timeout.  The optional account identity and
    bootstrap token are provided only by the native Android startup activity.
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

    _configure_environment(root, error_report_url, account_email, bootstrap_token)

    try:
        from app import app
        if bootstrap_token:
            from app import android_auth
            app.register_blueprint(android_auth.bp)
    except Exception as exc:
        raise RuntimeError(f"SimpleOffice4Me import failed: {type(exc).__name__}: {exc}") from exc

    app.config["TEMPLATES_AUTO_RELOAD"] = False
    app.config["MAX_CONTENT_LENGTH"] = min(int(app.config["MAX_CONTENT_LENGTH"]), 256 * 1024 * 1024)
    # The native bootstrap runs on every cold app start.  A long cookie lifetime
    # prevents the embedded WebView from ever falling back to the browser login
    # screen while the app remains open; password-enabled launches are still
    # explicitly unlocked by BootstrapActivity before the WebView is shown.
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)

    try:
        _SERVER = make_server(
            "127.0.0.1",
            8765,
            app,
            server_class=PooledServer,
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
