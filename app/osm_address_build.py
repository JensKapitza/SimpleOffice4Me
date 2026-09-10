"""Build workflow for the local OpenStreetMap address index.

The index object is injected deliberately so this module can own the long-running
filter/export/import orchestration without creating a circular import with the
public ``osm_address`` compatibility module.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .document_store import utc_now


def build_index(
    index: Any,
    source: str | Path,
    *,
    city: str = "",
    progress: Callable[[dict[str, Any]], None] | None = None,
    human_bytes_fn: Callable[[Any], str],
) -> dict[str, int]:
    self = index
    source = Path(source).resolve()
    if not source.is_file() or source.suffix.casefold() != ".pbf":
        raise ValueError("a regular .osm.pbf extract is required")
    osmium = shutil.which("osmium")
    if not osmium:
        raise RuntimeError("osmium is required to build the local address index")
    city = " ".join(str(city).split()).strip()
    if len(city) > 120 or any(ord(character) < 32 for character in city):
        raise ValueError("invalid OSM city filter")
    started = time.monotonic()
    source_fingerprint = self._source_fingerprint(source)
    fingerprint = hashlib.sha256(
        f"{source_fingerprint}\0city:{city.casefold()}".encode("utf-8")
    ).hexdigest() if city else source_fingerprint
    previous_build = self._build_status()
    if previous_build.get("source_fingerprint") != fingerprint:
        self._discard_stale_build()
        previous_build = {}
    self.build_dir.mkdir(parents=True, exist_ok=True)
    self._write_build_status(
        source_fingerprint=fingerprint,
        source_file=str(source),
        city=city,
        completed_at="",
        build_started_at=utc_now(),
        export_complete=bool(
            previous_build.get("export_complete")
            and not previous_build.get("completed_at")
        ),
    )
    active_ready = bool(self.db_path.is_file() and self._stored_status().get("ready"))
    self._write_status(
        state="indexing", phase="filtering", phase_started_at=utc_now(),
        source_file=str(source), source_fingerprint=fingerprint, indexed_at="",
        city=city,
        error="", ready=active_ready, resumable=True,
        processed=0, inserted=0, updated=0, duplicates=0,
        id_collisions=0, rejected=0, stored=0,
        replayed=0, replay_target=0,
    )

    filtered = self.filtered_path
    if not (filtered.is_file() and previous_build.get("filtered_complete")):
        filtered_part = filtered.with_suffix(filtered.suffix + ".part")
        filtered_part.unlink(missing_ok=True)
        expressions = [f"nwr/addr:city={city}"] if city else [
            "nwr/addr:housenumber", "nwr/addr:street",
            "nwr/addr:postcode", "nwr/addr:city",
        ]
        filter_command = [
            osmium, "tags-filter", str(source), *expressions,
            "--remove-tags", "--no-progress",
            # The temporary name ends in .pbf.part, from which osmium can
            # not infer an output format. Without this explicit format it
            # exits immediately with status 2.
            "-f", "pbf", "-o", str(filtered_part), "--overwrite",
        ]
        try:
            completed = subprocess.run(
                filter_command,
                check=True,
                timeout=max(3600, min(int(os.environ.get("SIMPLEOFFICE_OSM_FILTER_TIMEOUT", "21600")), 86400)),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            stderr = str(exc.stderr or "").strip()
            detail = stderr[-4000:] or f"keine stderr-Ausgabe; Befehl: {' '.join(filter_command)}"
            self.filter_log_path.write_text(
                f"[{utc_now()}] {type(exc).__name__}\n{detail}\n",
                encoding="utf-8",
            )
            self._write_build_status(
                filter_complete=False,
                filter_error=detail,
                filter_log=str(self.filter_log_path),
                filter_failed_at=utc_now(),
            )
            if isinstance(exc, subprocess.TimeoutExpired):
                raise RuntimeError(
                    f"osmium tags-filter timed out after {exc.timeout} seconds: {detail[-1000:]}"
                ) from exc
            raise RuntimeError(
                f"osmium tags-filter failed with exit {exc.returncode}: {detail[-1000:]}"
            ) from exc
        filter_stderr = str(getattr(completed, "stderr", "") or "")
        if filter_stderr:
            self.filter_log_path.write_text(filter_stderr[-4000:], encoding="utf-8")
        if not filtered_part.is_file() or filtered_part.stat().st_size == 0:
            raise RuntimeError("osmium tags-filter produced no usable address extract")
        filtered_part.replace(filtered)
        self._write_build_status(
            source_fingerprint=fingerprint,
            source_file=str(source),
            filtered_complete=True,
            filter_error="",
            filtered_bytes=filtered.stat().st_size,
            filtered_at=utc_now(),
        )
    else:
        self._write_status(
            state="indexing", phase="reusing_filtered", phase_started_at=utc_now(),
            resumed=True, filtered_bytes=filtered.stat().st_size,
            filtered_size=human_bytes_fn(filtered.stat().st_size),
        )

    self._write_status(
        state="indexing", phase="exporting_importing", phase_started_at=utc_now(),
        filtered_bytes=filtered.stat().st_size,
        filtered_size=human_bytes_fn(filtered.stat().st_size),
    )
    last_progress = 0.0
    resumed_at = 0

    def report(current: dict[str, int]) -> None:
        nonlocal last_progress
        now = time.monotonic()
        if now - last_progress < 2:
            return
        elapsed = max(0.001, now - started)
        run_records = max(0, current["processed"] - resumed_at)
        payload: dict[str, Any] = {
            **current,
            "state": "indexing",
            "phase": "exporting_importing",
            "resumed": resumed_at > 0,
            "resume_processed": resumed_at,
            "replayed": current.get("replayed", 0),
            "replay_target": current.get("replay_target", 0),
            "elapsed_seconds": round(elapsed, 1),
            "records_per_second": round(run_records / elapsed),
            "last_checkpoint_at": utc_now(),
        }
        self._write_status(**payload)
        if progress:
            progress(payload)
        last_progress = now

    def publish(current: dict[str, int], resume_position: int) -> dict[str, int]:
        self._write_status(
            state="indexing", phase="publishing", phase_started_at=utc_now(),
            processed=current["processed"], stored=current["stored"],
        )
        current["stored"] = (
            self._promote_city_staging_index(city, current["stored"])
            if city else self._promote_staging_index(current["stored"])
        )
        self.staging_db_path.unlink(missing_ok=True)
        Path(str(self.staging_db_path) + "-journal").unlink(missing_ok=True)
        elapsed = round(time.monotonic() - started, 1)
        self._write_build_status(
            source_fingerprint=fingerprint,
            source_file=str(source),
            city=city,
            filtered_complete=True,
            export_complete=True,
            completed_at=utc_now(),
            imported_processed=current["processed"],
        )
        self._write_status(
            state="ready", phase="completed", ready=True,
            count=current["stored"], indexed_at=utc_now(), error="",
            city=city,
            elapsed_seconds=elapsed, resumed=resume_position > 0,
            resume_processed=resume_position, staging_database="", **current,
        )
        return current

    if (
        previous_build.get("export_complete")
        and not previous_build.get("completed_at")
        and self.staging_db_path.is_file()
    ):
        with self._open_db(self.staging_db_path, staging=True) as staging_db:
            stats = self._load_staging_progress(staging_db, fingerprint)
        if stats["processed"] <= 0 or stats["stored"] <= 0:
            raise RuntimeError("completed OSM staging checkpoint is empty")
        self._write_status(
            resumed=True, resume_processed=stats["processed"],
            phase="publishing", staging_database=str(self.staging_db_path),
        )
        return publish(stats, stats["processed"])

    # stderr is a regular persistent file: it cannot deadlock the exporter
    # and remains available after a crash for diagnostics.
    with self._open_db(self.staging_db_path, staging=True) as staging_db:
        resumed_at = self._load_staging_progress(staging_db, fingerprint)["processed"]
        self._write_status(
            resumed=resumed_at > 0,
            resume_processed=resumed_at,
            processed=resumed_at,
            staging_database=str(self.staging_db_path),
        )
        with self.export_log_path.open("a+", encoding="utf-8") as stderr_log:
            stderr_log.write(f"\n[{utc_now()}] export start resume_after={resumed_at}\n")
            stderr_log.flush()
            try:
                idle_timeout = int(os.environ.get("SIMPLEOFFICE_OSM_EXPORT_IDLE_TIMEOUT", "1800"))
            except ValueError as exc:
                raise ValueError("invalid OSM export idle timeout") from exc
            idle_timeout = max(300, min(idle_timeout, 86400))
            process = subprocess.Popen(
                [osmium, "export", str(filtered), "-f", "geojsonseq", "--attributes=type,id", "--no-progress"],
                stdout=subprocess.PIPE, stderr=stderr_log, text=True, encoding="utf-8",
            )
            assert process.stdout is not None
            timed_out = threading.Event()
            watchdog_stop = threading.Event()
            last_output = [time.monotonic()]

            def watch_export() -> None:
                while not watchdog_stop.wait(min(30, max(1, idle_timeout // 10))):
                    if process.poll() is None and time.monotonic() - last_output[0] >= idle_timeout:
                        timed_out.set()
                        try:
                            process.kill()
                        except OSError:
                            pass
                        return

            def observed_lines() -> Any:
                for line in process.stdout:
                    last_output[0] = time.monotonic()
                    yield line

            watchdog = threading.Thread(target=watch_export, name="osm-export-watchdog", daemon=True)
            watchdog.start()
            try:
                stats = self._import_geojson_lines_resumable(
                    staging_db, observed_lines(), fingerprint, progress=report
                )
                last_progress = 0.0
                report(stats)
                rc = process.wait(timeout=60)
                stderr_log.flush()
                stderr_log.seek(0, os.SEEK_END)
                stderr_log.seek(max(0, stderr_log.tell() - 4000))
                stderr = stderr_log.read()
                if timed_out.is_set():
                    raise subprocess.TimeoutExpired(
                        process.args, idle_timeout, stderr=stderr[-4000:]
                    )
                if rc:
                    raise RuntimeError(f"osmium export failed: {stderr[-500:]}")
                accepted = stats["inserted"] + stats["updated"] + stats["duplicates"]
                if accepted >= 10_000 and stats["stored"] < accepted // 2:
                    raise RuntimeError(
                        "OSM index plausibility check failed: "
                        f"processed={stats['processed']} accepted={accepted} stored={stats['stored']}"
                    )
                self._write_build_status(
                    export_complete=True,
                    export_completed_at=utc_now(),
                    imported_processed=stats["processed"],
                    imported_stored=stats["stored"],
                )
            finally:
                watchdog_stop.set()
                watchdog.join(timeout=2)
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
    # Publish only a complete and plausible staging database. The final
    # copy is one transaction, so readers see either the old or the complete
    # new index. An interruption rolls back the live DB and leaves staging
    # plus checkpoint available for another attempt.
    return publish(stats, resumed_at)
