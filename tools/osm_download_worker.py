#!/usr/bin/env python3
"""Resume an OSM download and rebuild its local address index."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.osm_address import GEOFABRIK_REGIONS, LocalAddressIndex
from tools.index_worker import lower_process_priority
from tools.osm_index_worker import run_osm_index


def run_osm_download(root: str | Path, region: str) -> int:
    index = LocalAddressIndex(root)
    if region not in GEOFABRIK_REGIONS:
        index._write_status(state="error", error="Unbekannte Geofabrik-Region.")
        return 2
    lower_process_priority()
    try:
        source = index.download_region(region)
    except Exception as exc:
        index._write_status(
            state="error",
            ready=index.status().get("ready", False),
            resumable=True,
            error=str(exc)[:500],
        )
        print(f"OSM-Download fehlgeschlagen: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"OSM-Download abgeschlossen: {source}", flush=True)
    return run_osm_index(root, force=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me OSM download worker")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--region", required=True, choices=tuple(GEOFABRIK_REGIONS))
    args = parser.parse_args()
    raise SystemExit(run_osm_download(args.root, args.region))


if __name__ == "__main__":
    main()
