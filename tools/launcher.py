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
import sys
import threading
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
    from app import app
    from app.document_store import DocumentStore

    store = DocumentStore(document_root)
    options = waitress_options(config, int(app.config["MAX_CONTENT_LENGTH"]))

    def initial_scan() -> None:
        store.set_scan_status({"state": "running", "files": 0, "new_files": 0, "duplicates": 0, "errors": 0})
        last_reported_files = 0
        last_reported_at = time.monotonic()

        def report_progress(current: object) -> None:
            nonlocal last_reported_files, last_reported_at
            now = time.monotonic()
            current_files = int(getattr(current, "files"))
            if not should_report_scan_progress(current_files, last_reported_files, now, last_reported_at):
                return
            status = {
                "state": "running",
                "files": current_files,
                "new_files": int(getattr(current, "new_files")),
                "duplicates": int(getattr(current, "duplicates")),
                "errors": int(getattr(current, "errors")),
            }
            store.set_scan_status(status)
            print(
                "Initialscan: "
                f"files={status['files']} new={status['new_files']} "
                f"duplicates={status['duplicates']} errors={status['errors']}",
                flush=True,
            )
            last_reported_files = current_files
            last_reported_at = now

        try:
            report = store.scan(report_progress)
            store.set_scan_status({"state": "completed", "files": report.files, "new_files": report.new_files, "duplicates": report.duplicates, "errors": report.errors})
            print(f"Initialscan abgeschlossen: files={report.files} new={report.new_files} duplicates={report.duplicates} errors={report.errors}")
        except Exception as exc:
            store.set_scan_status({"state": "failed", "error": str(exc)})
            print(f"Initialscan fehlgeschlagen: {exc}", file=sys.stderr)

    threading.Thread(target=initial_scan, name="simpleoffice-initial-scan", daemon=True).start()
    host = str(options["host"])
    port = int(options["port"])
    print(
        f"SimpleOffice4Me läuft mit Waitress unter http://{host}:{port} "
        f"({options['threads']} Threads); Initialscan läuft im Hintergrund.",
        flush=True,
    )
    from waitress import serve

    serve(app, **options)


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me launcher")
    parser.add_argument("command", nargs="?", choices=("start", "setup"), default="start")
    args = parser.parse_args()
    start(configure_only=args.command == "setup")


if __name__ == "__main__":
    main()
