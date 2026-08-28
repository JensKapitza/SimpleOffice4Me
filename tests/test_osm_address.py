import json
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.osm_address import LocalAddressIndex, _remote_total, field_suggestions, human_bytes, unique_candidate


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class OsmAddressTests(unittest.TestCase):
    @staticmethod
    def _feature(number: int, *, feature_id=None, street="Teststraße", lat=None, lon="6.7"):
        feature = {
            "type": "Feature",
            "properties": {"addr:street": street, "addr:housenumber": str(number), "addr:postcode": "47137", "addr:city": "Duisburg"},
            "geometry": {"type": "Point", "coordinates": [lon, lat if lat is not None else f"51.{number:04d}"]},
        }
        if feature_id is not None:
            feature["id"] = feature_id
        return json.dumps(feature)

    def test_local_sqlite_index_finds_normalized_address(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            with index._db() as db:
                db.execute(
                    "INSERT INTO address(street,house_number,postal,city,country,state,lat,lon,osm_type,osm_id,normalized) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("Musterstraße", "12", "12345", "Musterstadt", "DE", "NRW", "51.0", "6.0", "node", "123", "musterstrasse 12 12345 musterstadt de"),
                )
            result = index.search("Musterstr. 12 12345", country_code="de")
            self.assertEqual(1, len(result))
            self.assertEqual("Musterstraße 12", result[0]["street"])
            self.assertEqual("12345", result[0]["postal"])
            self.assertEqual("Musterstadt", result[0]["city"])
            self.assertEqual("DE", result[0]["country"])

    def test_search_does_not_use_network_without_local_index(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual([], LocalAddressIndex(Path(root)).search("Musterstraße 12"))

    def test_search_tolerates_missing_city_on_osm_address(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            with index._db() as db:
                db.execute(
                    "INSERT INTO address(street,house_number,postal,city,country,state,lat,lon,osm_type,osm_id,normalized) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("Beispielstraße", "27", "12345", "", "DE", "NRW", "", "", "node", "27", "beispielstrasse 27 12345 de"),
                )
            result = index.search("Musterstadt 12345 Beispielstr. 27", country_code="de")
            self.assertEqual(1, len(result))
            self.assertEqual("Beispielstraße 27", result[0]["street"])
            self.assertEqual("fallback", result[0]["match_quality"])
            self.assertIsNone(unique_candidate(result))

    def test_unique_requires_one_complete_candidate(self):
        candidate = {"street": "A 1", "postal": "12345", "city": "Ort", "country": "DE"}
        self.assertEqual(candidate, unique_candidate([candidate]))
        self.assertIsNone(unique_candidate([candidate, dict(candidate)]))
        self.assertIsNone(unique_candidate([{"street": "A 1", "city": ""}]))

    def test_ambiguous_results_only_suggest_the_requested_field(self):
        candidates = [
            {"street": "Weserstraße 27", "postal": "47137", "city": "Duisburg"},
            {"street": "Weserstraße 29", "postal": "47137", "city": "Duisburg"},
            {"street": "Weserstraße 27", "postal": "28199", "city": "Bremen"},
        ]
        self.assertEqual(
            [{"field": "city", "value": "Duisburg"}, {"field": "city", "value": "Bremen"}],
            field_suggestions(candidates, "city"),
        )
        self.assertEqual(
            [{"field": "postal", "value": "47137"}, {"field": "postal", "value": "28199"}],
            field_suggestions(candidates, "postal"),
        )
        self.assertEqual([], field_suggestions(candidates, "country"))

    def test_human_bytes_formats_download_sizes(self):
        self.assertEqual("1.0 MiB", human_bytes(1024 * 1024))
        self.assertEqual("1.5 GiB", human_bytes(int(1.5 * 1024**3)))
        self.assertEqual("unbekannt", human_bytes(0))

    def test_status_calculates_download_progress(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            index.data_dir.mkdir(parents=True, exist_ok=True)
            index.status_path.write_text(json.dumps({"state": "downloading", "downloaded_bytes": 25, "expected_bytes": 100}), encoding="utf-8")
            status = index.status()
            self.assertEqual(25.0, status["progress_percent"])
            self.assertEqual(25, status["downloaded_bytes"])
            self.assertEqual(100, status["expected_bytes"])
            self.assertEqual(str(Path(root).resolve()), status["document_root"])
            self.assertEqual(str(index.db_path), status["database_path"])

    def test_remote_total_prefers_content_range_for_resume(self):
        self.assertEqual(1000, _remote_total(_Headers({"Content-Range": "bytes 250-499/1000", "Content-Length": "250"}), 250))
        self.assertEqual(1000, _remote_total(_Headers({"Content-Length": "750"}), 250))
        self.assertEqual(750, _remote_total(_Headers({"Content-Length": "750"}), 0))

    def test_partial_download_state_is_visible_as_resume_progress(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            index.data_dir.mkdir(parents=True, exist_ok=True)
            partial = index.data_dir / "germany-latest.osm.pbf.part"
            partial.write_bytes(b"x" * 50)
            index.status_path.write_text(json.dumps({"state": "retrying", "downloaded_bytes": partial.stat().st_size, "expected_bytes": 100}), encoding="utf-8")
            status = index.status()
            self.assertEqual("retrying", status["state"])
            self.assertEqual(50.0, status["progress_percent"])

    def test_existing_download_can_be_reindexed_without_network(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            index.data_dir.mkdir(parents=True)
            source = index.data_dir / "test-latest.osm.pbf"
            source.write_bytes(b"local extract")
            index.status_path.write_text(json.dumps({"source_file": str(source)}), encoding="utf-8")
            self.assertEqual(source.resolve(), index.downloaded_source())
            self.assertTrue(index.needs_reindex())
            self.assertTrue(index.status()["source_available"])

    def test_import_over_batch_size_keeps_every_missing_osm_id(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            lines = [self._feature(number) for number in range(2005)]
            with index._db() as db:
                stats = index._import_geojson_lines(db, lines, batch_size=2000)
                stored = db.execute("SELECT COUNT(*) FROM address").fetchone()[0]
            self.assertEqual(2005, stats["processed"])
            self.assertEqual(2005, stats["inserted"])
            self.assertEqual(2005, stats["stored"])
            self.assertEqual(2005, stored)

    def test_import_reports_progress_after_each_batch_and_at_completion(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root)); reports = []
            with index._db() as db:
                stats = index._import_geojson_lines(
                    db,
                    [self._feature(number) for number in range(5)],
                    batch_size=2,
                    progress=reports.append,
                )
            self.assertEqual([2, 4, 5], [row["processed"] for row in reports])
            self.assertEqual(5, reports[-1]["stored"])
            self.assertEqual(stats, reports[-1])

    def test_osmium_stderr_uses_file_instead_of_blocking_pipe(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            index.data_dir.mkdir(parents=True)
            source = index.data_dir / "test-latest.osm.pbf"
            source.write_bytes(b"extract")
            process = Mock()
            process.stdout = io.StringIO(self._feature(27) + "\n")
            process.wait.return_value = 0
            process.poll.return_value = 0
            process.args = ["osmium", "export"]
            with patch("app.osm_address.shutil.which", return_value="/usr/bin/osmium"), patch("app.osm_address.subprocess.run"), patch("app.osm_address.subprocess.Popen", return_value=process) as popen:
                stats = index.build(source)
            self.assertEqual(1, stats["stored"])
            self.assertIsNot(subprocess.PIPE, popen.call_args.kwargs["stderr"])
            self.assertTrue(hasattr(popen.call_args.kwargs["stderr"], "write"))

    def test_missing_osm_id_is_stable_and_exact_duplicate_is_counted(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root)); line = self._feature(27)
            with index._db() as db:
                first = index._import_geojson_lines(db, [line])
                stored_id = db.execute("SELECT osm_id FROM address").fetchone()[0]
                second = index._import_geojson_lines(db, [line])
            self.assertEqual(1, first["inserted"])
            self.assertEqual(1, second["duplicates"])
            self.assertEqual(1, second["stored"])
            self.assertTrue(stored_id.startswith("sha256:"))

    def test_same_address_with_distinct_osm_objects_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            lines = [self._feature(27, feature_id="node/1"), self._feature(27, feature_id="way/1")]
            with index._db() as db:
                stats = index._import_geojson_lines(db, lines)
            self.assertEqual(2, stats["inserted"])
            self.assertEqual(2, stats["stored"])
            self.assertEqual(0, stats["duplicates"])

    def test_synthetic_identity_uses_coordinates_to_avoid_collisions(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            lines = [self._feature(27, lat="51.10"), self._feature(27, lat="51.11")]
            with index._db() as db:
                stats = index._import_geojson_lines(db, lines)
                ids = {row[0] for row in db.execute("SELECT osm_id FROM address")}
            self.assertEqual(2, stats["stored"])
            self.assertEqual(2, len(ids))

    def test_existing_osm_object_is_updated_without_replace(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            with index._db() as db:
                index._import_geojson_lines(db, [self._feature(27, feature_id="node/42", street="Altstraße")])
                stats = index._import_geojson_lines(db, [self._feature(27, feature_id="node/42", street="Neustraße")])
                street = db.execute("SELECT street FROM address WHERE osm_type='node' AND osm_id='42'").fetchone()[0]
            self.assertEqual(1, stats["updated"])
            self.assertEqual(1, stats["stored"])
            self.assertEqual("Neustraße", street)

    def test_synthetic_id_collision_is_rejected_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as root:
            index = LocalAddressIndex(Path(root))
            first = index._feature_row(json.loads(self._feature(27, street="Erste Straße")))
            second = index._feature_row(json.loads(self._feature(28, street="Zweite Straße")))
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            collision = (*second[:9], first[9], second[10])
            stats = {
                "processed": 2, "inserted": 0, "updated": 0,
                "duplicates": 0, "id_collisions": 0, "rejected": 0, "stored": 0,
            }
            with index._db() as db:
                index._store_batch(db, [first, collision], stats)
                stored = db.execute("SELECT street FROM address").fetchall()
            self.assertEqual(1, stats["inserted"])
            self.assertEqual(1, stats["id_collisions"])
            self.assertEqual(1, stats["rejected"])
            self.assertEqual([("Erste Straße",)], [tuple(row) for row in stored])


if __name__ == "__main__":
    unittest.main()
