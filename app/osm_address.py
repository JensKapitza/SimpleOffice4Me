"""Local OpenStreetMap address index for CRM address completion.

No address entered by a user is sent to an external geocoder. Administrators may
explicitly download a Geofabrik OSM PBF extract and build a compact SQLite index
using the local ``osmium`` command line utility.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

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
        self.download_lock = self.data_dir / ".download.lock"

    def _db(self) -> sqlite3.Connection:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
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
            CREATE INDEX IF NOT EXISTS address_postal ON address(postal);
            CREATE INDEX IF NOT EXISTS address_city ON address(city COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS address_street ON address(street COLLATE NOCASE);
            """
        )
        return db

    def status(self) -> dict[str, Any]:
        status: dict[str, Any] = {"ready": False, "count": 0, "osmium": bool(shutil.which("osmium"))}
        try:
            if self.status_path.is_file():
                loaded = json.loads(self.status_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    status.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
        if self.db_path.is_file():
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
        return status

    def _write_status(self, **values: Any) -> None:
        current = self.status()
        current.update(values)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.status_path)

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
            with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed allow-listed host
                size = int(response.headers.get("Content-Length") or 0)
                modified = str(response.headers.get("Last-Modified") or "")
        except (OSError, ValueError):
            try:
                request = Request(url, headers={**headers, "Range": "bytes=0-0"})
                with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed allow-listed host
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
                    with urlopen(request, timeout=read_timeout) as response:  # noqa: S310 - fixed allow-listed host
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

    def build(self, source: str | Path) -> int:
        source = Path(source).resolve()
        if not source.is_file() or source.suffix.casefold() != ".pbf":
            raise ValueError("a regular .osm.pbf extract is required")
        osmium = shutil.which("osmium")
        if not osmium:
            raise RuntimeError("osmium is required to build the local address index")
        self._write_status(state="indexing", source_file=str(source), indexed_at="", error="")
        count = 0
        with tempfile.TemporaryDirectory(prefix="simpleoffice-osm-") as temporary_dir:
            filtered = Path(temporary_dir) / "addresses.osm.pbf"
            subprocess.run(
                [osmium, "tags-filter", str(source), "nwr/addr:housenumber", "nwr/addr:street", "nwr/addr:postcode", "nwr/addr:city", "-o", str(filtered), "--overwrite"],
                check=True, timeout=7200, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
            process = subprocess.Popen(
                [osmium, "export", str(filtered), "-f", "geojsonseq"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            )
            assert process.stdout is not None
            with self._db() as db:
                db.execute("DELETE FROM address")
                rows: list[tuple[str, ...]] = []
                for line in process.stdout:
                    line = line.strip().lstrip("\x1e")
                    if not line:
                        continue
                    try:
                        feature = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
                    street = _clean(properties.get("addr:street") or properties.get("addr:place"))
                    house = _clean(properties.get("addr:housenumber"), 80)
                    postal = _clean(properties.get("addr:postcode"), 40)
                    city = _city(properties)
                    if not (street and (house or postal or city)):
                        continue
                    country = _clean(properties.get("addr:country"), 10).upper() or "DE"
                    state = _clean(properties.get("addr:state"))
                    lat, lon = self._geometry_center(feature)
                    feature_id = _clean(feature.get("id"), 120)
                    if "/" in feature_id:
                        osm_type, osm_id = feature_id.split("/", 1)
                    else:
                        osm_type, osm_id = "osm", feature_id or str(count + 1)
                    normalized = _normal(f"{street} {house} {postal} {city} {country}")
                    rows.append((street, house, postal, city, country, state, lat, lon, osm_type, osm_id, normalized))
                    if len(rows) >= 2000:
                        db.executemany("INSERT OR REPLACE INTO address(street,house_number,postal,city,country,state,lat,lon,osm_type,osm_id,normalized) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
                        count += len(rows)
                        rows.clear()
                if rows:
                    db.executemany("INSERT OR REPLACE INTO address(street,house_number,postal,city,country,state,lat,lon,osm_type,osm_id,normalized) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
                    count += len(rows)
            stderr = process.stderr.read() if process.stderr else ""
            rc = process.wait(timeout=60)
            if rc:
                raise RuntimeError(f"osmium export failed: {stderr[-500:]}")
        self._write_status(state="ready", ready=True, count=count, indexed_at=utc_now(), error="")
        return count

    def search(self, query: str, *, country_code: str = "de", limit: int = 5) -> list[dict[str, Any]]:
        query = _clean(query, 500)
        if len(query) < 3 or not self.db_path.is_file():
            return []
        tokens = [token for token in _normal(query).split() if len(token) >= 2][:8]
        if not tokens:
            return []
        country = _clean(country_code, 8).upper()
        maximum = max(1, min(int(limit), 20))

        def select(db: sqlite3.Connection, selected: list[str]) -> list[sqlite3.Row]:
            where = " AND ".join("normalized LIKE ?" for _ in selected)
            params: list[Any] = [f"%{token}%" for token in selected]
            if country:
                where += " AND country = ?"
                params.append(country)
            params.append(maximum)
            return db.execute(
                f"SELECT * FROM address WHERE {where} ORDER BY CASE WHEN postal <> '' THEN 0 ELSE 1 END, city COLLATE NOCASE, street COLLATE NOCASE, house_number LIMIT ?",
                params,
            ).fetchall()

        fallback = False
        with self._db() as db:
            rows = select(db, tokens)
            # OSM address nodes frequently omit addr:city although street,
            # house number and postcode are present. Retry by omitting exactly
            # one textual token, but only accept records whose city is missing.
            if not rows and len(tokens) >= 3 and any(token.isdigit() for token in tokens):
                for omitted in sorted((token for token in tokens if not token.isdigit()), key=len):
                    reduced = list(tokens)
                    reduced.remove(omitted)
                    if not any(token.isdigit() for token in reduced) or not any(not token.isdigit() for token in reduced):
                        continue
                    rows = [row for row in select(db, reduced) if not row["city"]]
                    if rows:
                        fallback = True
                        break
        return [
            {
                "street": _clean(f"{row['street']} {row['house_number']}"),
                "postal": row["postal"],
                "city": row["city"],
                "country": row["country"],
                "country_name": "Deutschland" if row["country"] == "DE" else row["country"],
                "state": row["state"],
                "display_name": _clean(f"{row['street']} {row['house_number']}, {row['postal']} {row['city']}, {row['country']}"),
                "lat": row["lat"],
                "lon": row["lon"],
                "osm_type": row["osm_type"],
                "osm_id": row["osm_id"],
                "match_quality": "fallback" if fallback else "exact",
            }
            for row in rows
        ]


def search_address(query: str, *, root: str | Path, country_code: str = "de", limit: int = 5) -> list[dict[str, Any]]:
    return LocalAddressIndex(root).search(query, country_code=country_code, limit=limit)


def unique_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(candidates) != 1:
        return None
    item = candidates[0]
    if item.get("match_quality") == "fallback":
        return None
    if item.get("street") and item.get("city") and (item.get("postal") or item.get("country")):
        return item
    return None
