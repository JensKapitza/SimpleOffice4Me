"""Unittest-discoverable regressions for the persistent S3 switch."""

import tempfile
import unittest

from app.settings_store import SettingsStore


class S3AdminToggleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SettingsStore(self.temp.name)

    def test_disabled_by_default(self):
        self.assertFalse(self.store.settings()["s3"]["enabled"])

    def test_toggle_persists_across_instances(self):
        settings = self.store.settings()
        settings["s3"]["enabled"] = True
        self.store.save(settings, "test-admin")
        self.assertTrue(SettingsStore(self.temp.name).settings()["s3"]["enabled"])
        settings = self.store.settings()
        settings["s3"]["enabled"] = False
        self.store.save(settings, "test-admin")
        self.assertFalse(SettingsStore(self.temp.name).settings()["s3"]["enabled"])

    def test_rejects_non_boolean(self):
        settings = self.store.settings()
        settings["s3"]["enabled"] = "false"
        with self.assertRaisesRegex(ValueError, "S3 enabled"):
            self.store.save(settings, "test-admin")
