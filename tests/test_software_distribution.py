import hashlib
import json
import os
import shutil
import subprocess
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


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _repo(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "SimpleOffice Test")
    (source / "pyproject.toml").write_text('[project]\nname="simpleoffice4me"\nversion="1.2.3"\n', encoding="utf-8")
    (source / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "v1")
    return source


def _commit(root: Path, value: str) -> str:
    (root / "app.txt").write_text(value + "\n", encoding="utf-8")
    _git(root, "add", "app.txt")
    _git(root, "commit", "-m", value)
    return _git(root, "rev-parse", "HEAD")


_GIT_TEST_ENV = {
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "gc.auto",
    "GIT_CONFIG_VALUE_0": "0",
    "GIT_CONFIG_KEY_1": "maintenance.auto",
    "GIT_CONFIG_VALUE_1": "false",
}


@unittest.skipUnless(shutil.which("git"), "git unavailable")
@mock.patch.dict(os.environ, _GIT_TEST_ENV)
class SoftwareDistributionTests(unittest.TestCase):
    def test_release_order_prefers_version_then_commit_count(self):
        current = {"version": "1.2.3", "commit_count": 10, "build_epoch": 100, "revision": "a"}
        self.assertTrue(is_newer_release({"version": "1.2.4", "commit_count": 1, "build_epoch": 1}, current))
        self.assertTrue(is_newer_release({"version": "1.2.3", "commit_count": 11, "build_epoch": 90}, current))
        self.assertFalse(is_newer_release({"version": "1.2.3", "commit_count": 9, "build_epoch": 200}, current))

    def test_project_version_reader_remains_python_310_compatible(self):
        with tempfile.TemporaryDirectory() as temp:
            source = _repo(Path(temp))
            self.assertEqual("1.2.3", local_release_info(source)["version"])

    def test_self_deploy_bundle_clones_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _repo(root)
            archive = root / "release.zip"
            built = build_release_archive(archive, root=source)
            checked = inspect_release_archive(archive)
            self.assertEqual(built["archive_sha256"], checked["archive_sha256"])
            target = root / "target"
            result = clone_release_archive(archive, target)
            self.assertEqual("v1\n", (target / "app.txt").read_text(encoding="utf-8"))
            self.assertEqual("1.2.3", result["release"]["version"])
            installed = json.loads((target / ".simpleoffice-release.json").read_text(encoding="utf-8"))
            self.assertEqual(built["release"]["revision"], installed["revision"])

    def test_offline_update_fast_forwards_and_rejects_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _repo(root)
            first = root / "first.zip"
            build_release_archive(first, root=source)
            target = root / "target"
            clone_release_archive(first, target)

            new_revision = _commit(source, "v2")
            second = root / "second.zip"
            build_release_archive(second, root=source)
            result = apply_release_archive(second, root=target, install_dependencies=False)
            self.assertEqual(new_revision, result["new_revision"])
            self.assertEqual("v2\n", (target / "app.txt").read_text(encoding="utf-8"))

            (target / "app.txt").write_text("local\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "lokale getrackte Änderungen"):
                apply_release_archive(second, root=target, install_dependencies=False)

    def test_offline_update_rejects_downgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _repo(root)
            old_archive = root / "old.zip"
            build_release_archive(old_archive, root=source)
            target = root / "target"
            clone_release_archive(old_archive, target)
            _commit(source, "v2")
            new_archive = root / "new.zip"
            build_release_archive(new_archive, root=source)
            apply_release_archive(new_archive, root=target, install_dependencies=False)
            with self.assertRaisesRegex(ValueError, "nicht neuer"):
                apply_release_archive(old_archive, root=target, install_dependencies=False)

    def test_offline_update_rejects_divergent_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _repo(root)
            first = root / "first.zip"
            build_release_archive(first, root=source)
            target = root / "target"
            clone_release_archive(first, target)
            _git(target, "config", "user.email", "target@example.invalid")
            _git(target, "config", "user.name", "Target")
            (target / "local.txt").write_text("local\n", encoding="utf-8")
            _git(target, "add", "local.txt")
            _git(target, "commit", "-m", "local branch")

            _commit(source, "v2")
            _commit(source, "v3")
            candidate = root / "candidate.zip"
            build_release_archive(candidate, root=source)
            with self.assertRaisesRegex(ValueError, "kein Fast-Forward"):
                apply_release_archive(candidate, root=target, install_dependencies=False)

    def test_archive_rejects_unexpected_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.zip"
            bundle = b"not-a-real-bundle"
            manifest = {
                "schema": 1,
                "release": {"revision": "a" * 40, "branch": "main"},
                "repository": {"sha256": hashlib.sha256(bundle).hexdigest(), "size": len(bundle)},
                "wheelhouse": {"included": False, "files": []},
            }
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("release.json", json.dumps(manifest))
                package.writestr("repository.bundle", bundle)
                package.writestr("INSTALL.py", "pass\n")
                package.writestr("../escape", "bad")
            with self.assertRaisesRegex(ValueError, "unsicheren Pfad|unerwarteten Eintrag"):
                inspect_release_archive(archive)

    def test_offer_state_marks_obviously_new_version_available(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = SoftwareDistributionStore(root / "documents")
            offer = store.record_offer(
                "peer-a",
                {
                    "release": {"version": "999.0.0", "revision": "abc", "commit_count": 1, "build_epoch": 1},
                    "bundle": {"sha256": "a" * 64, "size": 123},
                },
            )
            self.assertEqual("available", offer["status"])
            self.assertEqual("peer-a", store.offers()[0]["peer_id"])

    def test_offline_restart_does_not_call_start_script(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools" / "self_deploy.py").read_text(encoding="utf-8")
        restart = script[script.index("def _offline_restart"):script.index("def _install_candidate_dependencies")]
        self.assertIn('"-m", "tools.launcher", "start"', restart)
        self.assertNotIn('"start.sh"', restart)
        self.assertNotIn('"start.bat"', restart)


if __name__ == "__main__":
    unittest.main()