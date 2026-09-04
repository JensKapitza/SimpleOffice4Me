import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

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


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "SimpleOffice Test")
    (root / "pyproject.toml").write_text('[project]\nname="simpleoffice4me"\nversion="1.2.3"\n', encoding="utf-8")
    (root / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "v1")
    return root


def _commit(root: Path, value: str) -> str:
    (root / "app.txt").write_text(value + "\n", encoding="utf-8")
    _git(root, "add", "app.txt")
    _git(root, "commit", "-m", value)
    return _git(root, "rev-parse", "HEAD")


def test_release_order_prefers_version_then_commit_count():
    current = {"version": "1.2.3", "commit_count": 10, "build_epoch": 100, "revision": "a"}
    assert is_newer_release({"version": "1.2.4", "commit_count": 1, "build_epoch": 1}, current)
    assert is_newer_release({"version": "1.2.3", "commit_count": 11, "build_epoch": 90}, current)
    assert not is_newer_release({"version": "1.2.3", "commit_count": 9, "build_epoch": 200}, current)


def test_project_version_reader_remains_python_310_compatible(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git unavailable")
    source = _repo(tmp_path)
    assert local_release_info(source)["version"] == "1.2.3"


def test_self_deploy_bundle_clones_without_network(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git unavailable")
    source = _repo(tmp_path)
    archive = tmp_path / "release.zip"
    built = build_release_archive(archive, root=source)
    checked = inspect_release_archive(archive)
    assert checked["archive_sha256"] == built["archive_sha256"]
    target = tmp_path / "target"
    result = clone_release_archive(archive, target)
    assert (target / "app.txt").read_text(encoding="utf-8") == "v1\n"
    assert result["release"]["version"] == "1.2.3"
    installed = json.loads((target / ".simpleoffice-release.json").read_text(encoding="utf-8"))
    assert installed["revision"] == built["release"]["revision"]


def test_offline_update_fast_forwards_and_rejects_dirty_worktree(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git unavailable")
    source = _repo(tmp_path)
    first = tmp_path / "first.zip"
    build_release_archive(first, root=source)
    target = tmp_path / "target"
    clone_release_archive(first, target)

    new_revision = _commit(source, "v2")
    second = tmp_path / "second.zip"
    build_release_archive(second, root=source)
    result = apply_release_archive(second, root=target, install_dependencies=False)
    assert result["new_revision"] == new_revision
    assert (target / "app.txt").read_text(encoding="utf-8") == "v2\n"

    (target / "app.txt").write_text("local\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lokale getrackte Änderungen"):
        apply_release_archive(second, root=target, install_dependencies=False)


def test_offline_update_rejects_downgrade(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git unavailable")
    source = _repo(tmp_path)
    old_archive = tmp_path / "old.zip"
    build_release_archive(old_archive, root=source)
    target = tmp_path / "target"
    clone_release_archive(old_archive, target)
    _commit(source, "v2")
    new_archive = tmp_path / "new.zip"
    build_release_archive(new_archive, root=source)
    apply_release_archive(new_archive, root=target, install_dependencies=False)
    with pytest.raises(ValueError, match="nicht neuer"):
        apply_release_archive(old_archive, root=target, install_dependencies=False)


def test_offline_update_rejects_divergent_history(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git unavailable")
    source = _repo(tmp_path)
    first = tmp_path / "first.zip"
    build_release_archive(first, root=source)
    target = tmp_path / "target"
    clone_release_archive(first, target)
    _git(target, "config", "user.email", "target@example.invalid")
    _git(target, "config", "user.name", "Target")
    (target / "local.txt").write_text("local\n", encoding="utf-8")
    _git(target, "add", "local.txt")
    _git(target, "commit", "-m", "local branch")

    _commit(source, "v2")
    _commit(source, "v3")
    candidate = tmp_path / "candidate.zip"
    build_release_archive(candidate, root=source)
    with pytest.raises(ValueError, match="kein Fast-Forward"):
        apply_release_archive(candidate, root=target, install_dependencies=False)


def test_archive_rejects_unexpected_paths(tmp_path):
    archive = tmp_path / "bad.zip"
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
    with pytest.raises(ValueError, match="unsicheren Pfad|unerwarteten Eintrag"):
        inspect_release_archive(archive)


def test_offer_state_marks_obviously_new_version_available(tmp_path):
    store = SoftwareDistributionStore(tmp_path / "documents")
    offer = store.record_offer(
        "peer-a",
        {
            "release": {"version": "999.0.0", "revision": "abc", "commit_count": 1, "build_epoch": 1},
            "bundle": {"sha256": "a" * 64, "size": 123},
        },
    )
    assert offer["status"] == "available"
    assert store.offers()[0]["peer_id"] == "peer-a"


def test_offline_restart_does_not_call_start_script():
    root = Path(__file__).resolve().parents[1]
    script = (root / "tools" / "self_deploy.py").read_text(encoding="utf-8")
    restart = script[script.index("def _offline_restart"):script.index("def _install_candidate_dependencies")]
    assert '"-m", "tools.launcher", "start"' in restart
    assert "start.sh" not in restart
    assert "start.bat" not in restart
