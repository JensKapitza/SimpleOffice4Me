"""Central user-home app config store (no environment)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import app_config_store as store


class AppConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(
            store, "settings_path",
            return_value=Path(self.temp.name) / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_file_reads_defaults(self):
        self.assertEqual("dflt", store.get("section", "key", "dflt"))
        self.assertEqual({}, store.section("section"))
        self.assertEqual("", store.secret("section", "key"))
        self.assertFalse(store.secret_configured("section", "key"))

    def test_put_and_get_roundtrip(self):
        store.put("vision", {"provider": "meta", "timeout": 60})
        self.assertEqual("meta", store.get("vision", "provider"))
        self.assertEqual({"provider": "meta", "timeout": 60}, store.section("vision"))
        store.put("vision", {"timeout": 30})
        self.assertEqual("meta", store.get("vision", "provider"))
        self.assertEqual(30, store.get("vision", "timeout"))

    def test_put_does_not_overwrite_corrupt_file(self):
        path = store.settings_path()
        path.write_text('{"vision":', encoding="utf-8")
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "beschädigt"):
            store.put("vision", {"provider": "meta"})
        self.assertEqual(original, path.read_bytes())

    def test_put_fails_closed_without_overwriting_corrupt_file(self):
        path = store.settings_path()
        path.write_text('{"vision":', encoding="utf-8")
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "beschädigt"):
            store.put("vision", {"provider": "meta"})
        self.assertEqual(original, path.read_bytes())

    def test_secret_encrypted_never_cleartext(self):
        with app.app_context():
            store.put_secret("vision", "meta_api_key", "sk-test-only")
        raw = store.settings_path().read_text(encoding="utf-8")
        self.assertNotIn("sk-test-only", raw)
        self.assertIn("enc:v1:", raw)
        self.assertTrue(store.secret_configured("vision", "meta_api_key"))
        with app.app_context():
            self.assertEqual("sk-test-only", store.secret("vision", "meta_api_key"))

    def test_rejects_non_jsonable_and_bad_keys(self):
        with self.assertRaises(ValueError):
            store.put("vision", {"key": object()})
        with self.assertRaises(ValueError):
            store.put("vision", {"": "x"})
        with self.assertRaises(ValueError):
            store.put_secret("vision", "k", "")


if __name__ == "__main__":
    unittest.main()
