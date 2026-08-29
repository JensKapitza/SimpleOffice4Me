#!/usr/bin/env python3
"""Rebuild the local OSM autocomplete index from an existing PBF extract."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from app.file_lock import exclusive_file_lock
from app.osm_address import LocalAddressIndex
from tools.index_worker import lower_process_priority


class OsmIndexInterrupted(Exception):
    """Raised in the main thread so build cleanup terminates osmium cleanly."""


def run_osm_index(root: str | Path, *, force: bool = False) -> int:
    index = LocalAddressIndex(root)
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
                replay_target = int(progress.get("replay_target", 0) or 0)
                replayed = int(progress.get("replayed", 0) or 0)
                if replay_target and replayed < replay_target:
                    print(
                        f"OSM-Resume: replayed={replayed}/{replay_target} "
                        f"checkpoint={progress['processed']}",
                        flush=True,
                    )
                    return
                print(
                    "OSM-Index: "
                    f"processed={progress['processed']} inserted={progress['inserted']} "
                    f"updated={progress['updated']} duplicates={progress['duplicates']} "
                    f"rejected={progress['rejected']} rate={progress['records_per_second']}/s",
                    flush=True,
                )

            stats = index.build(source, progress=report)
        except OsmIndexInterrupted:
            index._write_status(
                state="interrupted",
                ready=index.status().get("ready", False),
                resumable=True,
                error="OSM-Indexierung unterbrochen; Fortsetzung ab letztem Checkpoint möglich.",
            )
            print("OSM-Indexierung unterbrochen; bestätigter Fortschritt bleibt erhalten.", file=sys.stderr, flush=True)
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
            f"processed={stats['processed']} inserted={stats['inserted']} updated={stats['updated']} "
            f"duplicates={stats['duplicates']} id_collisions={stats['id_collisions']} "
            f"rejected={stats['rejected']} stored={stats['stored']}.",
            flush=True,
        )
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me OSM address index worker")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run_osm_index(args.root, force=args.force))


if __name__ == "__main__":
    main()
