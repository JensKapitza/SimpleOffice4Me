import tempfile
import unittest
from pathlib import Path

from app.datalogger import _source_config
from app.datalogger_collectors import CollectionError, collect_linux
from app.datalogger_store import DataLoggerStore


class DataLoggerPaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = DataLoggerStore(self.root)
        self.channel_id = self.store.create_channel("Test", "alice", unit="x")

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_linux_metric_defaults_to_load1(self):
        config = _source_config("linux", {"metric": "", "path": ""})
        self.assertEqual("load1", config["metric"])
        self.assertEqual("/", config["path"])

    def test_existing_empty_linux_metric_self_heals(self):
        value = collect_linux({"metric": ""})
        self.assertIsInstance(value, float)

    def test_unknown_linux_metric_is_rejected_when_configured(self):
        with self.assertRaises(ValueError):
            _source_config("linux", {"metric": "cpu_magic"})
        with self.assertRaises(CollectionError) as caught:
            collect_linux({"metric": "cpu_magic"})
        self.assertEqual("linux_metric_unknown", caught.exception.code)

    def test_sample_count_and_offset_pagination(self):
        for index in range(125):
            self.store.add_sample(
                self.channel_id,
                index,
                "alice",
                measured_at=f"2026-08-25T18:{index // 60:02d}:{index % 60:02d}+00:00",
            )
        self.assertEqual(125, self.store.sample_count(self.channel_id))
        first = self.store.samples(self.channel_id, limit=50, offset=0)
        second = self.store.samples(self.channel_id, limit=50, offset=50)
        third = self.store.samples(self.channel_id, limit=50, offset=100)
        self.assertEqual(50, len(first))
        self.assertEqual(50, len(second))
        self.assertEqual(25, len(third))
        self.assertEqual(124.0, first[-1]["value"])
        self.assertEqual(75.0, second[0]["value"])
        self.assertEqual(24.0, third[-1]["value"])


if __name__ == "__main__":
    unittest.main()
