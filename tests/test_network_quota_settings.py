"""Regression tests for the persisted WebDAV quota."""
import tempfile
import unittest

from app.settings_store import MAX_WEBDAV_QUOTA_BYTES, SettingsStore


class NetworkQuotaSettingsTest(unittest.TestCase):
    def test_persist_and_reset(self):
        with tempfile.TemporaryDirectory() as root:
            store = SettingsStore(root)
            self.assertEqual(store.settings()["network"]["webdav_quota_bytes"], 0)
            store.save({"network": {"webdav_quota_bytes": 1048576}}, "admin")
            self.assertEqual(SettingsStore(root).settings()["network"]["webdav_quota_bytes"], 1048576)
            store.save({"network": {"webdav_quota_bytes": 0}}, "admin")
            self.assertEqual(SettingsStore(root).settings()["network"]["webdav_quota_bytes"], 0)

    def test_reject_invalid_quota(self):
        with tempfile.TemporaryDirectory() as root:
            store = SettingsStore(root)
            for invalid in (-1, True, "12", MAX_WEBDAV_QUOTA_BYTES + 1):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):
                        store.save({"network": {"webdav_quota_bytes": invalid}}, "admin")


if __name__ == "__main__":
    unittest.main()
