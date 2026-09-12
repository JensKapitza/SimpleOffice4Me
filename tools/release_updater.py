#!/usr/bin/env python3
"""Update SimpleOffice4Me from a GitHub source archive without requiring Git."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "JensKapitza/SimpleOffice4Me"
DEFAULT_REF = "main"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 20000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MANAGED_DIRS = (
    "app", "tools", "templates", "static", "desktop", "android", "deploy", "docs", "tests", "database",
)
PROTECTED_NAMES = {
    ".git", ".venv", "instance", ".simpleoffice-history", ".simpleoffice-control", "node_modules",
}


class HTTPSOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not str(newurl).lower().startswith("https://"):
            raise ValueError("Update-Redirect muss HTTPS verwenden")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(HTTPSOnlyRedirect())


def _download(url: str, target: Path) -> None:
    if not url.lower().startswith("https://"):
        raise ValueError("Update-URL muss HTTPS verwenden")
    request = urllib.request.Request(url, headers={"User-Agent": "SimpleOffice4Me-Updater/1"})
    total = 0
    with _opener().open(request, timeout=90) as response, target.open("wb") as output:
        final_url = str(response.geturl())
        if not final_url.lower().startswith("https://"):
            raise ValueError("Update-Download wurde auf unsicheres Ziel umgeleitet")
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("Update-Archiv ist zu groß")
            output.write(block)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_ref(ref: str) -> tuple[str, int]:
    if not ref or len(ref) > 200 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-" for ch in ref):
        raise ValueError("Ungültiger Update-Ref")
    url = f"https://api.github.com/repos/{REPOSITORY}/commits/{ref}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "SimpleOffice4Me-Updater/1"})
    with _opener().open(request, timeout=30) as response:
        payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
    revision = str(payload.get("sha") or "")
    if len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision.lower()):
        raise ValueError("GitHub lieferte keine gültige Revision")
    date_text = str(((payload.get("commit") or {}).get("committer") or {}).get("date") or "")
    build_epoch = int(time.time())
    if date_text:
        try:
            from datetime import datetime
            build_epoch = int(datetime.fromisoformat(date_text.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return revision, build_epoch


def _validated_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ENTRIES:
        raise ValueError("Update-Archiv hat eine ungültige Dateianzahl")
    total = 0
    roots: set[str] = set()
    for item in entries:
        name = item.filename.replace("\\", "/")
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Update-Archiv enthält unsicheren Pfad")
        roots.add(path.parts[0])
        mode = (item.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise ValueError("Update-Archiv enthält symbolischen Link")
        total += max(0, item.file_size)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Update-Archiv ist entpackt zu groß")
    if len(roots) != 1:
        raise ValueError("Update-Archiv benötigt genau ein Projektwurzelverzeichnis")
    return entries


def _extract(archive_path: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive_path, "r") as archive:
        entries = _validated_entries(archive)
        root_name = PurePosixPath(entries[0].filename.replace("\\", "/")).parts[0]
        for item in entries:
            relative = PurePosixPath(item.filename.replace("\\", "/"))
            target = destination.joinpath(*relative.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open("xb") as sink:
                shutil.copyfileobj(source, sink, 1024 * 1024)
    source_root = destination / root_name
    if not (source_root / "pyproject.toml").is_file() or not (source_root / "app").is_dir():
        raise ValueError("Update-Archiv ist kein gültiges SimpleOffice4Me-Paket")
    return source_root


def _top_level_files(source_root: Path) -> list[Path]:
    result: list[Path] = []
    for item in source_root.iterdir():
        if not item.is_file() or item.name.startswith(".") or item.name in PROTECTED_NAMES:
            continue
        result.append(item)
    return sorted(result, key=lambda path: path.name.casefold())


def _restore(root: Path, backup: Path, replaced: list[str]) -> None:
    for name in reversed(replaced):
        target = root / name
        saved = backup / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        if saved.exists() or saved.is_symlink():
            saved.rename(target)


def _replace(root: Path, source_root: Path, backup: Path) -> None:
    replaced: list[str] = []
    try:
        for name in MANAGED_DIRS:
            source = source_root / name
            if not source.exists():
                continue
            target = root / name
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.rename(saved)
            shutil.copytree(source, target)
            replaced.append(name)
        for source in _top_level_files(source_root):
            name = source.name
            target = root / name
            saved = backup / name
            if target.exists() or target.is_symlink():
                target.rename(saved)
            shutil.copy2(source, target)
            if source.suffix in {".sh", ".command"}:
                target.chmod(target.stat().st_mode | stat.S_IXUSR)
            replaced.append(name)
    except Exception:
        _restore(root, backup, replaced)
        raise


def _project_version(root: Path) -> str:
    in_project = False
    for raw in (root / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project and line.startswith("version") and "=" in line:
            return line.split("=", 1)[1].strip().strip("\"'")
    return "0.0.0"


def apply_archive(root: Path, archive_path: Path, revision: str, branch: str, build_epoch: int) -> dict[str, object]:
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="simpleoffice-update-", dir=str(root.parent)) as temp:
        work = Path(temp)
        source_root = _extract(archive_path, work / "extract")
        backup = work / "backup"
        backup.mkdir()
        _replace(root, source_root, backup)
    release = {
        "schema": 1,
        "version": _project_version(root),
        "revision": revision,
        "branch": branch,
        "build_epoch": int(build_epoch),
        "commit_count": 0,
        "updated_at": int(time.time()),
        "update_mode": "https-source-archive",
    }
    temporary = root / ".simpleoffice-release.json.tmp"
    temporary.write_text(json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(root / ".simpleoffice-release.json")
    return release


def main() -> int:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me ohne Git aktualisieren")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--ref", default=os.environ.get("SIMPLEOFFICE_UPDATE_REF", DEFAULT_REF))
    parser.add_argument("--archive", help="lokales ZIP statt Download verwenden")
    parser.add_argument("--sha256", help="erwartete SHA-256-Summe für lokales ZIP")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()

    with tempfile.TemporaryDirectory(prefix="simpleoffice-download-") as temp:
        archive_path = Path(args.archive).expanduser().resolve() if args.archive else Path(temp) / "update.zip"
        if args.archive:
            if not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
                raise SystemExit("Lokales Update-Archiv ist ungültig")
            revision = args.ref if len(args.ref) == 40 else "local-archive"
            build_epoch = int(time.time())
        else:
            revision, build_epoch = _resolve_ref(args.ref)
            _download(f"https://github.com/{REPOSITORY}/archive/{revision}.zip", archive_path)
        if args.sha256 and _sha256(archive_path).lower() != args.sha256.lower().strip():
            raise SystemExit("SHA-256-Prüfung des Update-Archivs fehlgeschlagen")
        release = apply_archive(root, archive_path, revision, args.ref, build_epoch)
    print(f"SimpleOffice4Me aktualisiert: {release['version']} ({release['revision']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
