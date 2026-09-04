#!/usr/bin/env python3
"""Rebuild the local OSM autocomplete index from an existing PBF extract."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from app.file_lock import exclusive_file_lock
from app.osm_pbf import PurePythonAddressIndex
from tools.index_worker import lower_process_priority


class OsmIndexInterrupted(Exception):
    """Raised in the main thread so a confirmed PBF checkpoint is preserved."""


def run_osm_index(root: str | Path, *, force: bool = False, city: str = "") -> int:
    index = PurePythonAddressIndex(root)
    source = index.downloaded_source()
    if source is None:
        print("Kein lokaler OSM-Download vorhanden; Indexprüfung beendet.", flush=True)
        return 0
    with exclusive_file_lock(index.build_lock, blocking=False) as acquired:
        if not acquired:
            print("OSM-Indexer läuft bereits; zweiter Start wird beendet.", flush=True)
            return 0
        if not force and not index.needs_reindex(source):
            print("Lokaler OSM-Adressindex ist aktuell.", flush=True)
            return 0
        lower_process_priority()
        previous_handlers: dict[int, object] = {}

        def request_stop(signum: int, _frame: object) -> None:
            raise OsmIndexInterrupted(f"signal {signum}")

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, request_stop)
        try:
            def report(progress):
                print(
                    "OSM-PBF: "
                    f"{progress.get('progress_percent', 0)}% "
                    f"scanned={progress.get('scanned', 0)} "
                    f"processed={progress['processed']} inserted={progress['inserted']} "
                    f"updated={progress['updated']} duplicates={progress['duplicates']} "
                    f"rejected={progress['rejected']} rate={progress['records_per_second']}/s",
                    flush=True,
                )

            stats = index.build(source, city=city, progress=report)
        except OsmIndexInterrupted:
            index._write_status(
                state="interrupted",
                ready=index.status().get("ready", False),
                resumable=True,
                error="OSM-Indexierung unterbrochen; Fortsetzung ab letztem PBF-Block möglich.",
            )
            print("OSM-Indexierung unterbrochen; bestätigter PBF-Fortschritt bleibt erhalten.", file=sys.stderr, flush=True)
            return 130
        except Exception as exc:
            index._write_status(state="error", ready=index.status().get("ready", False), resumable=True, error=str(exc)[:500])
            print(f"OSM-Indexierung fehlgeschlagen: {exc}", file=sys.stderr, flush=True)
            return 1
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        print(
            "OSM-Indexierung abgeschlossen: "
            f"scanned={stats.get('scanned', 0)} processed={stats['processed']} "
            f"inserted={stats['inserted']} updated={stats['updated']} "
            f"duplicates={stats['duplicates']} id_collisions={stats['id_collisions']} "
            f"rejected={stats['rejected']} stored={stats['stored']}.",
            flush=True,
        )
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me OSM address index worker")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--city", default="", help="Replace only this exact addr:city value")
    args = parser.parse_args()
    raise SystemExit(run_osm_index(args.root, force=args.force, city=args.city))


if __name__ == "__main__":
    main()
