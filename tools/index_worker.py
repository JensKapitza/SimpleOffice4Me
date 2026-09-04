#!/usr/bin/env python3
"""Isolated, low-priority document index worker.

The web process only starts this service.  Scanning, hashing, PDF extraction,
archive inspection and OCR run outside Waitress and cannot consume its Python
thread pool or GIL.
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.archive_indexer import cleanup_stale_scratch, index_archive, is_supported_archive
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


def _post_processor(store: DocumentStore, previews: PreviewService):
    def process(path: Path) -> None:
        if previews.supports(path):
            metadata = store.get_document(path)
            current = metadata.get("preview", {})
            if not (
                current.get("status") == "ready"
                and current.get("source_sha256") == metadata.get("sha256")
                and previews.cached_path(metadata)
            ):
                store.set_preview_metadata(metadata["document_id"], previews.generate(path, metadata))
        if is_supported_archive(path):
            index_archive(store, path)

    return process


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
        cleanup_stale_scratch(store)
        tools = detect_preview_tools()
        previews = PreviewService(root, tools)
        post_process = _post_processor(store, previews)
        store.set_scan_status({"state": "detecting_tools", "preview_tools": tools["commands"]})
        print("Vorschauwerkzeuge: " + ", ".join(f"{name}={'ja' if available else 'nein'}" for name, available in tools["commands"].items()), flush=True)
        started = time.monotonic()
        store.set_scan_status({
            "state": "starting",
            "files": 0,
            "new_files": 0,
            "updated_files": 0,
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
                "updated_files": int(getattr(current, "updated_files")),
                "duplicates": int(getattr(current, "duplicates")),
                "errors": int(getattr(current, "errors")),
                "process_id": os.getpid(),
                "preview_tools": tools["commands"],
            }
            store.set_scan_status(status)
            print(
                "Index: "
                f"files={status['files']} new={status['new_files']} updated={status['updated_files']} "
                f"duplicates={status['duplicates']} errors={status['errors']}",
                flush=True,
            )
            last_reported_files = current_files
            last_reported_at = now

        def yield_to_web(_path: Path) -> None:
            if yield_ms:
                time.sleep(yield_ms / 1000)

        store.set_scan_status({
            "state": "running", "files": 0, "new_files": 0, "updated_files": 0,
            "duplicates": 0, "errors": 0, "process_id": os.getpid(), "preview_tools": tools["commands"],
        })
        try:
            report = store.scan(report_progress, yield_to_web, post_file=post_process)
            elapsed = round(time.monotonic() - started, 3)
            store.set_scan_status({
                "state": "completed", "files": report.files,
                "new_files": report.new_files, "updated_files": report.updated_files, "duplicates": report.duplicates,
                "errors": report.errors, "duration_seconds": elapsed,
                "preview_tools": tools["commands"],
            })
            print(
                f"Index abgeschlossen: files={report.files} new={report.new_files} updated={report.updated_files} "
                f"duplicates={report.duplicates} errors={report.errors} duration={elapsed}s",
                flush=True,
            )
            return 0
        except Exception as exc:
            store.set_scan_status({"state": "failed", "error": str(exc), "preview_tools": tools["commands"]})
            print(f"Index fehlgeschlagen: {exc}", file=sys.stderr, flush=True)
            return 1


def run_incremental(root: str | Path, paths: set[Path]) -> int:
    """Apply a debounced filesystem-event batch without walking the whole tree."""
    store = DocumentStore(root)
    store.initialize()
    with exclusive_file_lock(store.control / ".index-worker.lock", blocking=False) as acquired:
        if not acquired:
            return 0
        previews = PreviewService(root, detect_preview_tools())
        post_process = _post_processor(store, previews)
        try:
            report = store.scan_changed_paths(paths, post_file=post_process)
            store.set_scan_status({"state":"watching", "files":report.files, "new_files":report.new_files,
                                   "updated_files":report.updated_files, "duplicates":report.duplicates,
                                   "errors":report.errors, "process_id":os.getpid(), "mode":"inotify"})
            return 0
        except Exception as exc:
            store.set_scan_status({"state":"failed", "error":str(exc), "process_id":os.getpid(), "mode":"inotify"})
            return 1


class _IndexEventHandler(FileSystemEventHandler):
    def __init__(self, changes: queue.Queue[Path | None], root: Path):
        self.changes = changes
        self.root = root

    def _ignored(self, value: str) -> bool:
        try:
            first = Path(value).resolve(strict=False).relative_to(self.root).parts[0]
        except (ValueError, IndexError):
            return True
        return first in {".simpleoffice-meta", ".simpleoffice-history", ".webcache"}

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        if self._ignored(event.src_path):
            return
        if event.is_directory:
            self.changes.put(None)
            return
        self.changes.put(Path(event.src_path))
        destination = getattr(event, "dest_path", "")
        if destination and not self._ignored(destination):
            self.changes.put(Path(destination))


def run_service(root: str | Path) -> int:
    """Watch with native filesystem notifications and reconcile every six hours."""
    store = DocumentStore(root)
    store.initialize()
    with exclusive_file_lock(store.control / ".index-service.lock", blocking=False) as acquired:
        if not acquired:
            print("Indexdienst läuft bereits; zweiter Dienst wird beendet.", flush=True)
            return 0
        interval = _bounded_environment("SIMPLEOFFICE_INDEX_RECONCILE_SECONDS", 21_600, 60, 86_400)
        changes: queue.Queue[Path | None] = queue.Queue()
        resolved_root = Path(root).resolve()
        observer = Observer()
        observer.schedule(_IndexEventHandler(changes, resolved_root), str(resolved_root), recursive=True)
        observer.start()
        if run_index(root):
            observer.stop()
            observer.join(timeout=5)
            return 1
        next_reconcile = time.monotonic() + interval
        store.set_scan_status({"state":"watching", "process_id":os.getpid(), "mode":"inotify", "reconcile_seconds":interval})
        try:
            while True:
                timeout = max(0.1, min(1.0, next_reconcile - time.monotonic()))
                try:
                    first = changes.get(timeout=timeout)
                except queue.Empty:
                    first = ...
                if time.monotonic() >= next_reconcile:
                    run_index(root)
                    next_reconcile = time.monotonic() + interval
                    store.set_scan_status({"state":"watching", "process_id":os.getpid(), "mode":"inotify", "reconcile_seconds":interval})
                    continue
                if first is ...:
                    continue
                paths: set[Path] = set()
                full_scan = first is None
                if isinstance(first, Path):
                    paths.add(first)
                debounce_until = time.monotonic() + 0.75
                while time.monotonic() < debounce_until:
                    try:
                        item = changes.get(timeout=max(0.01, debounce_until - time.monotonic()))
                    except queue.Empty:
                        break
                    if item is None:
                        full_scan = True
                    else:
                        paths.add(item)
                    debounce_until = time.monotonic() + 0.25
                if full_scan:
                    run_index(root)
                elif paths:
                    run_incremental(root, paths)
        except KeyboardInterrupt:
            return 0
        finally:
            observer.stop()
            observer.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me document index worker")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--once", action="store_true", help="run one reconciliation and exit")
    args = parser.parse_args()
    from tools.service_control import register, unregister
    register("index", os.getpid(), "index_worker")
    try:
        result = run_index(args.root) if args.once else run_service(args.root)
    finally:
        unregister("index", os.getpid())
    raise SystemExit(result)


if __name__ == "__main__":
    main()
