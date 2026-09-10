"""Local OpenStreetMap address index storage and import core.

No address entered by a user is sent to an external geocoder. Administrators may
explicitly download a Geofabrik OSM PBF extract and build a compact SQLite index
using the local ``osmium`` command line utility.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, build_opener

from .document_store import CONTROL_DIR, utc_now
from .file_lock import exclusive_file_lock


GEOFABRIK_REGIONS = {
    "germany": ("Deutschland", "https://download.geofabrik.de/europe/germany-latest.osm.pbf"),
    "baden-wuerttemberg": ("Baden-Württemberg", "https://download.geofabrik.de/europe/germany/baden-wuerttemberg-latest.osm.pbf"),
    "bayern": ("Bayern", "https://download.geofabrik.de/europe/germany/bayern-latest.osm.pbf"),
    "berlin": ("Berlin", "https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf"),
    "brandenburg": ("Brandenburg", "https://download.geofabrik.de/europe/germany/brandenburg-latest.osm.pbf"),
    "bremen": ("Bremen", "https://download.geofabrik.de/europe/germany/bremen-latest.osm.pbf"),
    "hamburg": ("Hamburg", "https://download.geofabrik.de/europe/germany/hamburg-latest.osm.pbf"),
    "hessen": ("Hessen", "https://download.geofabrik.de/europe/germany/hessen-latest.osm.pbf"),
    "mecklenburg-vorpommern": ("Mecklenburg-Vorpommern", "https://download.geofabrik.de/europe/germany/mecklenburg-vorpommern-latest.osm.pbf"),
    "niedersachsen": ("Niedersachsen", "https://download.geofabrik.de/europe/germany/niedersachsen-latest.osm.pbf"),
    "nordrhein-westfalen": ("Nordrhein-Westfalen", "https://download.geofabrik.de/europe/germany/nordrhein-westfalen-latest.osm.pbf"),
    "rheinland-pfalz": ("Rheinland-Pfalz", "https://download.geofabrik.de/europe/germany/rheinland-pfalz-latest.osm.pbf"),
    "saarland": ("Saarland", "https://download.geofabrik.de/europe/germany/saarland-latest.osm.pbf"),
    "sachsen": ("Sachsen", "https://download.geofabrik.de/europe/germany/sachsen-latest.osm.pbf"),
    "sachsen-anhalt": ("Sachsen-Anhalt", "https://download.geofabrik.de/europe/germany/sachsen-anhalt-latest.osm.pbf"),
    "schleswig-holstein": ("Schleswig-Holstein", "https://download.geofabrik.de/europe/germany/schleswig-holstein-latest.osm.pbf"),
    "thueringen": ("Thüringen", "https://download.geofabrik.de/europe/germany/thueringen-latest.osm.pbf"),
}


def _open_geofabrik(request: Request, timeout: float):
    parsed = urlparse(request.full_url)
    if parsed.scheme != "https" or parsed.hostname != "download.geofabrik.de" or parsed.username or parsed.password:
        raise ValueError("OSM request must target approved Geofabrik HTTPS host")
    response = build_opener().open(request, timeout=timeout)
    final = urlparse(response.geturl())
    if final.scheme != "https" or final.hostname != "download.geofabrik.de" or final.username or final.password:
        response.close()
        raise ValueError("OSM redirect left approved Geofabrik HTTPS host")
    return response


def _clean(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _normal(value: str) -> str:
    value = _clean(value, 1200).casefold()
    value = value.replace("straße", "strasse").replace("str.", "strasse")
    return " ".join(re.sub(r"[^0-9a-zäöüß]+", " ", value).split())


def _city(properties: dict[str, Any]) -> str:
    return _clean(
        properties.get("addr:city") or properties.get("addr:place")
        or properties.get("addr:suburb") or properties.get("addr:district")
    )


def human_bytes(value: Any) -> str:
    try:
        size = max(0, int(value or 0))
    except (TypeError, ValueError):
        return "unbekannt"
    if not size:
        return "unbekannt"
    amount = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit in {"B", "KiB"} else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{size} B"


def _remote_total(headers: Any, resume_from: int = 0) -> int:
    content_range = str(headers.get("Content-Range") or "")
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    try:
        length = int(headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return 0
    return resume_from + length if resume_from else length


class LocalAddressIndex:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.control = self.root / CONTROL_DIR
        self.data_dir = self.control / "osm-addresses"
        self.db_path = self.data_dir / "addresses.sqlite3"
        self.status_path = self.data_dir / "status.json"
        self.build_dir = self.data_dir / "build"
        self.build_status_path = self.build_dir / "checkpoint.json"
        self.filtered_path = self.build_dir / "addresses.filtered.osm.pbf"
        self.staging_db_path = self.build_dir / "addresses.staging.sqlite3"
        self.filter_log_path = self.build_dir / "osmium-filter.log"
        self.export_log_path = self.build_dir / "osmium-export.log"
        self.download_lock = self.data_dir / ".download.lock"
        self.build_lock = self.data_dir / ".build.lock"

    def downloaded_source(self) -> Path | None:
        """Return a downloaded extract, never an arbitrary path from status.json."""
        candidates: list[Path] = []
        try:
            loaded = json.loads(self.status_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("source_file"):
                candidates.append(Path(str(loaded["source_file"])))
        except (OSError, json.JSONDecodeError):
            pass
        if self.data_dir.is_dir():
            candidates.extend(sorted(self.data_dir.glob("*-latest.osm.pbf"), key=lambda path: path.stat().st_mtime, reverse=True))
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                resolved.relative_to(self.data_dir.resolve())
            except (OSError, ValueError):
                continue
            if resolved.is_file() and resolved.name.endswith(".osm.pbf"):
                return resolved
        return None

    def needs_reindex(self, source: str | Path | None = None) -> bool:
        selected = Path(source).resolve() if source else self.downloaded_source()
        if selected is None or not selected.is_file():
            return False
        try:
            build_status = self._build_status()
            if (
                build_status.get("source_fingerprint") == self._source_fingerprint(selected)
                and build_status.get("build_started_at")
                and not build_status.get("completed_at")
            ):
                return True
        except OSError:
            pass
        if not self.db_path.is_file() or not self.status().get("ready"):
            return True
        return selected.stat().st_mtime_ns > self.db_path.stat().st_mtime_ns

    def _db(self) -> sqlite3.Connection:
        return self._open_db(self.db_path, staging=False)

    def _open_db(self, path: Path, *, staging: bool) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA journal_mode={'DELETE' if staging else 'WAL'}")
        db.execute("PRAGMA synchronous=NORMAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS address (
                id INTEGER PRIMARY KEY,
                street TEXT NOT NULL,
                house_number TEXT NOT NULL,
                postal TEXT NOT NULL,
                city TEXT NOT NULL,
                country TEXT NOT NULL,
                state TEXT NOT NULL,
                lat TEXT NOT NULL,
                lon TEXT NOT NULL,
                osm_type TEXT NOT NULL,
                osm_id TEXT NOT NULL,
                normalized TEXT NOT NULL,
                UNIQUE(osm_type, osm_id)
            );
            """
        )
        if not staging:
            self._create_search_indexes(db)
        if staging:
            db.execute(
                """CREATE TABLE IF NOT EXISTS build_progress (
                       singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                       source_fingerprint TEXT NOT NULL,
                       stats_json TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
        db.commit()
        return db

    @staticmethod
    def _create_search_indexes(db: sqlite3.Connection) -> None:
        db.execute("CREATE INDEX IF NOT EXISTS address_postal ON address(postal)")
        db.execute("CREATE INDEX IF NOT EXISTS address_city ON address(city COLLATE NOCASE)")
        db.execute("CREATE INDEX IF NOT EXISTS address_street ON address(street COLLATE NOCASE)")

    def status(self) -> dict[str, Any]:
        status: dict[str, Any] = {"ready": False, "count": 0, "osmium": bool(shutil.which("osmium"))}
        status.update(self._stored_status())
        status["document_root"] = str(self.root)
        status["database_path"] = str(self.db_path)
        status["filter_log"] = str(self.filter_log_path) if self.filter_log_path.is_file() else ""
        status["export_log"] = str(self.export_log_path) if self.export_log_path.is_file() else ""
        if status.get("state") == "indexing" and status.get("phase_started_at"):
            try:
                phase_started = datetime.fromisoformat(str(status["phase_started_at"]).replace("Z", "+00:00"))
                if phase_started.tzinfo is None:
                    phase_started = phase_started.replace(tzinfo=timezone.utc)
                status["phase_elapsed_seconds"] = max(0, round((datetime.now(timezone.utc) - phase_started).total_seconds()))
            except ValueError:
                pass
        # Do not scan a many-million-row table from an HTTP request while the
        # writer is rebuilding it. The completed status always contains the
        # actual SELECT COUNT(*) result calculated inside the import transaction.
        active_states = {"downloading", "resuming", "retrying", "indexing"}
        if self.db_path.is_file() and status.get("state") not in active_states:
            try:
                with self._db() as db:
                    status["count"] = int(db.execute("SELECT COUNT(*) FROM address").fetchone()[0])
                status["ready"] = status["count"] > 0
            except sqlite3.Error:
                status["ready"] = False
        expected = int(status.get("expected_bytes", 0) or 0)
        downloaded = int(status.get("downloaded_bytes", 0) or 0)
        status["expected_size"] = human_bytes(expected)
        status["downloaded_size"] = human_bytes(downloaded)
        if expected > 0:
            status["progress_percent"] = min(100, round(downloaded * 100 / expected, 1))
        source = self.downloaded_source()
        status["source_available"] = source is not None
        status["source_file"] = str(source) if source else ""
        build_status = self._build_status()
        status["resume_available"] = bool(
            source
            and build_status.get("build_started_at")
            and not build_status.get("completed_at")
            and (
                self.filtered_path.is_file()
                or self.staging_db_path.is_file()
            )
        )
        status["filtered_checkpoint_available"] = self.filtered_path.is_file()
        status["staging_checkpoint_available"] = self.staging_db_path.is_file()
        return status

    def _write_status(self, **values: Any) -> None:
        # Status writes must stay O(1). Calling status() here used to execute a
        # full COUNT(*) and made the worker appear stuck after a large import.
        current = self._stored_status()
        current.update(values)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.status_path)

    def _stored_status(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.status_path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _build_status(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.build_status_path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_build_status(self, **values: Any) -> None:
        current = self._build_status()
        current.update(values)
        self.build_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.build_status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.build_status_path)

    @staticmethod
    def _source_fingerprint(source: Path) -> str:
        stat = source.stat()
        identity = f"{source.resolve()}\x1f{stat.st_size}\x1f{stat.st_mtime_ns}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def _discard_stale_build(self) -> None:
        """Remove only resumable build artifacts, never the active index/download."""
        for path in (
            self.filtered_path,
            self.filtered_path.with_suffix(self.filtered_path.suffix + ".part"),
            self.staging_db_path,
            Path(str(self.staging_db_path) + "-journal"),
            Path(str(self.staging_db_path) + "-wal"),
            Path(str(self.staging_db_path) + "-shm"),
            self.filter_log_path,
            self.export_log_path,
            self.build_status_path,
        ):
            path.unlink(missing_ok=True)

    @staticmethod
    def _source(region: str) -> tuple[str, str]:
        if region not in GEOFABRIK_REGIONS:
            raise ValueError("unknown OSM region")
        label, url = GEOFABRIK_REGIONS[region]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "download.geofabrik.de":
            raise ValueError("OSM source must be an approved Geofabrik HTTPS URL")
        return label, url

    def region_info(self, region: str) -> dict[str, Any]:
        label, url = self._source(region)
        headers = {"User-Agent": "SimpleOffice4Me/1.0 local-address-index"}
        size = 0
        modified = ""
        try:
            request = Request(url, headers=headers, method="HEAD")
            with _open_geofabrik(request, timeout=20) as response:
                size = int(response.headers.get("Content-Length") or 0)
                modified = str(response.headers.get("Last-Modified") or "")
        except (OSError, ValueError):
            try:
                request = Request(url, headers={**headers, "Range": "bytes=0-0"})
                with _open_geofabrik(request, timeout=20) as response:
                    size = _remote_total(response.headers)
                    modified = str(response.headers.get("Last-Modified") or "")
            except (OSError, ValueError):
                size = 0
        return {"region": region, "label": label, "url": url, "bytes": size, "size": human_bytes(size), "last_modified": modified}

    def download_region(self, region: str) -> Path:
        label, url = self._source(region)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        target = self.data_dir / f"{region}-latest.osm.pbf"
        partial = self.data_dir / f"{region}-latest.osm.pbf.part"
        legacy_partial = target.with_suffix(".download")
        try:
            max_bytes = int(os.environ.get("SIMPLEOFFICE_OSM_MAX_DOWNLOAD_GIB", "8")) * 1024**3
            read_timeout = max(30, int(os.environ.get("SIMPLEOFFICE_OSM_READ_TIMEOUT_SECONDS", "300")))
            max_attempts = max(1, min(10, int(os.environ.get("SIMPLEOFFICE_OSM_DOWNLOAD_RETRIES", "5"))))
        except ValueError as exc:
            raise ValueError("invalid OSM download configuration") from exc

        with exclusive_file_lock(self.download_lock):
            if legacy_partial.is_file() and not partial.exists():
                legacy_partial.replace(partial)

            info = self.region_info(region)
            expected = int(info.get("bytes", 0) or 0)
            if expected > max_bytes:
                raise ValueError("OSM download exceeds configured size limit")
            total = partial.stat().st_size if partial.is_file() else 0
            if total > max_bytes:
                partial.unlink(missing_ok=True)
                total = 0

            self._write_status(
                state="downloading" if total == 0 else "resuming",
                region=region,
                region_label=label,
                source=url,
                started_at=utc_now(),
                error="",
                downloaded_bytes=total,
                expected_bytes=expected,
                progress_percent=min(100, round(total * 100 / expected, 1)) if expected else None,
                download_attempt=0,
                download_attempts=max_attempts,
            )

            last_error: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                resume_from = partial.stat().st_size if partial.is_file() else 0
                headers = {"User-Agent": "SimpleOffice4Me/1.0 local-address-index"}
                if resume_from:
                    headers["Range"] = f"bytes={resume_from}-"
                    if info.get("last_modified"):
                        headers["If-Range"] = str(info["last_modified"])
                request = Request(url, headers=headers)
                try:
                    with _open_geofabrik(request, timeout=read_timeout) as response:
                        status_code = int(getattr(response, "status", response.getcode()) or 0)
                        can_resume = bool(resume_from and status_code == 206)
                        if resume_from and not can_resume:
                            resume_from = 0
                        total = resume_from
                        response_total = _remote_total(response.headers, resume_from if can_resume else 0)
                        if response_total:
                            expected = response_total
                        if expected > max_bytes:
                            raise ValueError("OSM download exceeds configured size limit")
                        mode = "ab" if can_resume else "wb"
                        next_status = total + 8 * 1024 * 1024
                        with partial.open(mode) as output:
                            while True:
                                block = response.read(1024 * 1024)
                                if not block:
                                    break
                                total += len(block)
                                if total > max_bytes:
                                    raise ValueError("OSM download exceeds configured size limit")
                                output.write(block)
                                if total >= next_status:
                                    self._write_status(
                                        state="downloading",
                                        downloaded_bytes=total,
                                        expected_bytes=expected,
                                        progress_percent=min(100, round(total * 100 / expected, 1)) if expected else None,
                                        download_attempt=attempt,
                                        download_attempts=max_attempts,
                                    )
                                    next_status = total + 8 * 1024 * 1024

                    if expected and total < expected:
                        raise TimeoutError(f"OSM download ended early at {total} of {expected} bytes")
                    if total < 1024:
                        raise RuntimeError("downloaded OSM extract is unexpectedly small")
                    if not partial.is_file():
                        raise RuntimeError("OSM partial download disappeared unexpectedly")
                    partial.replace(target)
                    self._write_status(
                        state="downloaded",
                        downloaded_at=utc_now(),
                        downloaded_bytes=total,
                        expected_bytes=expected or total,
                        progress_percent=100,
                        source_file=str(target),
                        download_attempt=attempt,
                        download_attempts=max_attempts,
                        error="",
                    )
                    return target
                except (TimeoutError, OSError) as exc:
                    last_error = exc
                    total = partial.stat().st_size if partial.is_file() else 0
                    self._write_status(
                        state="retrying" if attempt < max_attempts else "error",
                        downloaded_bytes=total,
                        expected_bytes=expected,
                        progress_percent=min(100, round(total * 100 / expected, 1)) if expected else None,
                        download_attempt=attempt,
                        download_attempts=max_attempts,
                        error=str(exc)[:500],
                    )
                    if attempt >= max_attempts:
                        break
                    time.sleep(min(2 ** (attempt - 1), 10))
                except Exception as exc:
                    total = partial.stat().st_size if partial.is_file() else 0
                    self._write_status(
                        state="error",
                        downloaded_bytes=total,
                        expected_bytes=expected,
                        progress_percent=min(100, round(total * 100 / expected, 1)) if expected else None,
                        download_attempt=attempt,
                        download_attempts=max_attempts,
                        error=str(exc)[:500],
                    )
                    raise

            if last_error is not None:
                raise last_error
            raise RuntimeError("OSM download failed")

    @staticmethod
    def _geometry_center(feature: dict[str, Any]) -> tuple[str, str]:
        geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
        coordinates = geometry.get("coordinates")
        if geometry.get("type") == "Point" and isinstance(coordinates, list) and len(coordinates) >= 2:
            return _clean(coordinates[1], 40), _clean(coordinates[0], 40)
        return "", ""

    @staticmethod
    def _feature_row(feature: dict[str, Any]) -> tuple[str, ...] | None:
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        street = _clean(properties.get("addr:street") or properties.get("addr:place"))
        house = _clean(properties.get("addr:housenumber"), 80)
        postal = _clean(properties.get("addr:postcode"), 40)
        city = _city(properties)
        if not (street and (house or postal or city)):
            return None
        country = _clean(properties.get("addr:country"), 10).upper() or "DE"
        state = _clean(properties.get("addr:state"))
        lat, lon = LocalAddressIndex._geometry_center(feature)
        feature_id = _clean(feature.get("id"), 120)
        if feature_id:
            if "/" in feature_id:
                osm_type, osm_id = feature_id.split("/", 1)
            else:
                osm_type, osm_id = "osm", feature_id
        elif properties.get("@id") is not None:
            osm_type = _clean(properties.get("@type") or "osm", 40).casefold() or "osm"
            osm_id = _clean(properties.get("@id"), 120)
        else:
            osm_type = _clean(properties.get("@type") or properties.get("osm_type") or "feature", 40).casefold() or "feature"
            identity = "\x1f".join((street, house, postal, city, country, state, lat, lon, osm_type))
            osm_id = "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        normalized = _normal(f"{street} {house} {postal} {city} {country}")
        return street, house, postal, city, country, state, lat, lon, osm_type, osm_id, normalized

    @staticmethod
    def _store_batch(db: sqlite3.Connection, rows: list[tuple[str, ...]], stats: dict[str, int]) -> None:
        if not rows:
            return
        keys = list(dict.fromkeys((row[8], row[9]) for row in rows))
        existing: dict[tuple[str, str], tuple[str, ...]] = {}
        db.execute(
            """CREATE TEMP TABLE IF NOT EXISTS requested_address_key (
                   osm_type TEXT NOT NULL,
                   osm_id TEXT NOT NULL,
                   PRIMARY KEY(osm_type, osm_id)
               )"""
        )
        for offset in range(0, len(keys), 300):
            chunk = keys[offset:offset + 300]
            db.execute("DELETE FROM requested_address_key")
            db.executemany(
                "INSERT OR IGNORE INTO requested_address_key(osm_type,osm_id) VALUES(?,?)",
                chunk,
            )
            for current in db.execute(
                """SELECT a.street,a.house_number,a.postal,a.city,a.country,a.state,
                          a.lat,a.lon,a.osm_type,a.osm_id,a.normalized
                   FROM address a
                   JOIN requested_address_key r
                     ON r.osm_type=a.osm_type AND r.osm_id=a.osm_id"""
            ):
                value = tuple(str(item) for item in current)
                existing[(value[8], value[9])] = value
        final: dict[tuple[str, str], tuple[str, ...]] = dict(existing)
        for row in rows:
            key = row[8], row[9]
            previous = final.get(key)
            if previous is None:
                stats["inserted"] += 1
            elif previous == row:
                stats["duplicates"] += 1
            elif row[9].startswith("sha256:"):
                stats["id_collisions"] += 1
                stats["rejected"] += 1
                continue
            else:
                stats["updated"] += 1
            final[key] = row
        changed = [row for key, row in final.items() if existing.get(key) != row]
        db.executemany(
            """INSERT INTO address(street,house_number,postal,city,country,state,lat,lon,osm_type,osm_id,normalized)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(osm_type,osm_id) DO UPDATE SET
                 street=excluded.street, house_number=excluded.house_number,
                 postal=excluded.postal, city=excluded.city, country=excluded.country,
                 state=excluded.state, lat=excluded.lat, lon=excluded.lon,
                 normalized=excluded.normalized""",
            changed,
        )

    def _import_geojson_lines(
        self,
        db: sqlite3.Connection,
        lines: Any,
        *,
        batch_size: int = 2000,
        progress: Callable[[dict[str, int]], None] | None = None,
    ) -> dict[str, int]:
        stats = {
            "processed": 0,
            "inserted": 0,
            "updated": 0,
            "duplicates": 0,
            "id_collisions": 0,
            "rejected": 0,
            "stored": 0,
        }
        rows: list[tuple[str, ...]] = []
        for line in lines:
            line = str(line).strip().lstrip("\x1e")
            if not line:
                continue
            stats["processed"] += 1
            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                stats["rejected"] += 1
                continue
            if not isinstance(feature, dict):
                stats["rejected"] += 1
                continue
            row = self._feature_row(feature)
            if row is None:
                stats["rejected"] += 1
                continue
            rows.append(row)
            if len(rows) >= batch_size:
                self._store_batch(db, rows, stats)
                rows.clear()
                if progress:
                    progress(dict(stats))
        self._store_batch(db, rows, stats)
        stats["stored"] = int(db.execute("SELECT COUNT(*) FROM address").fetchone()[0])
        if progress:
            progress(dict(stats))
        return stats

    @staticmethod
    def _empty_import_stats() -> dict[str, int]:
        return {
            "processed": 0,
            "inserted": 0,
            "updated": 0,
            "duplicates": 0,
            "id_collisions": 0,
            "rejected": 0,
            "stored": 0,
        }

    def _load_staging_progress(self, db: sqlite3.Connection, source_fingerprint: str) -> dict[str, int]:
        row = db.execute(
            "SELECT source_fingerprint, stats_json FROM build_progress WHERE singleton=1"
        ).fetchone()
        if row is None or str(row["source_fingerprint"]) != source_fingerprint:
            db.execute("DELETE FROM address")
            db.execute("DELETE FROM build_progress")
            db.commit()
            return self._empty_import_stats()
        try:
            loaded = json.loads(str(row["stats_json"]))
            stats = self._empty_import_stats()
            for key in stats:
                stats[key] = max(0, int(loaded.get(key, 0)))
            stats["stored"] = int(db.execute("SELECT COUNT(*) FROM address").fetchone()[0])
            return stats
        except (TypeError, ValueError, json.JSONDecodeError):
            db.execute("DELETE FROM address")
            db.execute("DELETE FROM build_progress")
            db.commit()
            return self._empty_import_stats()

    @staticmethod
    def _save_staging_progress(
        db: sqlite3.Connection,
        source_fingerprint: str,
        stats: dict[str, int],
        stream_hash: str = "",
    ) -> None:
        checkpoint = {**stats, "_stream_sha256": stream_hash}
        db.execute(
            """INSERT INTO build_progress(singleton,source_fingerprint,stats_json,updated_at)
               VALUES(1,?,?,?)
               ON CONFLICT(singleton) DO UPDATE SET
                 source_fingerprint=excluded.source_fingerprint,
                 stats_json=excluded.stats_json,
                 updated_at=excluded.updated_at""",
            (source_fingerprint, json.dumps(checkpoint, separators=(",", ":")), utc_now()),
        )

    @staticmethod
    def _staging_line_hash(db: sqlite3.Connection) -> str:
        row = db.execute("SELECT stats_json FROM build_progress WHERE singleton=1").fetchone()
        if row is None:
            return ""
        try:
            loaded = json.loads(str(row["stats_json"]))
            return str(loaded.get("_stream_sha256", ""))
        except (AttributeError, json.JSONDecodeError):
            return ""

    def _import_geojson_lines_resumable(
        self,
        db: sqlite3.Connection,
        lines: Any,
        source_fingerprint: str,
        *,
        batch_size: int = 2000,
        progress: Callable[[dict[str, int]], None] | None = None,
    ) -> dict[str, int]:
        """Import and commit bounded batches, skipping the confirmed stream prefix."""
        stats = self._load_staging_progress(db, source_fingerprint)
        resume_after = stats["processed"]
        checkpoint_hash = self._staging_line_hash(db)
        stream_hasher = hashlib.sha256()
        skipped = 0
        since_commit = 0
        rows: list[tuple[str, ...]] = []

        def commit_batch() -> None:
            nonlocal since_commit
            inserted_before = stats["inserted"]
            self._store_batch(db, rows, stats)
            rows.clear()
            stats["stored"] += stats["inserted"] - inserted_before
            self._save_staging_progress(
                db, source_fingerprint, stats, stream_hasher.hexdigest()
            )
            db.commit()
            since_commit = 0
            if progress:
                progress(dict(stats))

        for raw_line in lines:
            line = str(raw_line).strip().lstrip("\x1e")
            if not line:
                continue
            stream_hasher.update(line.encode("utf-8"))
            stream_hasher.update(b"\n")
            if skipped < resume_after:
                skipped += 1
                if skipped == resume_after:
                    replay_hash = stream_hasher.hexdigest()
                    if not checkpoint_hash or replay_hash != checkpoint_hash:
                        db.execute("DELETE FROM address")
                        db.execute("DELETE FROM build_progress")
                        db.commit()
                        raise RuntimeError(
                            "OSM resume stream changed at checkpoint; staging was reset"
                        )
                if progress and skipped % batch_size == 0:
                    progress({**stats, "replayed": skipped, "replay_target": resume_after})
                continue
            stats["processed"] += 1
            since_commit += 1
            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                stats["rejected"] += 1
            else:
                if not isinstance(feature, dict):
                    stats["rejected"] += 1
                else:
                    row = self._feature_row(feature)
                    if row is None:
                        stats["rejected"] += 1
                    else:
                        rows.append(row)
            if since_commit >= batch_size:
                commit_batch()
        if skipped < resume_after:
            raise RuntimeError(
                f"OSM export ended before checkpoint: replayed={skipped} expected={resume_after}"
            )
        if since_commit or not resume_after:
            commit_batch()
        elif progress:
            progress(dict(stats))
        stats["stored"] = int(db.execute("SELECT COUNT(*) FROM address").fetchone()[0])
        self._save_staging_progress(
            db, source_fingerprint, stats, stream_hasher.hexdigest()
        )
        db.commit()
        return stats

    def _promote_staging_index(self, expected_count: int) -> int:
        """Publish staging in one live-DB transaction while readers keep the old snapshot."""
        if not self.staging_db_path.is_file():
            raise RuntimeError("OSM staging database disappeared before publication")
        with self._db() as db:
            db.execute("ATTACH DATABASE ? AS osm_staging", (str(self.staging_db_path),))
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DROP INDEX IF EXISTS main.address_postal")
                db.execute("DROP INDEX IF EXISTS main.address_city")
                db.execute("DROP INDEX IF EXISTS main.address_street")
                db.execute("DELETE FROM main.address")
                db.execute(
                    """INSERT INTO main.address(
                           street,house_number,postal,city,country,state,lat,lon,
                           osm_type,osm_id,normalized
                       )
                       SELECT street,house_number,postal,city,country,state,lat,lon,
                              osm_type,osm_id,normalized
                       FROM osm_staging.address"""
                )
                stored = int(db.execute("SELECT COUNT(*) FROM main.address").fetchone()[0])
                if stored != expected_count:
                    raise RuntimeError(
                        f"OSM publication count mismatch: staging={expected_count} live={stored}"
                    )
                self._create_search_indexes(db)
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.execute("DETACH DATABASE osm_staging")
        return stored

    def _promote_city_staging_index(self, city: str, expected_count: int) -> int:
        """Replace one city's rows atomically while all other cities stay live."""
        with self._open_db(self.staging_db_path, staging=True) as staging:
            selected = int(staging.execute("SELECT COUNT(*) FROM address").fetchone()[0])
        if selected != expected_count:
            raise RuntimeError(
                f"OSM city staging count mismatch: expected={expected_count} stored={selected}"
            )
        with self._db() as db:
            db.execute("ATTACH DATABASE ? AS osm_staging", (str(self.staging_db_path),))
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM main.address WHERE city = ? COLLATE NOCASE", (city,))
                db.execute(
                    """INSERT INTO main.address(
                           street,house_number,postal,city,country,state,lat,lon,
                           osm_type,osm_id,normalized
                       )
                       SELECT street,house_number,postal,city,country,state,lat,lon,
                              osm_type,osm_id,normalized
                       FROM osm_staging.address
                       WHERE 1
                       ON CONFLICT(osm_type,osm_id) DO UPDATE SET
                         street=excluded.street, house_number=excluded.house_number,
                         postal=excluded.postal, city=excluded.city,
                         country=excluded.country, state=excluded.state,
                         lat=excluded.lat, lon=excluded.lon,
                         normalized=excluded.normalized"""
                )
                stored = int(db.execute("SELECT COUNT(*) FROM main.address").fetchone()[0])
                self._create_search_indexes(db)
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.execute("DETACH DATABASE osm_staging")
        return stored
