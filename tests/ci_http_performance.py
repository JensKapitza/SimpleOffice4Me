"""Bounded, reproducible in-process HTTP smoke for CI; not a production load benchmark."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import statistics
import time

from app import app


def main() -> None:
    requests = int(os.environ.get("PERF_REQUESTS", "120"))
    concurrency = int(os.environ.get("PERF_CONCURRENCY", "6"))
    max_p95_ms = float(os.environ.get("PERF_MAX_P95_MS", "2000"))
    max_error_rate = float(os.environ.get("PERF_MAX_ERROR_RATE", "0"))
    if not (1 <= requests <= 10000 and 1 <= concurrency <= 32 and max_p95_ms > 0):
        raise ValueError("Invalid performance bounds")
    if not 0 <= max_error_rate <= 1:
        raise ValueError("Invalid error-rate threshold")

    def request(_index: int) -> tuple[float, int]:
        start = time.perf_counter()
        with app.test_client() as client:
            response = client.get("/auth/login")
            status = response.status_code
            response.close()
        return (time.perf_counter() - start) * 1000, status

    # Warm-up is excluded from the measurement.
    request(-1)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        observations = list(pool.map(request, range(requests)))
    durations = sorted(item[0] for item in observations)
    failures = sum(status != 200 for _, status in observations)
    p95 = durations[max(0, (len(durations) * 95 + 99) // 100 - 1)]
    report = {
        "endpoint": "/auth/login",
        "mode": "in-process Flask test client (not production throughput)",
        "requests": requests,
        "concurrency": concurrency,
        "p50_ms": round(statistics.median(durations), 2),
        "p95_ms": round(p95, 2),
        "max_ms": round(durations[-1], 2),
        "errors": failures,
        "error_rate": failures / requests,
        "threshold_p95_ms": max_p95_ms,
        "threshold_error_rate": max_error_rate,
    }
    target = Path("test-results/performance/http-smoke.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if p95 > max_p95_ms or failures / requests > max_error_rate:
        raise SystemExit("HTTP performance gate failed")


if __name__ == "__main__":
    main()
