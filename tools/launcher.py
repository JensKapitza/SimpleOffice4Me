#!/usr/bin/env python3
"""First-start wizard and local development server launcher.

The platform scripts create the virtual environment and install dependencies.
This module deliberately uses only the Python standard library until the
configured application is started.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "instance" / "simpleoffice.json"


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

    report = DocumentStore(document_root).scan()
    print(
        f"Dokumentordner geprüft: files={report.files} new={report.new_files} "
        f"duplicates={report.duplicates} symlinks={report.symlinks} "
        f"boundaries={report.skipped_boundaries} errors={report.errors}"
    )
    host = str(config["host"])
    port = int(config["port"])
    print(f"SimpleOffice4Me läuft unter http://{host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me launcher")
    parser.add_argument("command", nargs="?", choices=("start", "setup"), default="start")
    args = parser.parse_args()
    start(configure_only=args.command == "setup")


if __name__ == "__main__":
    main()
