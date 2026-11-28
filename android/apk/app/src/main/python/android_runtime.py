"""Start the SimpleOffice4Me Flask app inside the Android process."""
from __future__ import annotations

import importlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler

_SERVER = None
_THREAD = None
_PYTHON_SYMBOL_LIBRARY = None
MAX_SERVER_WORKERS = 6
MAX_PENDING_REQUESTS = 12
# Logcat marker for the #811 runtime smoke gate. Chaquopy forwards Python
# stdout to logcat, so an emulator/device run can grep for this line to prove
# the real cryptography._rust import succeeded inside the packaged app.
RUST_SMOKE_MARKER = "SIMPLEOFFICE_ANDROID_RUST_IMPORT_OK"


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


def _configure_lan_addresses(lan_addresses: str = "") -> None:
    values = []
    for item in str(lan_addresses or "").split(","):
        value = item.strip()
        if value and value not in values:
            values.append(value)
    from app import federation_network
    federation_network.set_lan_addresses(",".join(values))


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


def _promote_android_python_symbols() -> None:
    """Expose the embedded CPython ABI symbols to native extension modules."""
    global _PYTHON_SYMBOL_LIBRARY
    if _PYTHON_SYMBOL_LIBRARY is not None:
        return

    import ctypes

    soname = f"libpython{sys.version_info.major}.{sys.version_info.minor}.so"
    try:
        _PYTHON_SYMBOL_LIBRARY = ctypes.CDLL(
            soname,
            mode=ctypes.RTLD_GLOBAL | os.RTLD_NOW,
        )
    except OSError as exc:
        raise RuntimeError(
            "Android Python symbols could not be exposed to native extensions"
        ) from exc


def _verify_android_cryptography_extension() -> None:
    """Load cryptography's Rust module before application startup imports it indirectly."""
    importlib.import_module("cryptography.hazmat.bindings._rust")
    print(RUST_SMOKE_MARKER, flush=True)


def _android_speech_class():
    from java import jclass

    return jclass("de.simpleoffice4me.android.AndroidOfflineSpeech")


def _render_android_speech(
    text: str, language: str, output: Path, voice: str | None = None,
    rate: float = 1.0, pitch: float = 1.0,
) -> bool:
    return bool(_android_speech_class().render(
        text, language.replace("_", "-"), str(output), voice or "", float(rate), float(pitch),
    ))


def _android_speech_voices() -> list[dict[str, str]]:
    try:
        value = json.loads(str(_android_speech_class().voicesJson()))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return []
    if not isinstance(value, list):
        return []
    return [{name: row[name] for name in ("id", "language", "label")}
            for row in value[:96] if isinstance(row, dict)
            and all(isinstance(row.get(name), str) for name in ("id", "language", "label"))]


def _android_speech_status() -> str:
    try:
        state = str(_android_speech_class().status())
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return "unavailable"
    return state if state in {"loading", "ready", "unavailable"} else "unavailable"


def _refresh_android_speech() -> bool:
    return bool(_android_speech_class().refresh())


def _android_sms_import_class():
    from java import jclass

    return jclass("de.simpleoffice4me.android.AndroidSmsDeviceImport")


def _read_android_sms(lease: str, first: int, last: int):
    # The provider lease stays in the same APK process. Message bodies never
    # appear in a WebView bridge, browser request, or native HTTP response.
    try:
        raw = str(_android_sms_import_class().read(lease, first, last))
        if len(raw) > 16 * 1024 * 1024:
            return "too_many", []
        data = json.loads(raw)
        return data["state"], data.get("records", [])
    except Exception as exc:
        raise RuntimeError("Native SMS provider failed: " + type(exc).__name__) from None


def _android_sms_lease_active(lease: str) -> bool:
    try:
        return bool(_android_sms_import_class().active(lease))
    except Exception as exc:
        raise RuntimeError("Native SMS consent check failed: " + type(exc).__name__) from None


def _serve() -> None:
    assert _SERVER is not None
    _SERVER.serve_forever(poll_interval=0.25)


def set_lan_addresses(lan_addresses: str = "") -> bool:
    """Refresh native Android LAN addresses without restarting Flask."""
    _configure_lan_addresses(lan_addresses)
    return True


def _startup_stage(debug: bool, stage: str) -> None:
    if debug is True:
        print("SO4ME_PY_START " + stage, flush=True)


def start(
    runtime_root: str,
    error_report_url: str = "",
    account_email: str = "",
    bootstrap_token: str = "",
    lan_addresses: str = "",
    debug_startup: bool = False,
) -> bool:
    """Import the app and bind the local server before returning.

    Import and bind errors intentionally happen on the calling thread so
    Chaquopy forwards the real Python exception to Android instead of hiding
    it behind a generic backend timeout.  The optional account identity and
    bootstrap token are provided only by the native Android startup activity.
    """
    global _SERVER, _THREAD

    _startup_stage(debug_startup, "python-entry")
    root = Path(runtime_root).resolve()
    if not (root / "app" / "__init__.py").is_file():
        raise RuntimeError(f"SimpleOffice4Me sources missing under {root}")
    if not (root / "simpleoffice_version.py").is_file():
        raise RuntimeError(f"simpleoffice_version.py missing under {root}")
    if not (root / "tools" / "launcher.py").is_file():
        raise RuntimeError(f"tools/launcher.py missing under {root}")

    if _THREAD is not None and _THREAD.is_alive() and _SERVER is not None:
        # BootstrapActivity creates a fresh token for every native launch. The
        # already-running Flask app reads the token/account from the environment
        # on each native request, so refresh those values without restarting the
        # local server. Calls from NavigationActivity intentionally omit a token
        # and must not clear a valid bootstrap identity.
        if bootstrap_token:
            _configure_environment(
                root, error_report_url, account_email, bootstrap_token
            )
            _configure_lan_addresses(lan_addresses)
        elif lan_addresses:
            _configure_lan_addresses(lan_addresses)
        return True

    _configure_environment(
        root, error_report_url, account_email, bootstrap_token
    )
    # Importing any app.* module implicitly initializes app/__init__.py.
    # Finish environment and native linking before LAN setup can do that.
    _startup_stage(debug_startup, "environment-ready")
    _promote_android_python_symbols()
    _startup_stage(debug_startup, "symbols-ready")
    _verify_android_cryptography_extension()
    _startup_stage(debug_startup, "rust-ready")

    try:
        from app import app
        _startup_stage(debug_startup, "flask-imported")
        from app import android_auth, android_paired_folder
        # Register native routes on every Android runtime start, even when the
        # system restores NavigationActivity directly. Without a bootstrap token
        # the sensitive endpoints still fail closed in _require_native_request.
        if android_auth.bp.name not in app.blueprints:
            app.register_blueprint(android_auth.bp)
        if android_paired_folder.bp.name not in app.blueprints:
            app.register_blueprint(android_paired_folder.bp)
        _configure_lan_addresses(lan_addresses)
        _startup_stage(debug_startup, "routes-ready")
    except Exception as exc:
        raise RuntimeError(f"SimpleOffice4Me import failed: {type(exc).__name__}: {exc}") from exc

    try:
        app.config["ANDROID_PRIVATE_SPEECH_CACHE"] = str(_android_speech_class().cacheDirectory())
    except Exception as exc:
        app.logger.warning("Optional Android device speech unavailable: %s", type(exc).__name__)
    else:
        app.config.update(
            ANDROID_PRIVATE_SPEECH=_render_android_speech,
            ANDROID_PRIVATE_SPEECH_VOICES=_android_speech_voices,
            ANDROID_PRIVATE_SPEECH_STATUS=_android_speech_status,
            ANDROID_PRIVATE_SPEECH_REFRESH=_refresh_android_speech,
            ANDROID_PRIVATE_SPEECH_SETTINGS=True,
        )
    try:
        sms_enabled = _android_sms_import_class().enabled()
    except Exception as exc:
        app.logger.warning("Optional Android SMS import unavailable: %s", type(exc).__name__)
        sms_enabled = False
    if sms_enabled:
        app.config.update(
            ANDROID_PRIVATE_SMS_READER=_read_android_sms,
            ANDROID_PRIVATE_SMS_LEASE_ACTIVE=_android_sms_lease_active,
        )
    app.config["TEMPLATES_AUTO_RELOAD"] = False
    app.config["MAX_CONTENT_LENGTH"] = min(int(app.config["MAX_CONTENT_LENGTH"]), 256 * 1024 * 1024)
    # The native bootstrap runs on every cold app start. A long cookie lifetime
    # prevents the embedded WebView from ever falling back to the browser login
    # screen while the app remains open; password-enabled launches are still
    # explicitly unlocked by BootstrapActivity before the WebView is shown.
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)

    try:
        # Keep the WebView on 127.0.0.1, but listen on every device interface
        # so a peer can actually reach the Connect-QR address over WLAN,
        # Ethernet or Android hotspot/tethering.
        _SERVER = make_server(
            "0.0.0.0",
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
    _startup_stage(debug_startup, "backend-listening")
    return True
