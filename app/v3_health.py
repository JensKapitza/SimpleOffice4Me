"""Bounded system-health registry for the additive SimpleOffice 3.0 rollout."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Empty, Queue
import shutil
import sqlite3
from threading import Thread
import time
from typing import Any, Callable, Mapping

from .sqlite_utils import connect as sqlite_connect


HEALTH_STATES = {"healthy", "degraded", "unavailable", "not_configured", "unknown"}


@dataclass(frozen=True)
class HealthResult:
    name: str
    status: str
    code: str
    message: str
    required: bool
    checked_at: str
    duration_ms: int
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthCheck:
    name: str
    probe: Callable[[], Mapping[str, Any] | HealthResult]
    required: bool = False
    timeout_seconds: float = 1.0


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _bounded_metrics(value: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in dict(value or {}).items():
        safe_key = str(key)[:80]
        if isinstance(item, bool):
            result[safe_key] = item
        elif isinstance(item, int):
            result[safe_key] = max(-10**12, min(10**12, item))
        elif isinstance(item, float):
            result[safe_key] = round(max(-10**12, min(10**12, item)), 3)
        elif isinstance(item, str):
            result[safe_key] = item[:160]
    return result


def result(
    name: str,
    status: str,
    code: str,
    message: str,
    *,
    required: bool = False,
    duration_ms: int = 0,
    metrics: Mapping[str, Any] | None = None,
) -> HealthResult:
    status = str(status).strip().casefold()
    if status not in HEALTH_STATES:
        status = "unknown"
    return HealthResult(
        name=str(name)[:100],
        status=status,
        code=str(code)[:100],
        message=str(message)[:240],
        required=bool(required),
        checked_at=_timestamp(),
        duration_ms=max(0, min(60_000, int(duration_ms))),
        metrics=_bounded_metrics(metrics),
    )


class HealthRegistry:
    def __init__(self) -> None:
        self._checks: dict[str, HealthCheck] = {}

    def register(
        self,
        name: str,
        probe: Callable[[], Mapping[str, Any] | HealthResult],
        *,
        required: bool = False,
        timeout_seconds: float = 1.0,
    ) -> None:
        key = str(name).strip()
        if not key or key in self._checks:
            raise ValueError("health check names must be unique and non-empty")
        self._checks[key] = HealthCheck(
            key,
            probe,
            bool(required),
            max(0.01, min(10.0, float(timeout_seconds))),
        )

    @staticmethod
    def _normalize(check: HealthCheck, value: Mapping[str, Any] | HealthResult, elapsed: float) -> HealthResult:
        if isinstance(value, HealthResult):
            return result(
                check.name,
                value.status,
                value.code,
                value.message,
                required=check.required,
                duration_ms=int(elapsed * 1000),
                metrics=value.metrics,
            )
        payload = dict(value or {})
        return result(
            check.name,
            str(payload.get("status", "unknown")),
            str(payload.get("code", "check_unknown")),
            str(payload.get("message", "Health check returned no diagnostic message")),
            required=check.required,
            duration_ms=int(elapsed * 1000),
            metrics=payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {},
        )

    def run(self, *, total_timeout_seconds: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.05, min(30.0, float(total_timeout_seconds)))
        pending: list[tuple[HealthCheck, Queue, float]] = []
        for check in self._checks.values():
            queue: Queue = Queue(maxsize=1)
            started = time.monotonic()

            def worker(current: HealthCheck = check, output: Queue = queue, began: float = started) -> None:
                try:
                    value = current.probe()
                    output.put(("ok", value, time.monotonic() - began), block=False)
                except Exception:
                    output.put(("error", None, time.monotonic() - began), block=False)

            Thread(target=worker, name=f"health-{check.name}", daemon=True).start()
            pending.append((check, queue, started))

        checks: list[HealthResult] = []
        for check, queue, started in pending:
            remaining = max(0.0, deadline - time.monotonic())
            wait_for = min(check.timeout_seconds, remaining)
            try:
                kind, value, elapsed = queue.get(timeout=wait_for) if wait_for > 0 else ("timeout", None, 0.0)
            except Empty:
                kind, value, elapsed = "timeout", None, time.monotonic() - started
            if kind == "ok":
                checks.append(self._normalize(check, value, elapsed))
            elif kind == "error":
                checks.append(
                    result(
                        check.name,
                        "unavailable" if check.required else "degraded",
                        "check_failed",
                        "Health check failed",
                        required=check.required,
                        duration_ms=int(elapsed * 1000),
                    )
                )
            else:
                checks.append(
                    result(
                        check.name,
                        "unavailable" if check.required else "degraded",
                        "check_timeout",
                        "Health check exceeded its time limit",
                        required=check.required,
                        duration_ms=int(elapsed * 1000),
                    )
                )

        required_unavailable = any(item.required and item.status in {"unavailable", "unknown"} for item in checks)
        required_degraded = any(item.required and item.status == "degraded" for item in checks)
        optional_degraded = any((not item.required) and item.status in {"degraded", "unavailable", "unknown"} for item in checks)
        overall = "unavailable" if required_unavailable else "degraded" if required_degraded or optional_degraded else "healthy"
        return {
            "status": overall,
            "live": True,
            "ready": not required_unavailable,
            "checked_at": _timestamp(),
            "checks": checks,
        }


def _database_probe(database: str | Path):
    path = str(database)

    def probe() -> dict[str, Any]:
        connection = sqlite_connect(path, timeout=1)
        try:
            connection.execute("SELECT 1").fetchone()
            quick = connection.execute("PRAGMA quick_check").fetchone()
            if not quick or str(quick[0]).casefold() != "ok":
                return {"status": "degraded", "code": "database_integrity", "message": "Database quick check did not report OK"}
            return {"status": "healthy", "code": "database_ok", "message": "Database is reachable"}
        finally:
            connection.close()

    return probe


def _storage_probe(root: str | Path):
    source = Path(root).expanduser().resolve()

    def probe() -> dict[str, Any]:
        if not source.exists() or not source.is_dir():
            return {"status": "unavailable", "code": "storage_unavailable", "message": "Document storage is unavailable"}
        usage = shutil.disk_usage(source)
        status = "degraded" if usage.free < 256 * 1024 * 1024 else "healthy"
        return {
            "status": status,
            "code": "storage_low_space" if status == "degraded" else "storage_ok",
            "message": "Document storage has low free space" if status == "degraded" else "Document storage is reachable",
            "metrics": {"free_mib": int(usage.free // (1024 * 1024))},
        }

    return probe


def _migration_probe(root: str | Path):
    source = Path(root).expanduser().resolve()

    def probe() -> dict[str, Any]:
        from .v2.cutover import load_cutover_state

        state = load_cutover_state(source)
        if bool(getattr(state, "dirty", False)):
            return {"status": "degraded", "code": "migration_dirty", "message": "Storage migration requires reconciliation"}
        return {
            "status": "healthy",
            "code": "migration_ok",
            "message": "Storage migration state is consistent",
            "metrics": {"mode": str(getattr(state, "mode", "unknown")), "protection": str(getattr(state, "protection_mode", "unknown"))},
        }

    return probe


def _index_probe(root: str | Path):
    source = Path(root).expanduser().resolve()

    def probe() -> dict[str, Any]:
        from .document_store import DocumentStore

        store = DocumentStore(source)
        if not store.index_path.is_file():
            return {"status": "degraded", "code": "index_missing", "message": "Document index has not been initialized"}
        with store._db() as db:
            files = int(db.execute("SELECT COUNT(*) FROM scan_file").fetchone()[0])
        metrics: dict[str, Any] = {"files": files}
        try:
            payload = json.loads(store.scan_status_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                for key in ("errors", "duplicates", "files"):
                    if isinstance(payload.get(key), int):
                        metrics[f"last_{key}"] = int(payload[key])
        except (OSError, json.JSONDecodeError):
            pass
        if int(metrics.get("last_errors", 0)) > 0:
            return {"status": "degraded", "code": "index_scan_errors", "message": "Last document index run reported errors", "metrics": metrics}
        return {"status": "healthy", "code": "index_ok", "message": "Document index is readable", "metrics": metrics}

    return probe


def _jobs_probe(root: str | Path, configured: bool):
    source = Path(root).expanduser().resolve()

    def probe() -> dict[str, Any]:
        if not configured:
            return {"status": "not_configured", "code": "jobs_disabled", "message": "Background jobs capability is disabled"}
        from .v3_jobs import JobStore

        metrics = JobStore(source).metrics()
        status = "degraded" if metrics.get("failed", 0) or metrics.get("oldest_age_seconds", 0) > 3600 else "healthy"
        return {
            "status": status,
            "code": "jobs_backlog" if status == "degraded" else "jobs_ok",
            "message": "Background job queue needs attention" if status == "degraded" else "Background job queue is available",
            "metrics": metrics,
        }

    return probe


def _clamav_probe(configured: bool):
    def probe() -> dict[str, Any]:
        if not configured:
            return {"status": "not_configured", "code": "clamav_disabled", "message": "ClamAV is not configured"}
        from .attachment_security import ClamAV

        status = ClamAV(timeout=5).status()
        if status.get("state") != "available":
            return {"status": "unavailable", "code": "clamav_unavailable", "message": "Configured ClamAV scanner is unavailable"}
        return {"status": "healthy", "code": "clamav_ok", "message": "ClamAV scanner is available", "metrics": {"engine": str(status.get("engine", ""))}}

    return probe


def _s3_probe(configured: bool):
    def probe() -> dict[str, Any]:
        if not configured:
            return {"status": "not_configured", "code": "s3_disabled", "message": "S3 overlay is disabled"}
        return {"status": "healthy", "code": "s3_configured", "message": "S3 overlay is configured"}

    return probe


def _federation_probe(configured: bool):
    def probe() -> dict[str, Any]:
        if not configured:
            return {"status": "not_configured", "code": "federation_v3_disabled", "message": "Federation 3.0 capability is disabled"}
        try:
            from .v3_federation import health_snapshot
        except ImportError:
            return {"status": "degraded", "code": "federation_v3_unavailable", "message": "Federation 3.0 component is not deployed"}
        value = health_snapshot()
        return value if isinstance(value, Mapping) else {"status": "unknown", "code": "federation_v3_unknown", "message": "Federation 3.0 returned no status"}

    return probe


def default_registry(
    root: str | Path,
    database: str | Path,
    *,
    config: Mapping[str, Any] | None = None,
    capabilities: Mapping[str, bool] | None = None,
) -> HealthRegistry:
    cfg = dict(config or {})
    caps = dict(capabilities or {})
    registry = HealthRegistry()
    registry.register("database", _database_probe(database), required=True, timeout_seconds=1.5)
    registry.register("storage", _storage_probe(root), required=True, timeout_seconds=1.5)
    registry.register("migration", _migration_probe(root), required=True, timeout_seconds=1.0)
    registry.register("index", _index_probe(root), required=True, timeout_seconds=1.5)
    registry.register("jobs", _jobs_probe(root, bool(caps.get("v3.jobs"))), timeout_seconds=1.0)
    clamav_configured = bool(cfg.get("CLAMAV_ENABLED") or cfg.get("WEBDAV_CLAMAV") or cfg.get("CLAMAV_SCANNER"))
    registry.register("clamav", _clamav_probe(clamav_configured), timeout_seconds=5.0)
    registry.register("s3", _s3_probe(bool(cfg.get("S3_OVERLAY_ENABLED"))), timeout_seconds=0.5)
    registry.register("federation", _federation_probe(bool(caps.get("v3.federation"))), timeout_seconds=1.0)
    return registry


def public_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(report.get("status", "unknown")),
        "live": bool(report.get("live", True)),
        "ready": bool(report.get("ready", False)),
        "checked_at": str(report.get("checked_at", "")),
    }
