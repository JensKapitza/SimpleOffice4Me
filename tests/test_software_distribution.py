import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app.software_distribution import (
    SoftwareDistributionStore,
    apply_release_archive,
    build_release_archive,
    clone_release_archive,
    inspect_release_archive,
    is_newer_release,
    local_release_info,
)


def _source(root: Path, *, revision: str, epoch: int, value: str = "v1") -> Path:
    source = root / "source"
    source.mkdir()
    (source / "app").mkdir()
    (source / "app" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "pyproject.toml").write_text('[project]\nname="simpleoffice4me"\nversion="1.2.3"\n', encoding="utf-8")
    (source / "app.txt").write_text(value + "\n", encoding="utf-8")
    (source / ".simpleoffice-release.json").write_text(
        json.dumps({"revision": revision, "branch": "main", "build_epoch": epoch, "commit_count": epoch}),
        encoding="utf-8",
    )
    return source


def _advance(source: Path, *, revision: str, epoch: int, value: str) -> None:
    (source / "app.txt").write_text(value + "\n", encoding="utf-8")
    (source / ".simpleoffice-release.json").write_text(
        json.dumps({"revision": revision, "branch": "main", "build_epoch": epoch, "commit_count": epoch}),
        encoding="utf-8",
    )


class SoftwareDistributionTests(unittest.TestCase):
    def test_release_order_prefers_version_then_build_identity(self):
        current = {"version": "1.2.3", "commit_count": 10, "build_epoch": 100, "revision": "a"}
        self.assertTrue(is_newer_release({"version": "1.2.4", "commit_count": 1, "build_epoch": 1}, current))
        self.assertTrue(is_newer_release({"version": "1.2.3", "commit_count": 11, "build_epoch": 90}, current))
        self.assertFalse(is_newer_release({"version": "1.2.3", "commit_count": 9, "build_epoch": 200}, current))
        self.assertTrue(is_newer_release({"version": "1.2.3", "commit_count": 10, "build_epoch": 100, "revision": "b"}, current))

    def test_project_version_reader_works_without_git(self):
        with tempfile.TemporaryDirectory() as temp:
            source = _source(Path(temp), revision="a" * 40, epoch=100)
            self.assertEqual("1.2.3", local_release_info(source)["version"])

    def test_build_clone_and_inspect_require_no_git_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _source(root, revision="a" * 40, epoch=100)
            archive = root / "release.zip"
            with mock.patch("app.software_distribution_core.subprocess.run", side_effect=AssertionError("external process not expected")):
                built = build_release_archive(archive, root=source)
                checked = inspect_release_archive(archive)
                target = root / "target"
                result = clone_release_archive(archive, target)
            self.assertEqual(built["archive_sha256"], checked["archive_sha256"])
            self.assertEqual("file-payload-v2", built["repository"]["format"])
            self.assertEqual("v1\n", (target / "app.txt").read_text(encoding="utf-8"))
            self.assertEqual("1.2.3", result["release"]["version"])
            self.assertFalse((target / ".git").exists())

    def test_update_replaces_program_files_and_preserves_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _source(root, revision="a" * 40, epoch=100)
            first = root / "first.zip"
            build_release_archive(first, root=source)
            target = root / "target"
            clone_release_archive(first, target)
            (target / "instance").mkdir()
            (target / "instance" / "settings.json").write_text("local", encoding="utf-8")
            (target / ".venv").mkdir()
            (target / ".venv" / "keep.txt").write_text("venv", encoding="utf-8")

            _advance(source, revision="b" * 40, epoch=200, value="v2")
            second = root / "second.zip"
            build_release_archive(second, root=source)
            result = apply_release_archive(second, root=target, install_dependencies=False)
            self.assertEqual("b" * 40, result["new_revision"])
            self.assertEqual("v2\n", (target / "app.txt").read_text(encoding="utf-8"))
            self.assertEqual("local", (target / "instance" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual("venv", (target / ".venv" / "keep.txt").read_text(encoding="utf-8"))
            self.assertFalse((target / ".git").exists())

    def test_update_rejects_downgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _source(root, revision="a" * 40, epoch=100)
            old = root / "old.zip"
            build_release_archive(old, root=source)
            target = root / "target"
            clone_release_archive(old, target)
            _advance(source, revision="b" * 40, epoch=200, value="v2")
            new = root / "new.zip"
            build_release_archive(new, root=source)
            apply_release_archive(new, root=target, install_dependencies=False)
            with self.assertRaisesRegex(ValueError, "nicht neuer"):
                apply_release_archive(old, root=target, install_dependencies=False)

    def test_outer_archive_rejects_unexpected_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _source(root, revision="a" * 40, epoch=100)
            good = root / "good.zip"
            build_release_archive(good, root=source)
            bad = root / "bad.zip"
            with zipfile.ZipFile(good, "r") as source_zip, zipfile.ZipFile(bad, "w") as target_zip:
                for item in source_zip.infolist():
                    target_zip.writestr(item, source_zip.read(item))
                target_zip.writestr("../escape", "bad")
            with self.assertRaisesRegex(ValueError, "unsicheren Pfad|unerwarteten Eintrag"):
                inspect_release_archive(bad)

    def test_payload_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _source(root, revision="a" * 40, epoch=100)
            good = root / "good.zip"
            build_release_archive(good, root=source)
            bad = root / "bad.zip"
            with zipfile.ZipFile(good, "r") as source_zip, zipfile.ZipFile(bad, "w") as target_zip:
                for item in source_zip.infolist():
                    data = source_zip.read(item)
                    if item.filename == "release-package.zip":
                        data += b"tampered"
                    target_zip.writestr(item, data)
            with self.assertRaisesRegex(ValueError, "Payload-Archiv-Hash"):
                inspect_release_archive(bad)

    def test_offer_state_marks_new_version_available(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SoftwareDistributionStore(Path(temp) / "documents")
            offer = store.record_offer(
                "peer-a",
                {
                    "release": {"version": "999.0.0", "revision": "abc", "commit_count": 1, "build_epoch": 1},
                    "bundle": {"sha256": "a" * 64, "size": 123},
                },
            )
            self.assertEqual("available", offer["status"])
            self.assertEqual("peer-a", store.offers()[0]["peer_id"])

    def test_self_deploy_script_contains_no_git_rollback(self):
        script = (Path(__file__).resolve().parents[1] / "tools" / "self_deploy.py").read_text(encoding="utf-8")
        self.assertNotIn("git reset", script)
        self.assertNotIn("git clone", script)
        self.assertNotIn("git fetch", script)
        self.assertNotIn("git bundle", script)


if __name__ == "__main__":
    unittest.main()
