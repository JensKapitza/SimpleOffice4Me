"""Dependency-free OpenStreetMap PBF reader and address-index builder.

The parser intentionally implements only the standard OSM PBF wire fields that
SimpleOffice needs.  It streams one independently compressed file block at a
time and never materializes a complete Geofabrik extract in memory.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from .document_store import utc_now
from .osm_address import LocalAddressIndex, human_bytes


PARSER_VERSION = "python-pbf-v1"
MAX_BLOB_HEADER = 64 * 1024
MAX_BLOB_BYTES = 32 * 1024 * 1024
MAX_RAW_BLOB_BYTES = 32 * 1024 * 1024
SUPPORTED_REQUIRED_FEATURES = {"OsmSchema-V0.6", "DenseNodes", "HistoricalInformation"}
ADDRESS_KEYS = {
    "addr:street",
    "addr:place",
    "addr:housenumber",
    "addr:postcode",
    "addr:city",
    "addr:suburb",
    "addr:district",
    "addr:country",
    "addr:state",
}
ADDRESS_TRIGGER_KEYS = {"addr:housenumber", "addr:street", "addr:postcode", "addr:city"}
_ADDRESS_KEY_BYTES = {value.encode("utf-8") for value in ADDRESS_KEYS}


class PbfFormatError(RuntimeError):
    """Raised for malformed or unsupported OSM PBF input."""


@dataclass(frozen=True)
class PbfBlock:
    block_type: str
    data: bytes
    offset: int
    next_offset: int


@dataclass(frozen=True)
class PbfObject:
    osm_type: str
    osm_id: int
    tags: dict[str, str]
    lat: str = ""
    lon: str = ""


def _read_exact(handle: Any, size: int, description: str) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise PbfFormatError(f"OSM PBF ended while reading {description}")
    return data


def _read_varint(data: bytes | memoryview, position: int, end: int | None = None) -> tuple[int, int]:
    limit = len(data) if end is None else min(end, len(data))
    value = 0
    shift = 0
    start = position
    while position < limit and shift < 70:
        byte = int(data[position])
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
        shift += 7
    if position >= limit:
        raise PbfFormatError("truncated protobuf varint")
    raise PbfFormatError(f"protobuf varint is too long at offset {start}")


def _zigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _signed_int64(value: int) -> int:
    value &= (1 << 64) - 1
    return value - (1 << 64) if value & (1 << 63) else value


def _fields(data: bytes | memoryview) -> Iterator[tuple[int, int, int | memoryview]]:
    view = data if isinstance(data, memoryview) else memoryview(data)
    position = 0
    end = len(view)
    while position < end:
        key, position = _read_varint(view, position, end)
        number = key >> 3
        wire = key & 7
        if number <= 0:
            raise PbfFormatError("protobuf field number must be positive")
        if wire == 0:
            value, position = _read_varint(view, position, end)
            yield number, wire, value
        elif wire == 1:
            if position + 8 > end:
                raise PbfFormatError("truncated protobuf fixed64 field")
            value = view[position:position + 8]
            position += 8
            yield number, wire, value
        elif wire == 2:
            length, position = _read_varint(view, position, end)
            if length < 0 or position + length > end:
                raise PbfFormatError("truncated protobuf length-delimited field")
            value = view[position:position + length]
            position += length
            yield number, wire, value
        elif wire == 5:
            if position + 4 > end:
                raise PbfFormatError("truncated protobuf fixed32 field")
            value = view[position:position + 4]
            position += 4
            yield number, wire, value
        else:
            raise PbfFormatError(f"unsupported protobuf wire type {wire}")


def _packed_unsigned(value: int | memoryview, wire: int) -> list[int]:
    if wire == 0:
        return [int(value)]
    if wire != 2 or not isinstance(value, memoryview):
        raise PbfFormatError("invalid repeated integer encoding")
    result: list[int] = []
    position = 0
    while position < len(value):
        item, position = _read_varint(value, position)
        result.append(item)
    return result


def _packed_signed(value: int | memoryview, wire: int) -> list[int]:
    return [_zigzag(item) for item in _packed_unsigned(value, wire)]


def _blob_header(data: bytes) -> tuple[str, int]:
    block_type = ""
    data_size = 0
    for number, wire, value in _fields(data):
        if number == 1 and wire == 2 and isinstance(value, memoryview):
            block_type = bytes(value).decode("ascii", "strict")
        elif number == 3 and wire == 0:
            data_size = int(value)
    if not block_type or data_size <= 0 or data_size > MAX_BLOB_BYTES:
        raise PbfFormatError("invalid OSM PBF BlobHeader")
    return block_type, data_size


def _blob_payload(data: bytes) -> bytes:
    raw: bytes | None = None
    zlib_data: bytes | None = None
    raw_size = 0
    unsupported = ""
    for number, wire, value in _fields(data):
        if number == 1 and wire == 2 and isinstance(value, memoryview):
            raw = bytes(value)
        elif number == 2 and wire == 0:
            raw_size = int(value)
        elif number == 3 and wire == 2 and isinstance(value, memoryview):
            zlib_data = bytes(value)
        elif number in {4, 5, 6, 7} and wire == 2:
            unsupported = {4: "LZMA", 5: "bzip2", 6: "LZ4", 7: "ZSTD"}[number]
    if raw is not None:
        if len(raw) > MAX_RAW_BLOB_BYTES:
            raise PbfFormatError("OSM PBF raw block exceeds 32 MiB")
        return raw
    if zlib_data is not None:
        if raw_size <= 0 or raw_size > MAX_RAW_BLOB_BYTES:
            raise PbfFormatError("OSM PBF zlib block has invalid raw_size")
        try:
            payload = zlib.decompress(zlib_data)
        except zlib.error as exc:
            raise PbfFormatError("OSM PBF zlib block is corrupt") from exc
        if len(payload) != raw_size:
            raise PbfFormatError(
                f"OSM PBF zlib size mismatch: expected {raw_size}, got {len(payload)}"
            )
        return payload
    if unsupported:
        raise PbfFormatError(
            f"OSM PBF compression {unsupported} is not supported; Geofabrik zlib PBF is supported"
        )
    raise PbfFormatError("OSM PBF Blob contains no supported payload")


def iter_file_blocks(path: str | Path, *, start_offset: int = 0) -> Iterator[PbfBlock]:
    """Yield independently decoded PBF file blocks from a confirmed boundary."""
    source = Path(path)
    size = source.stat().st_size
    if start_offset < 0 or start_offset > size:
        raise PbfFormatError("OSM PBF resume offset is outside the source file")
    with source.open("rb") as handle:
        handle.seek(start_offset)
        while handle.tell() < size:
            offset = handle.tell()
            prefix = _read_exact(handle, 4, "BlobHeader length")
            header_size = int.from_bytes(prefix, "big", signed=False)
            if header_size <= 0 or header_size >= MAX_BLOB_HEADER:
                raise PbfFormatError(f"invalid OSM PBF BlobHeader size {header_size}")
            header = _read_exact(handle, header_size, "BlobHeader")
            block_type, blob_size = _blob_header(header)
            blob = _read_exact(handle, blob_size, "Blob")
            yield PbfBlock(block_type, _blob_payload(blob), offset, handle.tell())


def _required_features(data: bytes) -> set[str]:
    result: set[str] = set()
    for number, wire, value in _fields(data):
        if number == 4 and wire == 2 and isinstance(value, memoryview):
            result.add(bytes(value).decode("utf-8", "strict"))
    return result


def validate_header(data: bytes) -> None:
    unknown = _required_features(data) - SUPPORTED_REQUIRED_FEATURES
    if unknown:
        raise PbfFormatError(
            "OSM PBF requires unsupported feature(s): " + ", ".join(sorted(unknown))
        )


def _string_table(data: memoryview) -> list[bytes]:
    strings: list[bytes] = []
    for number, wire, value in _fields(data):
        if number == 1 and wire == 2 and isinstance(value, memoryview):
            strings.append(bytes(value))
    if not strings:
        raise PbfFormatError("OSM PBF PrimitiveBlock has no StringTable")
    return strings


def _decode_text(strings: list[bytes], index: int) -> str:
    if index < 0 or index >= len(strings):
        raise PbfFormatError(f"OSM PBF string-table index {index} is out of range")
    return strings[index].decode("utf-8", "replace")


def _wanted_key_ids(strings: list[bytes]) -> set[int]:
    return {index for index, value in enumerate(strings) if value in _ADDRESS_KEY_BYTES}


def _tags_from_parallel(
    keys: list[int], vals: list[int], strings: list[bytes], wanted: set[int]
) -> dict[str, str]:
    if len(keys) != len(vals):
        raise PbfFormatError("OSM PBF object has mismatched key/value arrays")
    result: dict[str, str] = {}
    for key_id, value_id in zip(keys, vals):
        if key_id in wanted:
            result[_decode_text(strings, key_id)] = _decode_text(strings, value_id)
    return result


def _message_keys_values(data: memoryview) -> tuple[list[int], list[int]]:
    keys: list[int] = []
    vals: list[int] = []
    for number, wire, value in _fields(data):
        if number == 2:
            keys.extend(_packed_unsigned(value, wire))
        elif number == 3:
            vals.extend(_packed_unsigned(value, wire))
    return keys, vals


def _coordinate(encoded: int, granularity: int, offset: int) -> str:
    nanodegrees = offset + granularity * encoded
    text = f"{nanodegrees / 1_000_000_000:.9f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _regular_node(
    data: memoryview,
    strings: list[bytes],
    wanted: set[int],
    granularity: int,
    lat_offset: int,
    lon_offset: int,
) -> PbfObject | None:
    object_id: int | None = None
    lat: int | None = None
    lon: int | None = None
    keys: list[int] = []
    vals: list[int] = []
    for number, wire, value in _fields(data):
        if number == 1 and wire == 0:
            object_id = _zigzag(int(value))
        elif number == 2:
            keys.extend(_packed_unsigned(value, wire))
        elif number == 3:
            vals.extend(_packed_unsigned(value, wire))
        elif number == 8 and wire == 0:
            lat = _zigzag(int(value))
        elif number == 9 and wire == 0:
            lon = _zigzag(int(value))
    if object_id is None or lat is None or lon is None:
        raise PbfFormatError("OSM PBF Node is missing id/coordinates")
    tags = _tags_from_parallel(keys, vals, strings, wanted)
    if not tags:
        return None
    return PbfObject(
        "node",
        object_id,
        tags,
        _coordinate(lat, granularity, lat_offset),
        _coordinate(lon, granularity, lon_offset),
    )


def _dense_nodes(
    data: memoryview,
    strings: list[bytes],
    wanted: set[int],
    granularity: int,
    lat_offset: int,
    lon_offset: int,
) -> tuple[list[PbfObject], int]:
    ids: list[int] = []
    lats: list[int] = []
    lons: list[int] = []
    keys_vals: list[int] = []
    for number, wire, value in _fields(data):
        if number == 1:
            ids.extend(_packed_signed(value, wire))
        elif number == 8:
            lats.extend(_packed_signed(value, wire))
        elif number == 9:
            lons.extend(_packed_signed(value, wire))
        elif number == 10:
            keys_vals.extend(_packed_unsigned(value, wire))
    if not (len(ids) == len(lats) == len(lons)):
        raise PbfFormatError("OSM PBF DenseNodes columns have different lengths")

    object_id = 0
    lat = 0
    lon = 0
    tag_position = 0
    result: list[PbfObject] = []
    for delta_id, delta_lat, delta_lon in zip(ids, lats, lons):
        object_id += delta_id
        lat += delta_lat
        lon += delta_lon
        tags: dict[str, str] = {}
        if keys_vals:
            while True:
                if tag_position >= len(keys_vals):
                    raise PbfFormatError("OSM PBF DenseNodes tag stream ended early")
                key_id = keys_vals[tag_position]
                tag_position += 1
                if key_id == 0:
                    break
                if tag_position >= len(keys_vals):
                    raise PbfFormatError("OSM PBF DenseNodes tag has no value")
                value_id = keys_vals[tag_position]
                tag_position += 1
                if key_id in wanted:
                    tags[_decode_text(strings, key_id)] = _decode_text(strings, value_id)
        if tags:
            result.append(
                PbfObject(
                    "node",
                    object_id,
                    tags,
                    _coordinate(lat, granularity, lat_offset),
                    _coordinate(lon, granularity, lon_offset),
                )
            )
    if keys_vals and tag_position != len(keys_vals):
        raise PbfFormatError("OSM PBF DenseNodes tag stream has trailing values")
    return result, len(ids)


def _way_or_relation(
    data: memoryview, osm_type: str, strings: list[bytes], wanted: set[int]
) -> PbfObject | None:
    object_id: int | None = None
    keys: list[int] = []
    vals: list[int] = []
    for number, wire, value in _fields(data):
        if number == 1 and wire == 0:
            object_id = _signed_int64(int(value))
        elif number == 2:
            keys.extend(_packed_unsigned(value, wire))
        elif number == 3:
            vals.extend(_packed_unsigned(value, wire))
    if object_id is None:
        raise PbfFormatError(f"OSM PBF {osm_type} is missing id")
    tags = _tags_from_parallel(keys, vals, strings, wanted)
    return PbfObject(osm_type, object_id, tags) if tags else None


def decode_primitive_block(data: bytes) -> tuple[list[PbfObject], int]:
    """Decode relevant address tags while counting every scanned OSM object."""
    string_table_data: memoryview | None = None
    groups: list[memoryview] = []
    granularity = 100
    lat_offset = 0
    lon_offset = 0
    for number, wire, value in _fields(data):
        if number == 1 and wire == 2 and isinstance(value, memoryview):
            string_table_data = value
        elif number == 2 and wire == 2 and isinstance(value, memoryview):
            groups.append(value)
        elif number == 17 and wire == 0:
            granularity = int(value)
        elif number == 19 and wire == 0:
            lat_offset = _signed_int64(int(value))
        elif number == 20 and wire == 0:
            lon_offset = _signed_int64(int(value))
    if string_table_data is None:
        raise PbfFormatError("OSM PBF PrimitiveBlock is missing StringTable")
    if granularity <= 0 or granularity > 1_000_000_000:
        raise PbfFormatError("OSM PBF PrimitiveBlock has invalid granularity")
    strings = _string_table(string_table_data)
    wanted = _wanted_key_ids(strings)
    objects: list[PbfObject] = []
    scanned = 0

    for group in groups:
        for number, wire, value in _fields(group):
            if wire != 2 or not isinstance(value, memoryview):
                continue
            if number == 1:
                scanned += 1
                item = _regular_node(
                    value, strings, wanted, granularity, lat_offset, lon_offset
                )
                if item is not None:
                    objects.append(item)
            elif number == 2:
                dense, count = _dense_nodes(
                    value, strings, wanted, granularity, lat_offset, lon_offset
                )
                scanned += count
                objects.extend(dense)
            elif number == 3:
                scanned += 1
                item = _way_or_relation(value, "way", strings, wanted)
                if item is not None:
                    objects.append(item)
            elif number == 4:
                scanned += 1
                item = _way_or_relation(value, "relation", strings, wanted)
                if item is not None:
                    objects.append(item)
    return objects, scanned


class PurePythonAddressIndex(LocalAddressIndex):
    """LocalAddressIndex builder that consumes OSM PBF without osmium."""

    def _empty_pbf_stats(self) -> dict[str, int]:
        return {
            **self._empty_import_stats(),
            "scanned": 0,
            "blocks": 0,
            "filtered_out": 0,
        }

    def _load_pbf_progress(
        self, db: sqlite3.Connection, source_fingerprint: str
    ) -> tuple[dict[str, int], int]:
        row = db.execute(
            "SELECT source_fingerprint, stats_json FROM build_progress WHERE singleton=1"
        ).fetchone()
        if row is None or str(row["source_fingerprint"]) != source_fingerprint:
            db.execute("DELETE FROM address")
            db.execute("DELETE FROM build_progress")
            db.commit()
            return self._empty_pbf_stats(), 0
        try:
            loaded = json.loads(str(row["stats_json"]))
            if loaded.get("_parser_version") != PARSER_VERSION:
                raise ValueError("legacy parser checkpoint")
            offset = max(0, int(loaded.get("_pbf_offset", 0)))
            stats = self._empty_pbf_stats()
            for key in stats:
                stats[key] = max(0, int(loaded.get(key, 0)))
            stats["stored"] = int(db.execute("SELECT COUNT(*) FROM address").fetchone()[0])
            return stats, offset
        except (TypeError, ValueError, json.JSONDecodeError):
            db.execute("DELETE FROM address")
            db.execute("DELETE FROM build_progress")
            db.commit()
            return self._empty_pbf_stats(), 0

    @staticmethod
    def _save_pbf_progress(
        db: sqlite3.Connection,
        source_fingerprint: str,
        stats: dict[str, int],
        offset: int,
    ) -> None:
        checkpoint = {
            **stats,
            "_parser_version": PARSER_VERSION,
            "_pbf_offset": max(0, int(offset)),
        }
        db.execute(
            """INSERT INTO build_progress(singleton,source_fingerprint,stats_json,updated_at)
               VALUES(1,?,?,?)
               ON CONFLICT(singleton) DO UPDATE SET
                 source_fingerprint=excluded.source_fingerprint,
                 stats_json=excluded.stats_json,
                 updated_at=excluded.updated_at""",
            (
                source_fingerprint,
                json.dumps(checkpoint, separators=(",", ":")),
                utc_now(),
            ),
        )

    @staticmethod
    def _object_feature(item: PbfObject) -> dict[str, Any]:
        feature: dict[str, Any] = {
            "type": "Feature",
            "id": f"{item.osm_type}/{item.osm_id}",
            "properties": dict(item.tags),
            "geometry": None,
        }
        if item.lat and item.lon:
            feature["geometry"] = {
                "type": "Point",
                "coordinates": [item.lon, item.lat],
            }
        return feature

    def _import_pbf_resumable(
        self,
        db: sqlite3.Connection,
        source: Path,
        source_fingerprint: str,
        *,
        city: str = "",
        progress: Callable[[dict[str, int], int], None] | None = None,
    ) -> tuple[dict[str, int], int]:
        stats, start_offset = self._load_pbf_progress(db, source_fingerprint)
        source_size = source.stat().st_size
        if start_offset > source_size:
            db.execute("DELETE FROM address")
            db.execute("DELETE FROM build_progress")
            db.commit()
            stats, start_offset = self._empty_pbf_stats(), 0
        offset = start_offset
        first_block = start_offset == 0

        for block in iter_file_blocks(source, start_offset=start_offset):
            if first_block:
                if block.block_type != "OSMHeader":
                    raise PbfFormatError("OSM PBF must start with an OSMHeader block")
                validate_header(block.data)
                first_block = False
            if block.block_type == "OSMHeader":
                validate_header(block.data)
            elif block.block_type == "OSMData":
                objects, scanned = decode_primitive_block(block.data)
                stats["scanned"] += scanned
                rows: list[tuple[str, ...]] = []
                for item in objects:
                    if city:
                        tagged_city = " ".join(item.tags.get("addr:city", "").split())
                        if tagged_city.casefold() != city.casefold():
                            stats["filtered_out"] += 1
                            continue
                    elif not any(key in item.tags for key in ADDRESS_TRIGGER_KEYS):
                        stats["filtered_out"] += 1
                        continue
                    stats["processed"] += 1
                    row = self._feature_row(self._object_feature(item))
                    if row is None:
                        stats["rejected"] += 1
                    else:
                        rows.append(row)
                inserted_before = stats["inserted"]
                self._store_batch(db, rows, stats)
                stats["stored"] += stats["inserted"] - inserted_before
            stats["blocks"] += 1
            offset = block.next_offset
            self._save_pbf_progress(db, source_fingerprint, stats, offset)
            db.commit()
            if progress:
                progress(dict(stats), offset)

        if first_block:
            raise PbfFormatError("OSM PBF contains no file blocks")
        stats["stored"] = int(db.execute("SELECT COUNT(*) FROM address").fetchone()[0])
        self._save_pbf_progress(db, source_fingerprint, stats, offset)
        db.commit()
        return stats, offset

    def build(
        self,
        source: str | Path,
        *,
        city: str = "",
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, int]:
        source = Path(source).resolve()
        if not source.is_file() or not source.name.casefold().endswith(".osm.pbf"):
            raise ValueError("a regular .osm.pbf extract is required")
        city = " ".join(str(city).split()).strip()
        if len(city) > 120 or any(ord(character) < 32 for character in city):
            raise ValueError("invalid OSM city filter")

        started = time.monotonic()
        source_size = source.stat().st_size
        raw_fingerprint = self._source_fingerprint(source)
        fingerprint = (
            hashlib.sha256(
                f"{raw_fingerprint}\0city:{city.casefold()}".encode("utf-8")
            ).hexdigest()
            if city
            else raw_fingerprint
        )
        previous_build = self._build_status()
        if (
            previous_build.get("source_fingerprint") != fingerprint
            or previous_build.get("parser_version") != PARSER_VERSION
        ):
            self._discard_stale_build()
            previous_build = {}

        self.build_dir.mkdir(parents=True, exist_ok=True)
        self._write_build_status(
            source_fingerprint=fingerprint,
            source_file=str(source),
            city=city,
            parser="python-pbf",
            parser_version=PARSER_VERSION,
            completed_at="",
            build_started_at=utc_now(),
            parse_complete=bool(
                previous_build.get("parse_complete")
                and not previous_build.get("completed_at")
            ),
        )
        active_ready = bool(self.db_path.is_file() and self._stored_status().get("ready"))
        self._write_status(
            state="indexing",
            phase="parsing_pbf",
            phase_started_at=utc_now(),
            parser="python-pbf",
            parser_version=PARSER_VERSION,
            osmium_required=False,
            source_file=str(source),
            source_fingerprint=fingerprint,
            source_bytes=source_size,
            source_size=human_bytes(source_size),
            indexed_at="",
            city=city,
            error="",
            ready=active_ready,
            resumable=True,
            processed=0,
            scanned=0,
            inserted=0,
            updated=0,
            duplicates=0,
            id_collisions=0,
            rejected=0,
            filtered_out=0,
            stored=0,
            blocks=0,
            bytes_processed=0,
            progress_percent=0,
        )

        last_progress = 0.0
        resumed_at = 0
        resumed_offset = 0

        def report(current: dict[str, int], offset: int, *, force: bool = False) -> None:
            nonlocal last_progress
            now = time.monotonic()
            if not force and now - last_progress < 2:
                return
            elapsed = max(0.001, now - started)
            run_records = max(0, current["processed"] - resumed_at)
            run_scanned = max(0, current["scanned"])
            payload: dict[str, Any] = {
                **current,
                "state": "indexing",
                "phase": "parsing_pbf",
                "parser": "python-pbf",
                "parser_version": PARSER_VERSION,
                "osmium_required": False,
                "resumed": resumed_offset > 0,
                "resume_processed": resumed_at,
                "resume_bytes": resumed_offset,
                "bytes_processed": offset,
                "source_bytes": source_size,
                "source_size": human_bytes(source_size),
                "progress_percent": min(100, round(offset * 100 / source_size, 1)) if source_size else 0,
                "elapsed_seconds": round(elapsed, 1),
                "records_per_second": round(run_records / elapsed),
                "objects_per_second": round(run_scanned / elapsed),
                "last_checkpoint_at": utc_now(),
            }
            self._write_status(**payload)
            if progress:
                progress(payload)
            last_progress = now

        def publish(current: dict[str, int], resume_position: int) -> dict[str, int]:
            self._write_status(
                state="indexing",
                phase="publishing",
                phase_started_at=utc_now(),
                parser="python-pbf",
                processed=current["processed"],
                scanned=current["scanned"],
                stored=current["stored"],
                progress_percent=100,
                bytes_processed=source_size,
            )
            current["stored"] = (
                self._promote_city_staging_index(city, current["stored"])
                if city
                else self._promote_staging_index(current["stored"])
            )
            self.staging_db_path.unlink(missing_ok=True)
            Path(str(self.staging_db_path) + "-journal").unlink(missing_ok=True)
            Path(str(self.staging_db_path) + "-wal").unlink(missing_ok=True)
            Path(str(self.staging_db_path) + "-shm").unlink(missing_ok=True)
            elapsed = round(time.monotonic() - started, 1)
            self._write_build_status(
                source_fingerprint=fingerprint,
                source_file=str(source),
                city=city,
                parser="python-pbf",
                parser_version=PARSER_VERSION,
                parse_complete=True,
                completed_at=utc_now(),
                imported_processed=current["processed"],
                imported_scanned=current["scanned"],
                imported_stored=current["stored"],
            )
            self._write_status(
                state="ready",
                phase="completed",
                ready=True,
                parser="python-pbf",
                parser_version=PARSER_VERSION,
                osmium_required=False,
                count=current["stored"],
                indexed_at=utc_now(),
                error="",
                city=city,
                source_bytes=source_size,
                source_size=human_bytes(source_size),
                bytes_processed=source_size,
                progress_percent=100,
                elapsed_seconds=elapsed,
                resumed=resume_position > 0,
                resume_processed=resume_position,
                staging_database="",
                **current,
            )
            return current

        if (
            previous_build.get("parse_complete")
            and not previous_build.get("completed_at")
            and self.staging_db_path.is_file()
        ):
            with self._open_db(self.staging_db_path, staging=True) as staging_db:
                stats, completed_offset = self._load_pbf_progress(staging_db, fingerprint)
            if completed_offset != source_size or stats["stored"] <= 0:
                raise RuntimeError("completed Python PBF staging checkpoint is incomplete")
            self._write_status(
                resumed=True,
                resume_processed=stats["processed"],
                resume_bytes=completed_offset,
                phase="publishing",
                staging_database=str(self.staging_db_path),
            )
            return publish(stats, stats["processed"])

        with self._open_db(self.staging_db_path, staging=True) as staging_db:
            resume_stats, resumed_offset = self._load_pbf_progress(staging_db, fingerprint)
            resumed_at = resume_stats["processed"]
            self._write_status(
                resumed=resumed_offset > 0,
                resume_processed=resumed_at,
                resume_bytes=resumed_offset,
                processed=resumed_at,
                scanned=resume_stats["scanned"],
                bytes_processed=resumed_offset,
                progress_percent=min(100, round(resumed_offset * 100 / source_size, 1)) if source_size else 0,
                staging_database=str(self.staging_db_path),
            )
            stats, final_offset = self._import_pbf_resumable(
                staging_db,
                source,
                fingerprint,
                city=city,
                progress=lambda current, offset: report(current, offset),
            )
            report(stats, final_offset, force=True)

        accepted = stats["inserted"] + stats["updated"] + stats["duplicates"]
        if accepted >= 10_000 and stats["stored"] < accepted // 2:
            raise RuntimeError(
                "OSM index plausibility check failed: "
                f"processed={stats['processed']} accepted={accepted} stored={stats['stored']}"
            )
        self._write_build_status(
            parse_complete=True,
            parse_completed_at=utc_now(),
            imported_processed=stats["processed"],
            imported_scanned=stats["scanned"],
            imported_stored=stats["stored"],
        )
        return publish(stats, resumed_at)
