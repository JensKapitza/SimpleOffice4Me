import tempfile
import unittest
import zlib
from pathlib import Path

from app.osm_pbf import (
    PbfFormatError,
    PurePythonAddressIndex,
    decode_primitive_block,
    iter_file_blocks,
)


def _varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint helper expects unsigned input")
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            result.append(byte | 0x80)
        else:
            result.append(byte)
            return bytes(result)


def _zigzag(value: int) -> int:
    return (value << 1) ^ (value >> 63)


def _field_varint(number: int, value: int) -> bytes:
    return _varint(number << 3) + _varint(value)


def _field_bytes(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _packed(values) -> bytes:
    return b"".join(_varint(int(value)) for value in values)


def _blob(block_type: str, payload: bytes) -> bytes:
    compressed = zlib.compress(payload)
    blob = _field_varint(2, len(payload)) + _field_bytes(3, compressed)
    header = _field_bytes(1, block_type.encode("ascii")) + _field_varint(3, len(blob))
    return len(header).to_bytes(4, "big") + header + blob


def _header_block() -> bytes:
    return _field_bytes(4, b"OsmSchema-V0.6") + _field_bytes(4, b"DenseNodes")


def _primitive_block(nodes) -> bytes:
    """Build one small, standards-shaped PrimitiveBlock with DenseNodes."""
    strings = [b""]
    for key in (
        b"addr:street",
        b"addr:housenumber",
        b"addr:postcode",
        b"addr:city",
        b"addr:country",
    ):
        if key not in strings:
            strings.append(key)
    for node in nodes:
        for value in (
            node["street"].encode(),
            node["house"].encode(),
            node["postal"].encode(),
            node["city"].encode(),
            b"DE",
        ):
            if value not in strings:
                strings.append(value)
    indexes = {value: index for index, value in enumerate(strings)}
    string_table = b"".join(_field_bytes(1, value) for value in strings)

    previous_id = previous_lat = previous_lon = 0
    id_deltas = []
    lat_deltas = []
    lon_deltas = []
    keys_vals = []
    for node in nodes:
        node_id = int(node["id"])
        lat = int(round(float(node["lat"]) * 1_000_000_000 / 100))
        lon = int(round(float(node["lon"]) * 1_000_000_000 / 100))
        id_deltas.append(_zigzag(node_id - previous_id))
        lat_deltas.append(_zigzag(lat - previous_lat))
        lon_deltas.append(_zigzag(lon - previous_lon))
        previous_id, previous_lat, previous_lon = node_id, lat, lon
        for key, value in (
            (b"addr:street", node["street"].encode()),
            (b"addr:housenumber", node["house"].encode()),
            (b"addr:postcode", node["postal"].encode()),
            (b"addr:city", node["city"].encode()),
            (b"addr:country", b"DE"),
        ):
            keys_vals.extend((indexes[key], indexes[value]))
        keys_vals.append(0)

    dense = (
        _field_bytes(1, _packed(id_deltas))
        + _field_bytes(8, _packed(lat_deltas))
        + _field_bytes(9, _packed(lon_deltas))
        + _field_bytes(10, _packed(keys_vals))
    )
    group = _field_bytes(2, dense)
    return _field_bytes(1, string_table) + _field_bytes(2, group)


def _write_pbf(path: Path, groups) -> None:
    content = bytearray(_blob("OSMHeader", _header_block()))
    for nodes in groups:
        content.extend(_blob("OSMData", _primitive_block(nodes)))
    path.write_bytes(bytes(content))


class PurePythonPbfTests(unittest.TestCase):
    def _node(self, number, *, city="Duisburg"):
        return {
            "id": number,
            "street": "Teststraße",
            "house": str(number),
            "postal": "47137",
            "city": city,
            "lat": "51.5001",
            "lon": "6.7502",
        }

    def test_iter_file_blocks_and_dense_nodes(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "test.osm.pbf"
            _write_pbf(source, [[self._node(101), self._node(102)]])
            blocks = list(iter_file_blocks(source))
            self.assertEqual(["OSMHeader", "OSMData"], [block.block_type for block in blocks])
            objects, scanned = decode_primitive_block(blocks[1].data)
            self.assertEqual(2, scanned)
            self.assertEqual([101, 102], [item.osm_id for item in objects])
            self.assertEqual("Teststraße", objects[0].tags["addr:street"])
            self.assertEqual("Duisburg", objects[0].tags["addr:city"])
            self.assertEqual("51.5001", objects[0].lat)
            self.assertEqual("6.7502", objects[0].lon)

    def test_build_indexes_pbf_without_osmium(self):
        with tempfile.TemporaryDirectory() as root:
            index = PurePythonAddressIndex(Path(root))
            index.data_dir.mkdir(parents=True)
            source = index.data_dir / "test-latest.osm.pbf"
            _write_pbf(source, [[self._node(101), self._node(102)]])
            stats = index.build(source)
            self.assertEqual(2, stats["scanned"])
            self.assertEqual(2, stats["processed"])
            self.assertEqual(2, stats["inserted"])
            self.assertEqual(2, stats["stored"])
            result = index.search("Teststr. 101 47137 Duisburg")
            self.assertEqual(1, len(result))
            self.assertEqual("node", result[0]["osm_type"])
            self.assertEqual("101", result[0]["osm_id"])
            self.assertEqual("Teststraße 101", result[0]["street"])
            self.assertEqual("python-pbf", index.status()["parser"])

    def test_city_build_filters_pbf_and_preserves_other_live_city(self):
        with tempfile.TemporaryDirectory() as root:
            index = PurePythonAddressIndex(Path(root))
            index.data_dir.mkdir(parents=True)
            source = index.data_dir / "test-latest.osm.pbf"
            _write_pbf(source, [[self._node(101), self._node(201, city="Berlin")]])
            index.build(source)
            _write_pbf(source, [[self._node(102), self._node(202, city="Berlin")]])
            stats = index.build(source, city="Duisburg")
            self.assertEqual(1, stats["processed"])
            with index._db() as db:
                rows = [tuple(row) for row in db.execute(
                    "SELECT city,osm_id FROM address ORDER BY city,osm_id"
                )]
            self.assertEqual([("Berlin", "201"), ("Duisburg", "102")], rows)

    def test_interrupted_build_resumes_from_confirmed_file_block(self):
        with tempfile.TemporaryDirectory() as root:
            index = PurePythonAddressIndex(Path(root))
            index.data_dir.mkdir(parents=True)
            source = index.data_dir / "test-latest.osm.pbf"
            _write_pbf(source, [[self._node(101)], [self._node(102)], [self._node(103)]])
            interrupted_at = 0

            def interrupt(progress):
                nonlocal interrupted_at
                if progress["processed"] >= 1:
                    interrupted_at = progress["bytes_processed"]
                    raise RuntimeError("intentional interruption")

            with self.assertRaisesRegex(RuntimeError, "intentional interruption"):
                index.build(source, progress=interrupt)
            self.assertGreater(interrupted_at, 0)
            with index._open_db(index.staging_db_path, staging=True) as db:
                self.assertEqual(1, db.execute("SELECT COUNT(*) FROM address").fetchone()[0])

            stats = index.build(source)
            self.assertEqual(3, stats["processed"])
            self.assertEqual(3, stats["stored"])
            status = index.status()
            self.assertTrue(status["resumed"])
            self.assertGreater(status["resume_bytes"], 0)

    def test_corrupt_zlib_blob_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "bad.osm.pbf"
            payload = _field_varint(2, 100) + _field_bytes(3, b"not-zlib")
            header = _field_bytes(1, b"OSMHeader") + _field_varint(3, len(payload))
            source.write_bytes(len(header).to_bytes(4, "big") + header + payload)
            with self.assertRaisesRegex(PbfFormatError, "zlib"):
                list(iter_file_blocks(source))


if __name__ == "__main__":
    unittest.main()
