import tempfile
import unittest
from pathlib import Path

from app.v2.migration import inspect_migration


class V2MigrationPreflightTests(unittest.TestCase):
    def test_missing_root_is_blocked_without_creating_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "missing"
            result = inspect_migration(root)
            self.assertFalse(result.ready)
            self.assertFalse(root.exists())
            self.assertIn("document root does not exist", result.blockers)

    def test_empty_existing_root_is_read_only_and_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            result = inspect_migration(root)
            after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            self.assertTrue(result.ready)
            self.assertEqual(before, after)
            self.assertEqual(0, result.v2_invalid_objects)


if __name__ == "__main__":
    unittest.main()
