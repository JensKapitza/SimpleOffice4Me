"""Git-independent self-deploy and federation software release primitives.

Release archives contain an auditable file payload, a manifest and optionally a
wheelhouse.  No Git repository, Git bundle or Git executable is required on the
builder, target or federation peer.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write
from .federation_core import manifest_valid, normalize_sha256, preallocate, verify_chunk, verify_file, write_chunk
from .federation_store import FederationStore
from .federation_worker import _json_request, _request
from .file_lock import exclusive_file_lock
from .safe_paths import resolve_under

RELEASE_SCHEMA = 2
STATE_SCHEMA = 1
MAX_RELEASE_BYTES = 4 * 1024 * 1024 * 1024
MAX_RELEASE_ENTRIES = 20000
MAX_RELEASE_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
PAYLOAD_PREFIX = "payload/"
MANAGED_DIRS = (
    "app", "tools", "templates", "static", "desktop", "android", "deploy", "docs", "database",
)
PROTECTED_NAMES = {
    ".git", ".venv", "instance", ".simpleoffice-history", ".simpleoffice-control", "node_modules",
    "__pycache__", ".pytest_cache", "build", "dist", "var",
}


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


def _project_version(root: Path) -> str:
    try:
        lines = (root / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "0.0.0"
    in_project = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project:
            match = re.match(r'''version\s*=\s*["']([^"']+)["']''', line)
            if match:
                return match.group(1).strip() or "0.0.0"
    return "0.0.0"


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
    return str(candidate.get("revision") or "") != str(current.get("revision") or "")


def _wheelhouse_matches(release: dict[str, Any]) -> bool:
    return (
        release.get("platform") == sys.platform
        and release.get("machine") == platform.machine()
        and release.get("python") == f"{sys.version_info.major}.{sys.version_info.minor}"
    )


def application_root() -> Path:
    return Path(__file__).resolve().parents[1]


def local_release_info(root: str | Path | None = None) -> dict[str, Any]:
    root_path = Path(root or application_root()).resolve()
    installed: dict[str, Any] = {}
    try:
        value = json.loads((root_path / ".simpleoffice-release.json").read_text(encoding="utf-8"))
        if isinstance(value, dict):
            installed = value
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "schema": RELEASE_SCHEMA,
        "version": _project_version(root_path),
        "revision": str(installed.get("revision") or os.environ.get("SIMPLEOFFICE_BUILD_REVISION") or ""),
        "branch": str(installed.get("branch") or os.environ.get("SIMPLEOFFICE_BUILD_BRANCH") or "main"),
        "build_epoch": int(installed.get("build_epoch") or os.environ.get("SIMPLEOFFICE_BUILD_EPOCH") or 0),
        "commit_count": int(installed.get("commit_count") or os.environ.get("SIMPLEOFFICE_BUILD_NUMBER") or 0),
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _payload_sources(root: Path) -> list[Path]:
    result: list[Path] = []
    for directory in MANAGED_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.is_symlink() or any(part in PROTECTED_NAMES for part in path.relative_to(root).parts):
                continue
            result.append(path)
    for path in root.iterdir():
        if not path.is_file() or path.is_symlink() or path.name.startswith(".") or path.name in PROTECTED_NAMES:
            continue
        result.append(path)
    return sorted(set(result), key=lambda item: item.relative_to(root).as_posix().casefold())


def _payload_manifest(root: Path, files: list[Path]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    tree = hashlib.sha256()
    total = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        digest = _sha256(path)
        mode = stat.S_IMODE(path.stat().st_mode)
        item = {"path": relative, "sha256": digest, "size": size, "mode": mode}
        entries.append(item)
        tree.update(json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        tree.update(b"\n")
        total += size
    return {"files": entries, "count": len(entries), "size": total, "tree_sha256": tree.hexdigest()}


def _release_identity(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    release = local_release_info(root)
    revision = str(release.get("revision") or "").strip()
    if not revision:
        revision = str(payload["tree_sha256"])
    release.update({"revision": revision, "created_at": int(time.time()), "package_schema": RELEASE_SCHEMA})
    return release


INSTALLER = r'''#!/usr/bin/env python3
import argparse, hashlib, json, platform, shutil, subprocess, sys, venv, zipfile
from pathlib import Path

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

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
    package = here / "release-package.zip"
    manifest = json.loads((here / "release.json").read_text(encoding="utf-8"))
    target = Path(args.target).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise SystemExit("Target directory must be empty")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package) as archive:
        archive.extractall(target)
    for item in manifest["payload"]["files"]:
        path = target / item["path"]
        if not path.is_file() or digest(path) != item["sha256"]:
            raise SystemExit("Payload verification failed")
    release = manifest["release"]
    wheels = here / "wheelhouse"
    if args.offline_install:
        tag = f"{sys.version_info.major}.{sys.version_info.minor}"
        if release.get("platform") != sys.platform or release.get("machine") != platform.machine() or release.get("python") != tag:
            raise SystemExit("Bundled wheels target a different platform/architecture/Python version")
        if not wheels.is_dir() or not any(wheels.glob("*.whl")):
            raise SystemExit("Archive contains no wheelhouse")
        env = target / ".venv"
        venv.EnvBuilder(with_pip=True).create(env)
        py = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        run([str(py), "-m", "pip", "install", "--no-index", "--find-links", str(wheels), "--editable", str(target)])
        run([str(py), "-m", "pip", "check"], cwd=target)
    (target / ".simpleoffice-release.json").write_text(json.dumps(release, sort_keys=True, indent=2), encoding="utf-8")
    print(f"SimpleOffice4Me deployed to {target}")

if __name__ == "__main__":
    main()
'''


def build_release_archive(destination: str | Path, *, root: str | Path | None = None, include_wheels: bool = False) -> dict[str, Any]:
    root_path = Path(root or application_root()).resolve()
    destination_path = Path(destination).expanduser().resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    files = _payload_sources(root_path)
    if not files or not (root_path / "pyproject.toml").is_file() or not (root_path / "app").is_dir():
        raise ValueError("Release-Quelle ist kein vollständiger SimpleOffice4Me-Programmbaum")
    payload = _payload_manifest(root_path, files)
    release = _release_identity(root_path, payload)
    with tempfile.TemporaryDirectory(prefix="simpleoffice-release-") as tmp:
        stage = Path(tmp)
        payload_zip = stage / "release-package.zip"
        with zipfile.ZipFile(payload_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in files:
                archive.write(path, path.relative_to(root_path).as_posix())
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
            "payload": {**payload, "archive_sha256": _sha256(payload_zip), "archive_size": payload_zip.stat().st_size},
            "repository": {"sha256": _sha256(payload_zip), "size": payload_zip.stat().st_size, "format": "file-payload-v2"},
            "wheelhouse": {"included": bool(wheel_names), "files": wheel_names},
            "security": {"transport_hash": "sha256", "update_mode": "transactional-file-replacement"},
        }
        (stage / "release.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        (stage / "INSTALL.py").write_text(INSTALLER, encoding="utf-8")
        archive_path = stage / "release.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name in ("release.json", "release-package.zip", "INSTALL.py"):
                archive.write(stage / name, name)
            for wheel in wheel_names:
                archive.write(stage / "wheelhouse" / wheel, f"wheelhouse/{wheel}")
        if archive_path.stat().st_size > MAX_RELEASE_BYTES:
            raise ValueError("Release-Paket überschreitet die maximale Größe")
        shutil.copy2(archive_path, destination_path)
    return {**manifest, "archive_sha256": _sha256(destination_path), "archive_size": destination_path.stat().st_size, "path": str(destination_path)}


def _validated_release_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    entries = archive.infolist()
    if not entries or len(entries) > MAX_RELEASE_ENTRIES:
        raise ValueError("Release enthält eine ungültige Anzahl Einträge")
    seen: set[str] = set()
    total = 0
    for item in entries:
        name = item.filename.replace("\\", "/")
        candidate = PurePosixPath(name)
        if not name or candidate.is_absolute() or ".." in candidate.parts or ":" in candidate.parts[0]:
            raise ValueError("Release enthält unsicheren Pfad")
        if name in seen:
            raise ValueError("Release enthält doppelte Einträge")
        seen.add(name)
        if ((item.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
            raise ValueError("Release enthält symbolischen Link")
        allowed = name in {"release.json", "release-package.zip", "INSTALL.py"} or (name.startswith("wheelhouse/") and name.endswith(".whl") and len(candidate.parts) == 2)
        if not allowed:
            raise ValueError("Release enthält unerwarteten Eintrag")
        total += max(0, item.file_size)
        if item.file_size > MAX_RELEASE_BYTES or total > MAX_RELEASE_UNCOMPRESSED_BYTES:
            raise ValueError("Release überschreitet die maximale Größe")
    if any(name not in seen for name in ("release.json", "release-package.zip", "INSTALL.py")):
        raise ValueError("Release-Paket ist unvollständig")
    return entries


def _verify_payload_archive(payload_zip: Path, payload: dict[str, Any]) -> None:
    expected_archive = normalize_sha256(payload.get("archive_sha256", ""))
    if _sha256(payload_zip) != expected_archive:
        raise ValueError("Payload-Archiv-Hash stimmt nicht")
    files = payload.get("files") or []
    expected = {str(item.get("path") or ""): item for item in files if isinstance(item, dict)}
    if len(expected) != int(payload.get("count") or -1):
        raise ValueError("Payload-Dateiliste ist inkonsistent")
    tree = hashlib.sha256()
    with zipfile.ZipFile(payload_zip, "r") as archive:
        seen: set[str] = set()
        for item in archive.infolist():
            name = item.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if item.is_dir():
                continue
            if path.is_absolute() or ".." in path.parts or name not in expected or name in seen:
                raise ValueError("Payload enthält unerwarteten oder unsicheren Pfad")
            if ((item.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
                raise ValueError("Payload enthält symbolischen Link")
            data = archive.read(item)
            definition = expected[name]
            if len(data) != int(definition.get("size") or -1) or hashlib.sha256(data).hexdigest() != normalize_sha256(definition.get("sha256", "")):
                raise ValueError(f"Payload-Datei ist beschädigt: {name}")
            canonical = {"path": name, "sha256": definition["sha256"], "size": int(definition["size"]), "mode": int(definition.get("mode") or 0)}
            tree.update(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")); tree.update(b"\n")
            seen.add(name)
    if seen != set(expected) or tree.hexdigest() != normalize_sha256(payload.get("tree_sha256", "")):
        raise ValueError("Payload-Baum stimmt nicht mit dem Manifest überein")


def inspect_release_archive(path: str | Path) -> dict[str, Any]:
    archive_path = Path(path).resolve()
    if not archive_path.is_file() or archive_path.is_symlink() or archive_path.stat().st_size > MAX_RELEASE_BYTES:
        raise ValueError("Ungültiges oder zu großes Release-Paket")
    with zipfile.ZipFile(archive_path, "r") as archive:
        _validated_release_entries(archive)
        manifest = json.loads(archive.read("release.json").decode("utf-8"))
        if int(manifest.get("schema", 0)) != RELEASE_SCHEMA:
            raise ValueError("Unbekanntes Release-Schema")
        release = manifest.get("release") or {}
        if not str(release.get("revision") or "") or not str(release.get("branch") or ""):
            raise ValueError("Release-Metadaten sind unvollständig")
        with tempfile.TemporaryDirectory(prefix="simpleoffice-payload-verify-") as tmp:
            payload_zip = Path(tmp) / "release-package.zip"
            payload_zip.write_bytes(archive.read("release-package.zip"))
            _verify_payload_archive(payload_zip, manifest.get("payload") or {})
    manifest["archive_sha256"] = _sha256(archive_path)
    manifest["archive_size"] = archive_path.stat().st_size
    return manifest


def _extract_release(path: Path, destination: Path) -> dict[str, Any]:
    manifest = inspect_release_archive(path)
    destination = destination.resolve(strict=True)
    with zipfile.ZipFile(path, "r") as archive:
        for item in _validated_release_entries(archive):
            target = resolve_under(destination, item.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item, "r") as source, target.open("xb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
    return manifest


def _extract_payload(payload_zip: Path, destination: Path, payload: dict[str, Any]) -> None:
    expected = {str(item["path"]): item for item in payload.get("files", [])}
    with zipfile.ZipFile(payload_zip, "r") as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            name = item.filename.replace("\\", "/")
            target = resolve_under(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open("xb") as sink:
                shutil.copyfileobj(source, sink, 1024 * 1024)
            mode = int(expected[name].get("mode") or 0)
            if mode and os.name == "posix":
                target.chmod(mode)


def _managed_top_files(source: Path) -> list[Path]:
    return sorted((path for path in source.iterdir() if path.is_file() and not path.name.startswith(".")), key=lambda item: item.name.casefold())


def _restore(root: Path, backup: Path, replaced: list[str]) -> None:
    for name in reversed(replaced):
        target, saved = root / name, backup / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        if saved.exists() or saved.is_symlink():
            saved.rename(target)


def _replace_program_tree(root: Path, source: Path, backup: Path) -> None:
    replaced: list[str] = []
    try:
        for name in MANAGED_DIRS:
            candidate = source / name
            if not candidate.exists():
                continue
            target, saved = root / name, backup / name
            if target.exists() or target.is_symlink():
                target.rename(saved)
            shutil.copytree(candidate, target)
            replaced.append(name)
        for candidate in _managed_top_files(source):
            name = candidate.name
            if name in PROTECTED_NAMES:
                continue
            target, saved = root / name, backup / name
            if target.exists() or target.is_symlink():
                target.rename(saved)
            shutil.copy2(candidate, target)
            replaced.append(name)
    except Exception:
        _restore(root, backup, replaced)
        raise


def _install_wheels(stage: Path, target: Path, release: dict[str, Any], *, create_venv: bool) -> None:
    wheels = stage / "wheelhouse"
    if not wheels.is_dir() or not any(wheels.glob("*.whl")):
        raise ValueError("Release enthält kein Wheelhouse für eine Offline-Installation")
    if not _wheelhouse_matches(release):
        raise ValueError("Wheelhouse passt nicht zu Plattform/Architektur/Python des Zielrechners")
    env = target / ".venv"
    if create_venv:
        import venv
        venv.EnvBuilder(with_pip=True).create(env)
    py = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not py.exists():
        raise ValueError("Lokale .venv fehlt")
    _run([str(py), "-m", "pip", "install", "--no-index", "--find-links", str(wheels), "--editable", str(target)], cwd=target, timeout=1800)
    _run([str(py), "-m", "pip", "check"], cwd=target, timeout=300)


def clone_release_archive(path: str | Path, target: str | Path, *, offline_install: bool = False) -> dict[str, Any]:
    archive_path = Path(path).resolve()
    target_path = Path(target).expanduser().resolve()
    if target_path.exists() and any(target_path.iterdir()):
        raise ValueError("Zielordner muss leer sein")
    target_path.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="simpleoffice-clone-") as tmp:
            stage = Path(tmp)
            manifest = _extract_release(archive_path, stage)
            _extract_payload(stage / "release-package.zip", target_path, manifest["payload"])
            if offline_install:
                _install_wheels(stage, target_path, manifest["release"], create_venv=True)
            (target_path / ".simpleoffice-release.json").write_text(json.dumps(manifest["release"], ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        return {"target": str(target_path), "release": manifest["release"]}
    except Exception:
        if target_path.exists():
            shutil.rmtree(target_path)
        raise


def apply_release_archive(path: str | Path, *, root: str | Path | None = None, install_dependencies: bool = True) -> dict[str, Any]:
    root_path = Path(root or application_root()).resolve()
    current = local_release_info(root_path)
    archive_path = Path(path).resolve()
    with tempfile.TemporaryDirectory(prefix="simpleoffice-apply-", dir=str(root_path.parent)) as tmp:
        work = Path(tmp)
        stage = work / "stage"; stage.mkdir()
        source = work / "source"; source.mkdir()
        backup = work / "backup"; backup.mkdir()
        manifest = _extract_release(archive_path, stage)
        release = manifest["release"]
        if not is_newer_release(release, current):
            raise ValueError("Update abgebrochen: Release ist nicht neuer als der lokale Stand")
        _extract_payload(stage / "release-package.zip", source, manifest["payload"])
        _replace_program_tree(root_path, source, backup)
        if install_dependencies and (manifest.get("wheelhouse") or {}).get("included"):
            _install_wheels(stage, root_path, release, create_venv=False)
        (root_path / ".simpleoffice-release.json").write_text(json.dumps(release, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    return {"old_revision": str(current.get("revision") or ""), "new_revision": str(release["revision"]), "release": release}


class SoftwareDistributionStore:
    def __init__(self, document_root: str | Path):
        self.document_root = Path(document_root).expanduser().resolve()
        self.control = self.document_root / CONTROL_DIR
        self.base = self.control / "software-distribution"
        self.releases = self.base / "releases"
        self.incoming = self.base / "incoming"
        self.state_path = self.base / "state.json"
        self.lock_path = self.base / ".state-write.lock"
        self.initialize()

    def initialize(self) -> None:
        self.releases.mkdir(parents=True, exist_ok=True); self.incoming.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            with exclusive_file_lock(self.lock_path):
                if not self.state_path.exists():
                    atomic_json_write(self.state_path, {"schema": STATE_SCHEMA, "releases": [], "offers": [], "staged": []})

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(value, dict): return value
        except (OSError, json.JSONDecodeError): pass
        return {"schema": STATE_SCHEMA, "releases": [], "offers": [], "staged": []}

    def _save(self, state: dict[str, Any]) -> None:
        with exclusive_file_lock(self.lock_path): atomic_json_write(self.state_path, state)

    def build(self, *, include_wheels: bool = False) -> dict[str, Any]:
        tmp = self.releases / f"simpleoffice-release-{int(time.time())}.zip.tmp"
        result = build_release_archive(tmp, include_wheels=include_wheels)
        digest = result["archive_sha256"]; final = self.releases / f"{digest}.zip"; tmp.replace(final)
        state = self._read()
        entry = {key: result[key] for key in ("archive_sha256", "archive_size", "release", "repository", "payload", "wheelhouse")}
        state["releases"] = [entry] + [item for item in state.get("releases", []) if item.get("archive_sha256") != digest]
        state["releases"] = state["releases"][:20]; self._save(state); return entry

    def latest(self) -> dict[str, Any] | None:
        releases = self._read().get("releases", []); return releases[0] if releases else None

    def release_path(self, digest: str) -> Path:
        digest = normalize_sha256(digest); path = (self.releases / f"{digest}.zip").resolve()
        if self.releases.resolve() not in path.parents or not path.is_file() or _sha256(path) != digest: raise ValueError("Release nicht verfügbar")
        return path

    def offers(self) -> list[dict[str, Any]]: return list(self._read().get("offers", []))

    def record_offer(self, peer_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        release = payload.get("release") or {}; bundle = payload.get("bundle") or {}; digest = normalize_sha256(bundle.get("sha256", ""))
        entry = {"peer_id": peer_id, "release": release, "bundle": {"sha256": digest, "size": int(bundle.get("size") or 0)}, "offered_at": int(time.time()), "status": "available" if is_newer_release(release, local_release_info()) else "not-newer"}
        state = self._read(); state["offers"] = [entry] + [item for item in state.get("offers", []) if not (item.get("peer_id") == peer_id and item.get("bundle", {}).get("sha256") == digest)]
        state["offers"] = state["offers"][:100]; self._save(state); return entry

    def staged(self) -> list[dict[str, Any]]: return list(self._read().get("staged", []))

    def stage_from_peer(self, peer_id: str) -> dict[str, Any]:
        federation = FederationStore(self.document_root); peer = federation.get_peer(peer_id)
        if not peer or not peer.get("enabled"): raise ValueError("Federation-Peer ist nicht aktiv")
        policy = peer.get("policy") or {}; software_policy = policy.get("software", {}) if isinstance(policy, dict) else {}
        if software_policy.get("receive") is not True: raise ValueError("Peer-Policy muss software.receive=true explizit erlauben")
        token = federation.peer_token(peer_id); remote = _json_request(peer["base_url"] + "/federation/v1/software/releases/current", token=token, timeout=60)
        release = remote.get("release") or {}
        if not is_newer_release(release, local_release_info()): raise ValueError("Peer bietet keine neuere Version an")
        remote_manifest = remote.get("manifest") or {}
        if not manifest_valid(remote_manifest): raise ValueError("Peer lieferte kein gültiges Software-Manifest")
        digest = normalize_sha256(remote.get("bundle", {}).get("sha256", ""))
        if normalize_sha256(remote_manifest.get("blob_hash", "")) != digest: raise ValueError("Software-Manifest passt nicht zum Release")
        size = int(remote_manifest.get("size", 0))
        if size <= 0 or size > MAX_RELEASE_BYTES: raise ValueError("Ungültige Release-Größe")
        partial = self.incoming / f"{digest}.part"; preallocate(partial, size)
        for chunk in remote_manifest.get("chunks") or []:
            index, offset, length = int(chunk["index"]), int(chunk["offset"]), int(chunk["length"])
            with partial.open("rb") as handle: handle.seek(offset); existing = handle.read(length)
            if len(existing) == length and verify_chunk(existing, chunk["hash"]): continue
            url = f"{peer['base_url']}/federation/v1/software/releases/{digest}/chunks/{index}"
            with _request(url, token=token, timeout=120) as response: data = response.read()
            if len(data) != length or not verify_chunk(data, chunk["hash"]): raise ValueError(f"Release-Chunk {index} ist beschädigt")
            write_chunk(partial, offset, data)
        if not verify_file(partial, digest): raise ValueError("Release-Gesamthash stimmt nicht")
        final = self.incoming / f"{digest}.zip"; partial.replace(final); verified = inspect_release_archive(final)
        entry = {"peer_id": peer_id, "sha256": digest, "path": str(final), "release": verified["release"], "staged_at": int(time.time())}
        state = self._read(); state["staged"] = [entry] + [item for item in state.get("staged", []) if item.get("sha256") != digest]
        state["staged"] = state["staged"][:20]; self._save(state); return entry

    def staged_path(self, digest: str) -> Path:
        digest = normalize_sha256(digest); path = (self.incoming / f"{digest}.zip").resolve()
        if self.incoming.resolve() not in path.parents or not path.is_file() or _sha256(path) != digest: raise ValueError("Gestagtes Release nicht verfügbar")
        return path


__all__ = ["SoftwareDistributionStore", "apply_release_archive", "application_root", "build_release_archive", "clone_release_archive", "inspect_release_archive", "is_newer_release", "local_release_info"]
