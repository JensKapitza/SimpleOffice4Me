import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.archive_settings import (
    DEFAULT_ARCHIVE_MAX_CONTENT_FILES,
    DEFAULT_ARCHIVE_MAX_MEMBERS,
    DEFAULT_ARCHIVE_MAX_MEMBER_MB,
    DEFAULT_ARCHIVE_MAX_TEXT_MB,
    DEFAULT_ARCHIVE_MEMBER_YIELD_MS,
)
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
            "archive_max_members": str(DEFAULT_ARCHIVE_MAX_MEMBERS),
            "archive_max_text_mb": str(DEFAULT_ARCHIVE_MAX_TEXT_MB),
            "archive_max_member_mb": str(DEFAULT_ARCHIVE_MAX_MEMBER_MB),
            "archive_max_content_files": str(DEFAULT_ARCHIVE_MAX_CONTENT_FILES),
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

    def test_archive_member_limit_migrates_persists_and_preserves_old_forms(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SettingsStore(temporary)
            with patch.dict(os.environ, {"SIMPLEOFFICE_ARCHIVE_MAX_MEMBERS": "15000"}):
                self.assertEqual(15000, store.settings()["media"]["archive_max_members"])
                store.save({"media": {"preview_enabled": False}}, "archive-admin")
            with patch.dict(os.environ, {"SIMPLEOFFICE_ARCHIVE_MAX_MEMBERS": "100"}):
                self.assertEqual(15000, store.settings()["media"]["archive_max_members"])

            form = self._valid_form()
            form["archive_max_members"] = "100"
            parsed = media_settings_from_form(store, form, reset_defaults=False)
            self.assertEqual(100, parsed["archive_max_members"])
            for invalid in ("99", "200001", "1.5", "invalid"):
                with self.subTest(invalid=invalid):
                    invalid_form = self._valid_form()
                    invalid_form["archive_max_members"] = invalid
                    with self.assertRaises(ValueError):
                        media_settings_from_form(store, invalid_form, reset_defaults=False)
            older_form = self._valid_form()
            older_form.pop("archive_max_members")
            self.assertEqual(
                15000,
                media_settings_from_form(store, older_form, reset_defaults=False)[
                    "archive_max_members"
                ],
            )
            store.save({"media": parsed}, "archive-admin")
            self.assertEqual(100, store.settings()["media"]["archive_max_members"])
            for invalid in (True, 99, 200001):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    store.save({"media": {"archive_max_members": invalid}}, "archive-admin")
            reset = media_settings_from_form(store, {}, reset_defaults=True)
            self.assertEqual(DEFAULT_ARCHIVE_MAX_MEMBERS, reset["archive_max_members"])

    def test_archive_member_limit_is_exposed_with_bounded_admin_input(self):
        template = Path(__file__).parents[1] / "templates/admin/documents_media_settings.html"
        html = template.read_text(encoding="utf-8")
        self.assertIn('name="archive_max_members"', html)
        self.assertIn('min="100" max="200000" step="1"', html)

    def test_total_text_budget_migrates_validates_and_preserves_old_forms(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SettingsStore(temporary)
            with patch.dict(os.environ, {"SIMPLEOFFICE_ARCHIVE_MAX_TEXT_MB": "12"}):
                self.assertEqual(12, store.settings()["media"]["archive_max_text_mb"])
                store.save({"media": {"preview_enabled": False}}, "archive-admin")
            with patch.dict(os.environ, {"SIMPLEOFFICE_ARCHIVE_MAX_TEXT_MB": "1"}):
                self.assertEqual(12, store.settings()["media"]["archive_max_text_mb"])

            form = self._valid_form()
            form["archive_max_text_mb"] = "1"
            parsed = media_settings_from_form(store, form, reset_defaults=False)
            self.assertEqual(1, parsed["archive_max_text_mb"])
            older_form = self._valid_form()
            older_form.pop("archive_max_text_mb")
            self.assertEqual(
                12,
                media_settings_from_form(store, older_form, reset_defaults=False)[
                    "archive_max_text_mb"
                ],
            )
            for invalid in ("0", "65", "1.5", "invalid"):
                with self.subTest(invalid=invalid):
                    invalid_form = self._valid_form()
                    invalid_form["archive_max_text_mb"] = invalid
                    with self.assertRaises(ValueError):
                        media_settings_from_form(store, invalid_form, reset_defaults=False)
            store.save({"media": parsed}, "archive-admin")
            self.assertEqual(1, store.settings()["media"]["archive_max_text_mb"])
            for invalid in (True, 0, 65):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    store.save({"media": {"archive_max_text_mb": invalid}}, "archive-admin")
            reset = media_settings_from_form(store, {}, reset_defaults=True)
            self.assertEqual(DEFAULT_ARCHIVE_MAX_TEXT_MB, reset["archive_max_text_mb"])

    def test_total_text_budget_is_exposed_with_bounded_admin_input(self):
        template = Path(__file__).parents[1] / "templates/admin/documents_media_settings.html"
        html = template.read_text(encoding="utf-8")
        self.assertIn('name="archive_max_text_mb"', html)
        self.assertIn('min="1" max="64" step="1"', html)

    def test_extraction_limits_migrate_validate_and_preserve_old_forms(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SettingsStore(temporary)
            legacy = {
                "SIMPLEOFFICE_ARCHIVE_MAX_MEMBER_MB": "2048",
                "SIMPLEOFFICE_ARCHIVE_MAX_CONTENT_FILES": "2500",
            }
            with patch.dict(os.environ, legacy):
                media = store.settings()["media"]
                self.assertEqual(2048, media["archive_max_member_mb"])
                self.assertEqual(2500, media["archive_max_content_files"])
                store.save({"media": {"preview_enabled": False}}, "archive-admin")
            with patch.dict(os.environ, {
                "SIMPLEOFFICE_ARCHIVE_MAX_MEMBER_MB": "512",
                "SIMPLEOFFICE_ARCHIVE_MAX_CONTENT_FILES": "500",
            }):
                media = store.settings()["media"]
                self.assertEqual(2048, media["archive_max_member_mb"])
                self.assertEqual(2500, media["archive_max_content_files"])
            store.save(
                {"media": {"archive_max_member_mb": 2, "archive_max_content_files": 2000}},
                "archive-admin",
            )

            form = self._valid_form()
            form.update({"archive_max_member_mb": "1", "archive_max_content_files": "10000"})
            parsed = media_settings_from_form(store, form, reset_defaults=False)
            self.assertEqual(1, parsed["archive_max_member_mb"])
            self.assertEqual(10000, parsed["archive_max_content_files"])

            older_form = self._valid_form()
            older_form.pop("archive_max_member_mb")
            older_form.pop("archive_max_content_files")
            previous = media_settings_from_form(store, older_form, reset_defaults=False)
            self.assertEqual(2, previous["archive_max_member_mb"])
            self.assertEqual(2000, previous["archive_max_content_files"])

            for key, invalid in (
                ("archive_max_member_mb", "0"),
                ("archive_max_member_mb", "8193"),
                ("archive_max_content_files", "0"),
                ("archive_max_content_files", "10001"),
                ("archive_max_content_files", "1.5"),
            ):
                with self.subTest(key=key, invalid=invalid):
                    invalid_form = self._valid_form()
                    invalid_form[key] = invalid
                    with self.assertRaises(ValueError):
                        media_settings_from_form(store, invalid_form, reset_defaults=False)

            reset = media_settings_from_form(store, {}, reset_defaults=True)
            self.assertEqual(DEFAULT_ARCHIVE_MAX_MEMBER_MB, reset["archive_max_member_mb"])
            self.assertEqual(DEFAULT_ARCHIVE_MAX_CONTENT_FILES, reset["archive_max_content_files"])

    def test_extraction_limits_are_exposed_with_bounded_admin_inputs(self):
        template = Path(__file__).parents[1] / "templates/admin/documents_media_settings.html"
        html = template.read_text(encoding="utf-8")
        self.assertIn('name="archive_max_member_mb"', html)
        self.assertIn('min="1" max="8192" step="1"', html)
        self.assertIn('name="archive_max_content_files"', html)
        self.assertIn('min="1" max="10000" step="1"', html)


if __name__ == "__main__":
    unittest.main()
