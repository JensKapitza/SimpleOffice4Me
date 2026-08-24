#!/usr/bin/env python3
"""Isolated, low-priority document index worker.

The web process only starts this service.  Scanning, hashing, PDF extraction
and OCR run outside Waitress and cannot consume its Python thread pool or GIL.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from app.document_store import DocumentStore
from app.file_lock import exclusive_file_lock
from app.preview_service import PreviewService, detect_preview_tools
from tools.launcher import should_report_scan_progress


def _bounded_environment(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def lower_process_priority() -> None:
    """Prefer HTTP work over indexing without requiring extra packages."""
    if os.name != "nt" and hasattr(os, "nice"):
        try:
            os.nice(_bounded_environment("SIMPLEOFFICE_INDEX_NICE", 10, 0, 19))
        except OSError:
            # Containers and restricted service accounts may reject niceness.
            pass


def run_index(root: str | Path) -> int:
    store = DocumentStore(root)
    store.initialize()
    lock_path = store.control / ".index-worker.lock"
    with exclusive_file_lock(lock_path, blocking=False) as acquired:
        if not acquired:
            print("Indexdienst läuft bereits; zweiter Start wird beendet.", flush=True)
            return 0

        delay = _bounded_environment("SIMPLEOFFICE_INDEX_DELAY_SECONDS", 2, 0, 300)
        yield_ms = _bounded_environment("SIMPLEOFFICE_INDEX_YIELD_MS", 1, 0, 100)
        lower_process_priority()
        tools = detect_preview_tools()
        previews = PreviewService(root, tools)
        store.set_scan_status({"state": "detecting_tools", "preview_tools": tools["commands"]})
        print("Vorschauwerkzeuge: " + ", ".join(f"{name}={'ja' if available else 'nein'}" for name, available in tools["commands"].items()), flush=True)
        started = time.monotonic()
        store.set_scan_status({
            "state": "starting",
            "files": 0,
            "new_files": 0,
            "duplicates": 0,
            "errors": 0,
            "process_id": os.getpid(),
            "delay_seconds": delay,
            "preview_tools": tools["commands"],
        })
        if delay:
            time.sleep(delay)

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
                "process_id": os.getpid(),
                "preview_tools": tools["commands"],
            }
            store.set_scan_status(status)
            print(
                "Index: "
                f"files={status['files']} new={status['new_files']} "
                f"duplicates={status['duplicates']} errors={status['errors']}",
                flush=True,
            )
            last_reported_files = current_files
            last_reported_at = now

        def yield_to_web(_path: Path) -> None:
            if yield_ms:
                time.sleep(yield_ms / 1000)

        def generate_preview(path: Path) -> None:
            if not previews.supports(path):
                return
            metadata = store.get_document(path)
            current = metadata.get("preview", {})
            if current.get("status") == "ready" and current.get("source_sha256") == metadata.get("sha256") and previews.cached_path(metadata):
                return
            store.set_preview_metadata(metadata["document_id"], previews.generate(path, metadata))

        store.set_scan_status({
            "state": "running", "files": 0, "new_files": 0,
            "duplicates": 0, "errors": 0, "process_id": os.getpid(), "preview_tools": tools["commands"],
        })
        try:
            report = store.scan(report_progress, yield_to_web, post_file=generate_preview)
            elapsed = round(time.monotonic() - started, 3)
            store.set_scan_status({
                "state": "completed", "files": report.files,
                "new_files": report.new_files, "duplicates": report.duplicates,
                "errors": report.errors, "duration_seconds": elapsed,
                "preview_tools": tools["commands"],
            })
            print(
                f"Index abgeschlossen: files={report.files} new={report.new_files} "
                f"duplicates={report.duplicates} errors={report.errors} duration={elapsed}s",
                flush=True,
            )
            return 0
        except Exception as exc:
            store.set_scan_status({"state": "failed", "error": str(exc), "preview_tools": tools["commands"]})
            print(f"Index fehlgeschlagen: {exc}", file=sys.stderr, flush=True)
            return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me document index worker")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    from tools.service_control import register, unregister
    register("index", os.getpid(), "index_worker")
    try:
        result = run_index(args.root)
    finally:
        unregister("index", os.getpid())
    raise SystemExit(result)


if __name__ == "__main__":
    main()
