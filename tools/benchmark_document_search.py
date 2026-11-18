"""Benchmark request-scoped document search visibility paging.

Example::

    python -m tools.benchmark_document_search --documents 2000 \
        --hidden-fractions 0,0.5,0.9 --pages 1,20

The benchmark creates an isolated temporary document root and uses the real
DocumentStore index, Flask request context, and visibility policy. Output is
JSON so runs can be compared or archived easily.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch


def _csv_ints(value: str) -> list[int]:
    try:
        values = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not values or any(item < 1 for item in values):
        raise argparse.ArgumentTypeError("values must be positive integers")
    return values


def _csv_fractions(value: str) -> list[float]:
    try:
        values = [float(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated fractions") from exc
    if not values or any(not 0 <= item <= 1 for item in values):
        raise argparse.ArgumentTypeError("fractions must be between 0 and 1")
    return values


def _make_store(root: Path, count: int, hidden_fraction: float, store_class):
    root.mkdir(parents=True)
    store = store_class(root)
    store.initialize()
    hidden_count = round(count * hidden_fraction)
    # Interleave private documents across the insertion order of the index.
    hidden_indexes = {(index * count) // hidden_count for index in range(hidden_count)}
    for index in range(count):
        path = root / f"benchmark-{index:08d}.txt"
        path.write_text("documentsearchbenchmarktoken", encoding="utf-8")
        store._scan_file(path, force_hash=True)
        if index in hidden_indexes:
            document = store.get_document(path)
            store.update_metadata(
                document["document_id"],
                attributes={"chat_attachment": {
                    "visibility": "chat",
                    "allowed_local_users": ["someone-else"],
                }},
                author="benchmark-setup",
            )
    return store


def _measure_page(store, page, page_size, repeats):
    read_samples = []
    elapsed_ms = []
    original_read = store._read_json
    sidecars = store.documents

    def counted_read(path, default):
        nonlocal reads
        if path.parent == sidecars and path.suffix == ".json":
            reads += 1
        return original_read(path, default)

    with patch.object(store, "_read_json", counted_read):
        for _ in range(repeats):
            reads = 0
            started = time.perf_counter()
            result = store.search_page("documentsearchbenchmarktoken", page=page, page_size=page_size)
            elapsed_ms.append((time.perf_counter() - started) * 1000)
            read_samples.append(reads)

    return {
        "page": page, "page_size": page_size,
        "visible_results": len(result["results"]), "has_next": result["has_next"],
        "metadata_reads_per_search": _summary(read_samples),
        "elapsed_ms_per_search": _summary(elapsed_ms),
    }


def _summary(samples):
    return {"min": min(samples), "max": max(samples), "mean": sum(samples) / len(samples)}


def run_benchmark(document_count, page_size, hidden_fractions, pages, repeats) -> dict[str, Any]:
    cases = []
    with tempfile.TemporaryDirectory(prefix="document-search-benchmark-") as temp:
        fixture_root = Path(temp)
        # Import the application only after isolating its startup document root.
        with patch.dict(os.environ, {"SIMPLEOFFICE_DOCUMENT_ROOT": str(fixture_root)}):
            from flask import g
            from app import app
            from app.document_store import DocumentStore

            for ordinal, fraction in enumerate(hidden_fractions):
                store = _make_store(fixture_root / f"case-{ordinal}", document_count, fraction, DocumentStore)
                with app.test_request_context("/documents/search"):
                    g.user = {"username": "benchmark-viewer", "is_admin": False}
                    for page in pages:
                        cases.append({
                            "documents": document_count,
                            "hidden_documents": round(document_count * fraction),
                            "hidden_fraction": fraction,
                            **_measure_page(store, page, page_size, repeats),
                        })
    return {"benchmark": "document_search_visibility", "repeats": repeats, "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=1000, help="synthetic documents per hidden-fraction case")
    parser.add_argument("--page-size", type=int, default=25)
    parser.add_argument("--hidden-fractions", type=_csv_fractions, default=[0.0, 0.5, 0.9])
    parser.add_argument("--pages", type=_csv_ints, default=[1, 10], help="comma-separated first/deep pages")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.documents < 1 or args.repeats < 1:
        parser.error("--documents and --repeats must be positive")
    if not 1 <= args.page_size <= 100:
        parser.error("--page-size must be between 1 and 100")
    print(json.dumps(run_benchmark(
        args.documents, args.page_size, args.hidden_fractions, args.pages, args.repeats
    ), indent=2))


if __name__ == "__main__":
    main()
