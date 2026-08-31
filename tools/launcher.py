#!/usr/bin/env python3
"""First-start wizard and production WSGI server launcher.

The platform scripts create the virtual environment and install dependencies.
This module deliberately uses only the Python standard library until the
configured application is started.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Direct execution (``python tools/launcher.py``) otherwise exposes only the
# tools directory on sys.path. Keep that supported for existing installations
# while the platform starters use the unambiguous module invocation below.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
CONFIG_PATH = PROJECT_ROOT / "instance" / "simpleoffice.json"
SCAN_STATUS_FILE_INTERVAL = 250
SCAN_STATUS_TIME_INTERVAL = 2.0


def _integer_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} muss eine ganze Zahl sein.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} muss zwischen {minimum} und {maximum} liegen.")
    return value


def waitress_options(config: dict[str, object], max_request_body_size: int) -> dict[str, object]:
    """Build bounded Waitress settings while keeping the local bind default."""
    host = os.environ.get("SIMPLEOFFICE_HOST", str(config["host"])).strip()
    if not host or any(character.isspace() for character in host):
        raise RuntimeError("SIMPLEOFFICE_HOST darf nicht leer sein oder Leerzeichen enthalten.")
    options: dict[str, object] = {
        "host": host,
        "port": _integer_setting("SIMPLEOFFICE_PORT", int(config["port"]), 1, 65535),
        "threads": _integer_setting("SIMPLEOFFICE_WSGI_THREADS", 4, 1, 64),
        "channel_timeout": _integer_setting("SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT", 120, 10, 3600),
        "max_request_body_size": max_request_body_size,
        "expose_tracebacks": False,
        "ident": "SimpleOffice4Me",
    }
    # ProxyFix remains the single authority for configured proxy chains. In
    # that explicit mode Waitress must preserve the headers until Flask has
    # applied the configured hop count. The deployment docs require the app to
    # be unreachable except through that proxy.
    if _integer_setting("SIMPLEOFFICE_TRUSTED_PROXY_HOPS", 0, 0, 16) > 0:
        options["clear_untrusted_proxy_headers"] = False
    return options


def should_report_scan_progress(
    current_files: int,
    last_files: int,
    now: float,
    last_at: float,
) -> bool:
    """Keep the background scan visible without writing status per file."""
    return (
        current_files - last_files >= SCAN_STATUS_FILE_INTERVAL
        or now - last_at >= SCAN_STATUS_TIME_INTERVAL
    )


def background_index_enabled() -> bool:
    value = os.environ.get("SIMPLEOFFICE_BACKGROUND_INDEX", "1").strip().casefold()
    return value not in {"0", "false", "no", "off"}


def start_index_worker(document_root: str) -> subprocess.Popen[bytes] | None:
    """Start the rebuildable index in an isolated, low-priority process."""
    if not background_index_enabled():
        return None
    command = [
        sys.executable,
        "-m",
        "tools.index_worker",
        "--root",
        document_root,
    ]
    options: dict[str, object] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        # BELOW_NORMAL_PRIORITY_CLASS.  Keep this literal to avoid pywin32.
        options["creationflags"] = 0x00004000
    else:
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def start_osm_index_worker(document_root: str, *, force: bool = False, city: str = "") -> subprocess.Popen[bytes]:
    """Check the local address index on every start and rebuild it if needed."""
    command = [sys.executable, "-m", "tools.osm_index_worker", "--root", document_root]
    if force:
        command.append("--force")
    if city:
        command.extend(["--city", city])
    options: dict[str, object] = {"cwd": str(PROJECT_ROOT), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = 0x00004000
    else:
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def start_osm_download_worker(document_root: str, region: str) -> subprocess.Popen[bytes]:
    """Download a resumable Geofabrik extract and index it outside WSGI."""
    command = [
        sys.executable,
        "-m",
        "tools.osm_download_worker",
        "--root",
        document_root,
        "--region",
        region,
    ]
    options: dict[str, object] = {"cwd": str(PROJECT_ROOT), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = 0x00004000
    else:
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def datalogger_enabled() -> bool:
    return os.environ.get("SIMPLEOFFICE_DATALOGGER", "1").strip().casefold() not in {"0", "false", "no", "off"}


def start_datalogger_worker(document_root: str) -> subprocess.Popen[bytes] | None:
    """Run periodic sensor I/O outside all WSGI request threads."""
    if not datalogger_enabled():
        return None
    options: dict[str, object] = {"cwd": str(PROJECT_ROOT), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = 0x00004000
    return subprocess.Popen([sys.executable, "-m", "tools.datalogger_worker", "--root", document_root], **options)


def stop_worker(worker: subprocess.Popen[bytes] | None) -> None:
    if worker is None or worker.poll() is not None:
        return
    worker.terminate()
    try:
        worker.wait(timeout=10)
    except subprocess.TimeoutExpired:
        print(f"Hintergrunddienst PID {worker.pid} reagiert nicht auf SIGTERM; kein erzwungenes Beenden.", file=sys.stderr)


def default_document_root() -> Path:
    documents = Path.home() / "Documents"
    return documents / "SimpleOffice4Me"


def load_config() -> dict[str, object] | None:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_config(config: dict[str, object]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(CONFIG_PATH)


def first_start_configure(interactive: bool = True) -> dict[str, object]:
    existing = load_config()
    if existing is not None:
        return existing

    default_root = default_document_root()
    document_root = default_root
    port = 8080
    if interactive and sys.stdin.isatty():
        print("SimpleOffice4Me – Ersteinrichtung")
        answer = input(f"Dokumentordner [{default_root}]: ").strip()
        if answer:
            document_root = Path(answer).expanduser()
        answer = input("Lokaler Port [8080]: ").strip()
        if answer:
            try:
                port = int(answer)
            except ValueError:
                print("Ungültiger Port, 8080 wird verwendet.")

    config: dict[str, object] = {
        "version": 1,
        "document_root": str(document_root.resolve()),
        "host": "127.0.0.1",
        "port": port,
    }
    save_config(config)
    return config


def start(configure_only: bool = False) -> None:
    config = first_start_configure()
    if configure_only:
        print(f"Einrichtung gespeichert: {CONFIG_PATH}")
        return

    document_root = str(config["document_root"])
    os.environ["SIMPLEOFFICE_DOCUMENT_ROOT"] = document_root

    # Imports happen after the environment and configuration are available.
    from tools.service_control import register, unregister
    register("web", os.getpid(), "launcher.py")
    from app import app
    options = waitress_options(config, int(app.config["MAX_CONTENT_LENGTH"]))
    try:
        worker = start_index_worker(document_root)
    except OSError as exc:
        worker = None
        from app.document_store import DocumentStore
        DocumentStore(document_root).set_scan_status({"state": "failed", "error": f"Indexdienst konnte nicht gestartet werden: {exc}"})
        print(f"Indexdienst konnte nicht gestartet werden: {exc}", file=sys.stderr, flush=True)
    try:
        osm_worker = start_osm_index_worker(
            document_root,
            force=os.environ.get("SIMPLEOFFICE_OSM_REINDEX_ON_START", "").strip().casefold() in {"1", "true", "yes", "on"},
        )
    except OSError as exc:
        osm_worker = None
        print(f"OSM-Indexdienst konnte nicht gestartet werden: {exc}", file=sys.stderr, flush=True)
    try:
        datalogger_worker = start_datalogger_worker(document_root)
    except OSError as exc:
        datalogger_worker = None
        print(f"Datenloggerdienst konnte nicht gestartet werden: {exc}", file=sys.stderr, flush=True)
    host = str(options["host"])
    port = int(options["port"])
    index_message = (
        f"Indexdienst PID {worker.pid} startet getrennt."
        if worker is not None else
        "Automatischer Indexdienst ist deaktiviert oder nicht verfügbar."
    )
    print(
        f"SimpleOffice4Me läuft mit Waitress unter http://{host}:{port} "
        f"({options['threads']} Threads); {index_message}",
        flush=True,
    )
    from waitress import serve

    previous_handlers: dict[int, object] = {}
    def request_stop(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, request_stop)
    try:
        serve(app, **options)
    finally:
        stop_worker(worker)
        stop_worker(osm_worker)
        stop_worker(datalogger_worker)
        if worker is not None and worker.poll() is not None:
            unregister("index", worker.pid)
        unregister("web", os.getpid())
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me launcher")
    parser.add_argument("command", nargs="?", choices=("start", "setup"), default="start")
    args = parser.parse_args()
    start(configure_only=args.command == "setup")


if __name__ == "__main__":
    main()
