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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .document_store import CONTROL_DIR, utc_now


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


class LocalAddressIndex:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.control = self.root / CONTROL_DIR
        self.data_dir = self.control / "osm-addresses"
        self.db_path = self.data_dir / "addresses.sqlite3"
        self.status_path = self.data_dir / "status.json"

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
        return status

    def _write_status(self, **values: Any) -> None:
        current = self.status()
        current.update(values)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.status_path)

    def download_region(self, region: str) -> Path:
        if region not in GEOFABRIK_REGIONS:
            raise ValueError("unknown OSM region")
        label, url = GEOFABRIK_REGIONS[region]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "download.geofabrik.de":
            raise ValueError("OSM source must be an approved Geofabrik HTTPS URL")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        target = self.data_dir / f"{region}-latest.osm.pbf"
        temporary = target.with_suffix(".download")
        request = Request(url, headers={"User-Agent": "SimpleOffice4Me/1.0 local-address-index"})
        max_bytes = int(os.environ.get("SIMPLEOFFICE_OSM_MAX_DOWNLOAD_GIB", "8")) * 1024**3
        total = 0
        self._write_status(state="downloading", region=region, region_label=label, source=url, started_at=utc_now(), error="")
        try:
            with urlopen(request, timeout=60) as response, temporary.open("wb") as output:  # noqa: S310 - fixed allow-listed host
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > max_bytes:
                        raise ValueError("OSM download exceeds configured size limit")
                    output.write(block)
            if total < 1024:
                raise RuntimeError("downloaded OSM extract is unexpectedly small")
            temporary.replace(target)
            self._write_status(state="downloaded", downloaded_at=utc_now(), downloaded_bytes=total, source_file=str(target))
            return target
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self._write_status(state="error", error=str(exc)[:500])
            raise

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
                        count += len(rows); rows.clear()
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
        where = " AND ".join("normalized LIKE ?" for _ in tokens)
        params: list[Any] = [f"%{token}%" for token in tokens]
        country = _clean(country_code, 8).upper()
        if country:
            where += " AND country = ?"
            params.append(country)
        params.append(max(1, min(int(limit), 20)))
        with self._db() as db:
            rows = db.execute(
                f"SELECT * FROM address WHERE {where} ORDER BY CASE WHEN postal <> '' THEN 0 ELSE 1 END, city COLLATE NOCASE, street COLLATE NOCASE, house_number LIMIT ?",
                params,
            ).fetchall()
        return [
            {
                "street": _clean(f"{row['street']} {row['house_number']}"), "postal": row["postal"],
                "city": row["city"], "country": row["country"], "country_name": "Deutschland" if row["country"] == "DE" else row["country"],
                "state": row["state"], "display_name": _clean(f"{row['street']} {row['house_number']}, {row['postal']} {row['city']}, {row['country']}"),
                "lat": row["lat"], "lon": row["lon"], "osm_type": row["osm_type"], "osm_id": row["osm_id"],
            }
            for row in rows
        ]


def search_address(query: str, *, root: str | Path, country_code: str = "de", limit: int = 5) -> list[dict[str, Any]]:
    return LocalAddressIndex(root).search(query, country_code=country_code, limit=limit)


def unique_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(candidates) != 1:
        return None
    item = candidates[0]
    if item.get("street") and item.get("city") and (item.get("postal") or item.get("country")):
        return item
    return None
