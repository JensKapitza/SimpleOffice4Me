"""Offline self-deploy and federation software update primitives.

A release is a ZIP containing a Git bundle, an auditable release manifest and,
optionally, a wheelhouse. Runtime/customer data is never part of the release.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write
from .federation_core import build_manifest, manifest_valid, normalize_sha256, preallocate, verify_chunk, verify_file, write_chunk
from .federation_store import FederationStore
from .federation_worker import _json_request, _request

RELEASE_SCHEMA = 1
STATE_SCHEMA = 1
MAX_RELEASE_BYTES = 4 * 1024 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command: list[str], *, cwd: Path, timeout: int = 300) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()[-4000:]
        raise ValueError(detail)
    return result.stdout.strip()


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(part) for part in parts[:4]) or (0,)


def is_newer_release(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    candidate_version = _version_tuple(str(candidate.get("version", "0")))
    current_version = _version_tuple(str(current.get("version", "0")))
    if candidate_version != current_version:
        return candidate_version > current_version
    candidate_count = int(candidate.get("commit_count") or 0)
    current_count = int(current.get("commit_count") or 0)
    if candidate_count and current_count and candidate_count != current_count:
        return candidate_count > current_count
    candidate_epoch = int(candidate.get("build_epoch") or 0)
    current_epoch = int(current.get("build_epoch") or 0)
    if candidate_epoch != current_epoch:
        return candidate_epoch > current_epoch
    return False


def application_root() -> Path:
    return Path(__file__).resolve().parents[1]


def local_release_info(root: str | Path | None = None) -> dict[str, Any]:
    root_path = Path(root or application_root()).resolve()
    version = "0.0.0"
    try:
        data = tomllib.loads((root_path / "pyproject.toml").read_text(encoding="utf-8"))
        version = str(data.get("project", {}).get("version") or version)
    except (OSError, tomllib.TOMLDecodeError):
        pass
    installed_manifest = root_path / ".simpleoffice-release.json"
    try:
        installed = json.loads(installed_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        installed = {}
    revision = str(installed.get("revision") or "")
    branch = str(installed.get("branch") or "")
    build_epoch = int(installed.get("build_epoch") or 0)
    commit_count = int(installed.get("commit_count") or 0)
    if (root_path / ".git").exists() and shutil.which("git"):
        try:
            revision = _run(["git", "rev-parse", "HEAD"], cwd=root_path, timeout=20)
            branch = _run(["git", "branch", "--show-current"], cwd=root_path, timeout=20) or branch
            build_epoch = int(_run(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=root_path, timeout=20) or 0)
            commit_count = int(_run(["git", "rev-list", "--count", "HEAD"], cwd=root_path, timeout=30) or 0)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
    return {
        "schema": RELEASE_SCHEMA,
        "version": version,
        "revision": revision,
        "branch": branch or "main",
        "build_epoch": build_epoch,
        "commit_count": commit_count,
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


INSTALLER = r'''#!/usr/bin/env python3
import argparse, hashlib, json, platform, shutil, subprocess, sys, venv
from pathlib import Path

def run(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)

def main():
    parser = argparse.ArgumentParser(description="SimpleOffice4Me offline self deploy")
    parser.add_argument("target")
    parser.add_argument("--offline-install", action="store_true")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    target = Path(args.target).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise SystemExit("Target directory must be empty")
    if not shutil.which("git"):
        raise SystemExit("Git is required on the target PC")
    manifest = json.loads((here / "release.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256((here / "repository.bundle").read_bytes()).hexdigest()
    if digest != manifest["repository"]["sha256"]:
        raise SystemExit("Repository bundle hash mismatch")
    branch = manifest["release"].get("branch") or "main"
    run(["git", "clone", "-b", branch, str(here / "repository.bundle"), str(target)])
    revision = manifest["release"]["revision"]
    run(["git", "checkout", "--detach", revision], cwd=target)
    (target / ".simpleoffice-release.json").write_text(json.dumps(manifest["release"], sort_keys=True, indent=2), encoding="utf-8")
    wheels = here / "wheelhouse"
    if args.offline_install:
        release = manifest["release"]
        if release.get("platform") != sys.platform or release.get("machine") != platform.machine():
            raise SystemExit("Bundled wheels target a different platform/architecture")
        if not wheels.is_dir() or not any(wheels.glob("*.whl")):
            raise SystemExit("Archive contains no wheelhouse")
        env_dir = target / ".venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        py = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        run([str(py), "-m", "pip", "install", "--no-index", "--find-links", str(wheels), "--editable", str(target)])
        run([str(py), "-m", "pip", "check"])
    print(f"SimpleOffice4Me deployed to {target}")

if __name__ == "__main__":
    main()
'''


def _ensure_clean_git(root: Path) -> tuple[str, str]:
    if not shutil.which("git") or not (root / ".git").exists():
        raise ValueError("Self-Deploy benötigt eine Git-Arbeitskopie")
    if _run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, timeout=30):
        raise ValueError("Release abgebrochen: lokale getrackte Änderungen vorhanden")
    revision = _run(["git", "rev-parse", "HEAD"], cwd=root, timeout=20)
    branch = _run(["git", "branch", "--show-current"], cwd=root, timeout=20) or "main"
    if branch != "main" and os.environ.get("SIMPLEOFFICE_ALLOW_NON_MAIN_RELEASE", "0").strip().casefold() not in {"1", "true", "yes", "on"}:
        raise ValueError("Self-Deploy veröffentlicht standardmäßig nur den main-Branch")
    return revision, branch


def build_release_archive(destination: str | Path, *, root: str | Path | None = None, include_wheels: bool = False) -> dict[str, Any]:
    root_path = Path(root or application_root()).resolve()
    destination_path = Path(destination).expanduser().resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    revision, branch = _ensure_clean_git(root_path)
    release = local_release_info(root_path)
    release.update({"revision": revision, "branch": branch, "created_at": int(time.time())})
    with tempfile.TemporaryDirectory(prefix="simpleoffice-release-") as tmp:
        stage = Path(tmp)
        repo_bundle = stage / "repository.bundle"
        _run(["git", "bundle", "create", str(repo_bundle), branch], cwd=root_path, timeout=600)
        wheel_names: list[str] = []
        if include_wheels:
            wheelhouse = stage / "wheelhouse"
            wheelhouse.mkdir()
            _run([sys.executable, "-m", "pip", "download", "--dest", str(wheelhouse), "setuptools>=68", "wheel"], cwd=root_path, timeout=1800)
            _run([sys.executable, "-m", "pip", "download", "--dest", str(wheelhouse), str(root_path)], cwd=root_path, timeout=3600)
            wheel_names = sorted(path.name for path in wheelhouse.glob("*.whl"))
        manifest = {
            "schema": RELEASE_SCHEMA,
            "release": release,
            "repository": {"sha256": _sha256(repo_bundle), "size": repo_bundle.stat().st_size},
            "wheelhouse": {"included": bool(wheel_names), "files": wheel_names},
            "security": {"transport_hash": "sha256", "update_mode": "git-fast-forward-only"},
        }
        (stage / "release.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        (stage / "INSTALL.py").write_text(INSTALLER, encoding="utf-8")
        tmp_archive = stage / "release.zip"
        with zipfile.ZipFile(tmp_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name in ("release.json", "repository.bundle", "INSTALL.py"):
                archive.write(stage / name, name)
            for wheel in wheel_names:
                archive.write(stage / "wheelhouse" / wheel, f"wheelhouse/{wheel}")
        if tmp_archive.stat().st_size > MAX_RELEASE_BYTES:
            raise ValueError("Release-Paket überschreitet die maximale Größe")
        shutil.copy2(tmp_archive, destination_path)
    return {**manifest, "archive_sha256": _sha256(destination_path), "archive_size": destination_path.stat().st_size, "path": str(destination_path)}


def inspect_release_archive(path: str | Path) -> dict[str, Any]:
    archive_path = Path(path).resolve()
    if not archive_path.is_file() or archive_path.stat().st_size > MAX_RELEASE_BYTES:
        raise ValueError("Ungültiges oder zu großes Release-Paket")
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if any(required not in names for required in ("release.json", "repository.bundle", "INSTALL.py")):
            raise ValueError("Release-Paket ist unvollständig")
        for item in archive.infolist():
            candidate = Path(item.filename)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("Release enthält unsicheren Pfad")
            allowed = item.filename in {"release.json", "repository.bundle", "INSTALL.py"} or (item.filename.startswith("wheelhouse/") and item.filename.endswith(".whl") and len(candidate.parts) == 2)
            if not allowed:
                raise ValueError("Release enthält unerwarteten Eintrag")
            if item.file_size > MAX_RELEASE_BYTES:
                raise ValueError("Release-Eintrag ist zu groß")
        manifest = json.loads(archive.read("release.json").decode("utf-8"))
        if int(manifest.get("schema", 0)) != RELEASE_SCHEMA:
            raise ValueError("Unbekanntes Release-Schema")
        with tempfile.TemporaryDirectory(prefix="simpleoffice-verify-") as tmp:
            bundle = Path(tmp) / "repository.bundle"
            bundle.write_bytes(archive.read("repository.bundle"))
            expected = normalize_sha256(manifest.get("repository", {}).get("sha256", ""))
            if _sha256(bundle) != expected:
                raise ValueError("Git-Bundle-Hash stimmt nicht")
    manifest["archive_sha256"] = _sha256(archive_path)
    manifest["archive_size"] = archive_path.stat().st_size
    return manifest


def _extract_release(path: Path, destination: Path) -> dict[str, Any]:
    manifest = inspect_release_archive(path)
    with zipfile.ZipFile(path, "r") as archive:
        for item in archive.infolist():
            target = destination / item.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item, "r") as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
    return manifest


def clone_release_archive(path: str | Path, target: str | Path, *, offline_install: bool = False) -> dict[str, Any]:
    archive_path = Path(path).resolve()
    target_path = Path(target).expanduser().resolve()
    if target_path.exists() and any(target_path.iterdir()):
        raise ValueError("Zielordner muss leer sein")
    if not shutil.which("git"):
        raise ValueError("Git ist auf dem Zielrechner erforderlich")
    with tempfile.TemporaryDirectory(prefix="simpleoffice-clone-") as tmp:
        stage = Path(tmp)
        manifest = _extract_release(archive_path, stage)
        release = manifest["release"]
        _run(["git", "clone", "-b", str(release.get("branch") or "main"), str(stage / "repository.bundle"), str(target_path)], cwd=stage, timeout=900)
        _run(["git", "checkout", "--detach", str(release["revision"])], cwd=target_path, timeout=120)
        (target_path / ".simpleoffice-release.json").write_text(json.dumps(release, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        wheels = stage / "wheelhouse"
        if offline_install:
            if release.get("platform") != sys.platform or release.get("machine") != platform.machine():
                raise ValueError("Wheelhouse passt nicht zu Plattform/Architektur des Zielrechners")
            if not wheels.is_dir() or not any(wheels.glob("*.whl")):
                raise ValueError("Release enthält kein Wheelhouse für eine Offline-Installation")
            import venv
            venv.EnvBuilder(with_pip=True).create(target_path / ".venv")
            py = target_path / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            _run([str(py), "-m", "pip", "install", "--no-index", "--find-links", str(wheels), "--editable", str(target_path)], cwd=target_path, timeout=1800)
            _run([str(py), "-m", "pip", "check"], cwd=target_path, timeout=300)
    return {"target": str(target_path), "release": release}


def apply_release_archive(path: str | Path, *, root: str | Path | None = None, install_dependencies: bool = True) -> dict[str, Any]:
    root_path = Path(root or application_root()).resolve()
    archive_path = Path(path).resolve()
    if not (root_path / ".git").is_dir():
        raise ValueError("Offline-Update benötigt eine Git-Arbeitskopie")
    if _run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root_path, timeout=30):
        raise ValueError("Update abgebrochen: lokale getrackte Änderungen vorhanden")
    old_revision = _run(["git", "rev-parse", "HEAD"], cwd=root_path, timeout=20)
    with tempfile.TemporaryDirectory(prefix="simpleoffice-apply-") as tmp:
        stage = Path(tmp)
        manifest = _extract_release(archive_path, stage)
        release = manifest["release"]
        revision = str(release.get("revision") or "")
        branch = str(release.get("branch") or "main")
        _run(["git", "bundle", "verify", str(stage / "repository.bundle")], cwd=root_path, timeout=120)
        _run(["git", "fetch", str(stage / "repository.bundle"), f"{branch}:refs/remotes/offline/{branch}"], cwd=root_path, timeout=600)
        _run(["git", "merge", "--ff-only", revision], cwd=root_path, timeout=600)
        (root_path / ".simpleoffice-release.json").write_text(json.dumps(release, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        wheels = stage / "wheelhouse"
        if install_dependencies and wheels.is_dir() and any(wheels.glob("*.whl")):
            venv_python = root_path / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            if venv_python.exists():
                _run([str(venv_python), "-m", "pip", "install", "--no-index", "--find-links", str(wheels), "--editable", str(root_path)], cwd=root_path, timeout=1800)
                _run([str(venv_python), "-m", "pip", "check"], cwd=root_path, timeout=300)
    return {"old_revision": old_revision, "new_revision": revision, "release": release}


class SoftwareDistributionStore:
    def __init__(self, document_root: str | Path):
        self.document_root = Path(document_root).expanduser().resolve()
        self.control = self.document_root / CONTROL_DIR
        self.base = self.control / "software-distribution"
        self.releases = self.base / "releases"
        self.incoming = self.base / "incoming"
        self.state_path = self.base / "state.json"
        self.initialize()

    def initialize(self) -> None:
        self.releases.mkdir(parents=True, exist_ok=True)
        self.incoming.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            atomic_json_write(self.state_path, {"schema": STATE_SCHEMA, "releases": [], "offers": [], "staged": []})

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass
        return {"schema": STATE_SCHEMA, "releases": [], "offers": [], "staged": []}

    def _save(self, state: dict[str, Any]) -> None:
        atomic_json_write(self.state_path, state)

    def build(self, *, include_wheels: bool = False) -> dict[str, Any]:
        tmp = self.releases / f"simpleoffice-release-{int(time.time())}.zip.tmp"
        result = build_release_archive(tmp, include_wheels=include_wheels)
        digest = result["archive_sha256"]
        final = self.releases / f"{digest}.zip"
        tmp.replace(final)
        state = self._read()
        entry = {key: result[key] for key in ("archive_sha256", "archive_size", "release", "repository", "wheelhouse")}
        state["releases"] = [entry] + [item for item in state.get("releases", []) if item.get("archive_sha256") != digest]
        state["releases"] = state["releases"][:20]
        self._save(state)
        return entry

    def latest(self) -> dict[str, Any] | None:
        releases = self._read().get("releases", [])
        return releases[0] if releases else None

    def release_path(self, digest: str) -> Path:
        digest = normalize_sha256(digest)
        path = (self.releases / f"{digest}.zip").resolve()
        if self.releases.resolve() not in path.parents or not path.is_file() or _sha256(path) != digest:
            raise ValueError("Release nicht verfügbar")
        return path

    def offers(self) -> list[dict[str, Any]]:
        return list(self._read().get("offers", []))

    def record_offer(self, peer_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        release = payload.get("release") or {}
        bundle = payload.get("bundle") or {}
        digest = normalize_sha256(bundle.get("sha256", ""))
        entry = {"peer_id": peer_id, "release": release, "bundle": {"sha256": digest, "size": int(bundle.get("size") or 0)}, "offered_at": int(time.time()), "status": "available" if is_newer_release(release, local_release_info()) else "not-newer"}
        state = self._read()
        state["offers"] = [entry] + [item for item in state.get("offers", []) if not (item.get("peer_id") == peer_id and item.get("bundle", {}).get("sha256") == digest)]
        state["offers"] = state["offers"][:100]
        self._save(state)
        return entry

    def staged(self) -> list[dict[str, Any]]:
        return list(self._read().get("staged", []))

    def stage_from_peer(self, peer_id: str) -> dict[str, Any]:
        federation = FederationStore(self.document_root)
        peer = federation.get_peer(peer_id)
        if not peer or not peer.get("enabled"):
            raise ValueError("Federation-Peer ist nicht aktiv")
        policy = peer.get("policy") or {}
        software_policy = policy.get("software", {}) if isinstance(policy, dict) else {}
        if software_policy.get("receive") is not True:
            raise ValueError("Peer-Policy muss software.receive=true explizit erlauben")
        token = federation.peer_token(peer_id)
        remote = _json_request(peer["base_url"] + "/federation/v1/software/releases/current", token=token, timeout=60)
        release = remote.get("release") or {}
        if not is_newer_release(release, local_release_info()):
            raise ValueError("Peer bietet keine neuere Version an")
        remote_manifest = remote.get("manifest") or {}
        if not manifest_valid(remote_manifest):
            raise ValueError("Peer lieferte kein gültiges Software-Manifest")
        digest = normalize_sha256(remote.get("bundle", {}).get("sha256", ""))
        if normalize_sha256(remote_manifest.get("blob_hash", "")) != digest:
            raise ValueError("Software-Manifest passt nicht zum Bundle")
        size = int(remote_manifest.get("size", 0))
        if size <= 0 or size > MAX_RELEASE_BYTES:
            raise ValueError("Ungültige Release-Größe")
        partial = self.incoming / f"{digest}.part"
        preallocate(partial, size)
        chunks = remote_manifest.get("chunks") or []
        for chunk in chunks:
            index = int(chunk["index"])
            offset = int(chunk["offset"])
            length = int(chunk["length"])
            with partial.open("rb") as handle:
                handle.seek(offset)
                existing = handle.read(length)
            if len(existing) == length and verify_chunk(existing, chunk["hash"]):
                continue
            url = f"{peer['base_url']}/federation/v1/software/releases/{digest}/chunks/{index}"
            with _request(url, token=token, timeout=120) as response:
                data = response.read()
            if len(data) != length or not verify_chunk(data, chunk["hash"]):
                raise ValueError(f"Release-Chunk {index} ist beschädigt")
            write_chunk(partial, offset, data)
        if not verify_file(partial, digest):
            raise ValueError("Release-Gesamthash stimmt nicht")
        final = self.incoming / f"{digest}.zip"
        partial.replace(final)
        verified = inspect_release_archive(final)
        entry = {"peer_id": peer_id, "sha256": digest, "path": str(final), "release": verified["release"], "staged_at": int(time.time())}
        state = self._read()
        state["staged"] = [entry] + [item for item in state.get("staged", []) if item.get("sha256") != digest]
        state["staged"] = state["staged"][:20]
        self._save(state)
        return entry

    def staged_path(self, digest: str) -> Path:
        digest = normalize_sha256(digest)
        path = (self.incoming / f"{digest}.zip").resolve()
        if self.incoming.resolve() not in path.parents or not path.is_file() or _sha256(path) != digest:
            raise ValueError("Gestagtes Release nicht verfügbar")
        return path
