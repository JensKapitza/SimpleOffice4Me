import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from simpleoffice_version import build_info, version_label


class VersionTrackingTests(unittest.TestCase):
    def test_version_label_uses_major_and_build_number(self):
        self.assertEqual(
            version_label({"release_version": "1.0.0", "build_number": 842, "build_epoch": 1}),
            "1-842",
        )

    def test_version_label_uses_timestamp_when_build_number_is_unavailable(self):
        self.assertEqual(
            version_label({"release_version": "1.0.0", "build_number": 0, "build_epoch": 1704067200}),
            "1-20240101000000",
        )

    def test_release_manifest_supplies_offline_build_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "simpleoffice4me"\nversion = "1.0.0"\n',
                encoding="utf-8",
            )
            (root / ".simpleoffice-release.json").write_text(
                json.dumps({
                    "revision": "abc123def456",
                    "commit_count": 42,
                    "build_epoch": 1704067200,
                }),
                encoding="utf-8",
            )
            env = {
                "SIMPLEOFFICE_BUILD_NUMBER": "",
                "SIMPLEOFFICE_BUILD_EPOCH": "",
                "SIMPLEOFFICE_BUILD_REVISION": "",
                "SOURCE_DATE_EPOCH": "",
                "GITHUB_RUN_NUMBER": "",
            }
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "simpleoffice_version.shutil.which", return_value=None
            ):
                info = build_info(root)

        self.assertEqual(info["version"], "1-42")
        self.assertEqual(info["build_timestamp"], "2024-01-01T00:00:00Z")
        self.assertEqual(info["revision"], "abc123def456")

    def test_explicit_build_metadata_wins_for_packaged_builds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "simpleoffice4me"\nversion = "1.0.0"\n',
                encoding="utf-8",
            )
            env = {
                "SIMPLEOFFICE_BUILD_NUMBER": "99",
                "SIMPLEOFFICE_BUILD_EPOCH": "1704067200",
                "SIMPLEOFFICE_BUILD_REVISION": "fedcba987654",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                info = build_info(root)

        self.assertEqual(info["version"], "1-99")
        self.assertEqual(info["revision"], "fedcba987654")


if __name__ == "__main__":
    unittest.main()
