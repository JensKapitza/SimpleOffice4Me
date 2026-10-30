import os
import tempfile
import unittest
from unittest.mock import patch

from app.archive_settings import DEFAULT_ARCHIVE_MEMBER_YIELD_MS
from app.documents_media_settings_form import media_settings_from_form
from app.settings_store import SettingsStore


class ArchiveSettingsFormTests(unittest.TestCase):
    @staticmethod
    def _valid_form():
        return {
            "upload_limit_mib": "512",
            "chat_attachment_max_bytes": "0",
            "archive_ocr_enabled": "0",
            "archive_deep_index_enabled": "1",
            "archive_max_load_per_cpu": "0.75",
            "archive_min_free_memory_mb": "384",
            "archive_disk_reserve_mb": "2048",
            "archive_member_yield_ms": str(DEFAULT_ARCHIVE_MEMBER_YIELD_MS),
            "preview_enabled": "1",
            "preview_timeout_seconds": "45",
            "preview_max_bytes": str(256 * 1024 * 1024),
            "preview_max_pixels": "80000000",
            "video_preview_frames": "10",
        }

    def test_legacy_yield_migrates_once_and_persisted_setting_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SettingsStore(temporary)
            with patch.dict(os.environ, {"SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS": "25"}):
                self.assertEqual(25, store.settings()["media"]["archive_member_yield_ms"])
                store.save({"media": {"preview_enabled": False}}, "archive-admin")
            with patch.dict(os.environ, {"SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS": "0"}):
                self.assertEqual(25, store.settings()["media"]["archive_member_yield_ms"])
        for raw, expected in (("0", 0), ("101", 100), ("invalid", 2), ("-1", 0)):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temporary:
                with patch.dict(os.environ, {"SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS": raw}):
                    self.assertEqual(
                        expected,
                        SettingsStore(temporary).settings()["media"]["archive_member_yield_ms"],
                    )

    def test_form_accepts_bounded_yield_rejects_invalid_and_preserves_older_forms(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SettingsStore(temporary)
            store.save({"media": {"archive_member_yield_ms": 25}}, "archive-admin")
            form = self._valid_form()
            form["archive_member_yield_ms"] = "0"
            parsed = media_settings_from_form(store, form, reset_defaults=False)
            self.assertEqual(0, parsed["archive_member_yield_ms"])

            old_form = self._valid_form()
            old_form.pop("archive_member_yield_ms")
            self.assertEqual(
                25,
                media_settings_from_form(store, old_form, reset_defaults=False)[
                    "archive_member_yield_ms"
                ],
            )
            for invalid in ("-1", "101", "1.5", "invalid", "9" * 20):
                with self.subTest(invalid=invalid):
                    invalid_form = self._valid_form()
                    invalid_form["archive_member_yield_ms"] = invalid
                    with self.assertRaises(ValueError):
                        media_settings_from_form(store, invalid_form, reset_defaults=False)

            store.save({"media": parsed}, "archive-admin")
            self.assertEqual(0, store.settings()["media"]["archive_member_yield_ms"])
            for invalid in (True, -1, 101):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    store.save({"media": {"archive_member_yield_ms": invalid}}, "archive-admin")
            reset = media_settings_from_form(store, {}, reset_defaults=True)
            self.assertEqual(DEFAULT_ARCHIVE_MEMBER_YIELD_MS, reset["archive_member_yield_ms"])


if __name__ == "__main__":
    unittest.main()
