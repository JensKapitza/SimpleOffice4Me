"""Persistent installer cache and authenticated federation transport.

Installable binaries are kept outside runtime/customer data and are treated as
opaque, hash-addressed files.  The cache survives loss of Internet access and
can be filled from GitHub Actions or another trusted federation peer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .document_store import CONTROL_DIR, atomic_json_write
from .federation_core import build_manifest, manifest_valid, normalize_sha256, preallocate, verify_chunk, verify_file, write_chunk
from .federation_store import FederationStore
from .federation_worker import _json_request, _request
from .file_lock import exclusive_file_lock

ARTIFACT_SCHEMA = 1
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024
MAX_ACTION_ARCHIVE_BYTES = 5 * 1024 * 1024 * 1024
MAX_ARTIFACTS = 200
MAX_OFFERS = 500

_ALLOWED_SUFFIXES = {
    ".exe": ("windows", "exe"),
    ".msi": ("windows", "msi"),
    ".dmg": ("macos", "dmg"),
    ".pkg": ("macos", "pkg"),
    ".appimage": ("linux", "appimage"),
    ".deb": ("linux", "deb"),
    ".rpm": ("linux", "rpm"),
    ".apk": ("android", "apk"),
    ".zip": ("archive", "zip"),
    ".tar.gz": ("docker", "docker-image"),
}
_ALLOWED_PLATFORMS = {"windows", "macos", "linux", "android", "docker", "archive"}
_GITHUB_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _suffix(name: str) -> str:
    lowered = name.casefold()
    if lowered.endswith(".tar.gz"):
        return ".tar.gz"
    return Path(lowered).suffix


def _classify(name: str) -> tuple[str, str]:
    suffix = _suffix(name)
    try:
        return _ALLOWED_SUFFIXES[suffix]
    except KeyError as exc:
        raise ValueError(f"Nicht unterstütztes Installations-Artefakt: {suffix or 'ohne Dateiendung'}") from exc


def _safe_filename(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or raw in {".", ".."} or "\x00" in raw or Path(raw).name != raw or "/" in raw or "\\" in raw:
        raise ValueError("Ungültiger Artefakt-Dateiname")
    cleaned = re.sub(r"[^A-Za-z0-9._()+ -]", "_", raw).strip(" .")
    if not cleaned:
        raise ValueError("Ungültiger Artefakt-Dateiname")
    if len(cleaned) > 180:
        suffix = "".join(Path(cleaned).suffixes[-2:]) if cleaned.casefold().endswith(".tar.gz") else Path(cleaned).suffix
        stem = cleaned[: max(1, 180 - len(suffix))].rstrip(" .")
        cleaned = stem + suffix
    _classify(cleaned)
    return cleaned


def _normalize_platform(value: Any, fallback: str) -> str:
    candidate = str(value or "").strip().casefold()
    return candidate if candidate in _ALLOWED_PLATFORMS else fallback


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "sha256": str(entry.get("sha256") or ""),
        "name": str(entry.get("name") or ""),
        "size": int(entry.get("size") or 0),
        "platform": str(entry.get("platform") or "archive"),
        "kind": str(entry.get("kind") or "file"),
        "revision": str(entry.get("revision") or ""),
        "source": str(entry.get("source") or ""),
        "cached_at": int(entry.get("cached_at") or 0),
    }


def _github_channel(name: str) -> str | None:
    lowered = name.casefold()
    exact = {
        "simpleoffice4me-desktop-windows": "desktop-windows",
        "simpleoffice4me-desktop-macos": "desktop-macos",
        "simpleoffice4me-desktop-linux": "desktop-linux",
        "simpleoffice4me-android-installable": "android",
    }
    if lowered in exact:
        return exact[lowered]
    if lowered.startswith("simpleoffice4me-docker-"):
        if lowered.endswith("-linux-amd64"):
            return "docker-linux-amd64"
        if lowered.endswith("-linux-arm64"):
            return "docker-linux-arm64"
    return None


def _github_platform(channel: str) -> str:
    if channel.endswith("windows"):
        return "windows"
    if channel.endswith("macos"):
        return "macos"
    if channel == "android":
        return "android"
    if channel.startswith("docker-"):
        return "docker"
    return "linux"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _validate_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_https_url(url: str) -> None:
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Unsichere Download-URL")


def _open_https(request: urllib.request.Request, *, timeout: int, allow_redirects: bool = True):
    _validate_https_url(request.full_url)
    handler = _HttpsOnlyRedirect() if allow_redirects else _NoRedirect()
    opener = urllib.request.build_opener(handler)
    response = opener.open(request, timeout=timeout)
    try:
        _validate_https_url(response.geturl())
    except Exception:
        response.close()
        raise
    return response


class SoftwareArtifactStore:
    def __init__(self, document_root: str | Path):
        self.document_root = Path(document_root).expanduser().resolve()
        self.base = self.document_root / CONTROL_DIR / "software-distribution"
        self.cache = self.base / "installers"
        self.incoming = self.base / "installer-incoming"
        self.state_path = self.base / "installers.json"
        self.lock_path = self.base / ".installers-write.lock"
        self.initialize()

    def initialize(self) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        self.incoming.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            with exclusive_file_lock(self.lock_path):
                if not self.state_path.exists():
                    atomic_json_write(self.state_path, {"schema": ARTIFACT_SCHEMA, "artifacts": [], "offers": []})

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass
        return {"schema": ARTIFACT_SCHEMA, "artifacts": [], "offers": []}

    def _save(self, state: dict[str, Any]) -> None:
        state["schema"] = ARTIFACT_SCHEMA
        with exclusive_file_lock(self.lock_path):
            atomic_json_write(self.state_path, state)

    def _entry(self, digest: str) -> dict[str, Any] | None:
        normalized = normalize_sha256(digest)
        for entry in self._read().get("artifacts", []):
            if entry.get("sha256") == normalized:
                return dict(entry)
        return None

    def artifact_path(self, digest: str, *, verify: bool = True) -> Path:
        normalized = normalize_sha256(digest)
        entry = self._entry(normalized)
        if not entry:
            raise ValueError("Installations-Artefakt nicht im Offline-Cache")
        name = _safe_filename(str(entry.get("name") or ""))
        path = (self.cache / normalized / name).resolve()
        cache_root = self.cache.resolve()
        if cache_root not in path.parents or not path.is_file() or path.is_symlink():
            raise ValueError("Installations-Artefakt nicht im Offline-Cache")
        if path.stat().st_size != int(entry.get("size") or 0):
            raise ValueError("Installations-Artefakt hat eine unerwartete Größe")
        if verify and _sha256(path) != normalized:
            raise ValueError("SHA-256 des Installations-Artefakts stimmt nicht")
        return path

    def catalog(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for raw in self._read().get("artifacts", []):
            try:
                digest = normalize_sha256(raw.get("sha256", ""))
                name = _safe_filename(str(raw.get("name") or ""))
                path = (self.cache / digest / name).resolve()
                if self.cache.resolve() not in path.parents or not path.is_file() or path.is_symlink():
                    continue
                if path.stat().st_size != int(raw.get("size") or 0):
                    continue
                result.append(_public_entry(raw))
            except (OSError, TypeError, ValueError):
                continue
        return result

    def offers(self) -> list[dict[str, Any]]:
        return list(self._read().get("offers", []))

    def _adopt_verified(self, temp_path: Path, *, name: str, digest: str, size: int, source: str, platform: str | None = None, revision: str = "") -> dict[str, Any]:
        safe_name = _safe_filename(name)
        normalized = normalize_sha256(digest)
        fallback_platform, kind = _classify(safe_name)
        platform_name = _normalize_platform(platform, fallback_platform)
        if size <= 0 or size > MAX_ARTIFACT_BYTES or temp_path.stat().st_size != size:
            raise ValueError("Ungültige Größe des Installations-Artefakts")

        existing = self._entry(normalized)
        if existing:
            try:
                self.artifact_path(normalized, verify=False)
                if temp_path.exists():
                    temp_path.unlink()
                return _public_entry(existing)
            except (OSError, ValueError):
                pass

        destination_dir = self.cache / normalized
        if destination_dir.exists():
            shutil.rmtree(destination_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / safe_name
        temp_path.replace(destination)
        entry = {
            "sha256": normalized,
            "name": safe_name,
            "size": size,
            "platform": platform_name,
            "kind": kind,
            "revision": str(revision or "")[:64],
            "source": str(source or "local")[:200],
            "cached_at": int(time.time()),
        }
        state = self._read()
        state["artifacts"] = [entry] + [item for item in state.get("artifacts", []) if item.get("sha256") != normalized]
        state["artifacts"] = state["artifacts"][:MAX_ARTIFACTS]
        self._save(state)
        return _public_entry(entry)

    def cache_stream(
        self,
        stream: BinaryIO,
        name: str,
        *,
        source: str = "local",
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        platform: str | None = None,
        revision: str = "",
    ) -> dict[str, Any]:
        safe_name = _safe_filename(name)
        expected_digest = normalize_sha256(expected_sha256) if expected_sha256 else None
        if expected_size is not None and (expected_size <= 0 or expected_size > MAX_ARTIFACT_BYTES):
            raise ValueError("Ungültige Größe des Installations-Artefakts")

        handle = tempfile.NamedTemporaryFile(prefix="installer-", suffix=".part", dir=self.incoming, delete=False)
        temp_path = Path(handle.name)
        digest = hashlib.sha256()
        size = 0
        try:
            with handle:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_ARTIFACT_BYTES:
                        raise ValueError("Installations-Artefakt überschreitet die maximale Größe")
                    handle.write(block)
                    digest.update(block)
                handle.flush()
                os.fsync(handle.fileno())
            actual = digest.hexdigest()
            if expected_size is not None and size != expected_size:
                raise ValueError("Installations-Artefakt hat eine unerwartete Größe")
            if expected_digest and actual != expected_digest:
                raise ValueError("SHA-256 des Installations-Artefakts stimmt nicht")
            return self._adopt_verified(
                temp_path,
                name=safe_name,
                digest=actual,
                size=size,
                source=source,
                platform=platform,
                revision=revision,
            )
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def cache_file(self, path: str | Path, *, name: str | None = None, source: str = "local", platform: str | None = None, revision: str = "") -> dict[str, Any]:
        source_path = Path(path).resolve()
        if not source_path.is_file() or source_path.is_symlink():
            raise ValueError("Installations-Artefakt ist keine reguläre Datei")
        if source_path.stat().st_size <= 0 or source_path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("Ungültige Größe des Installations-Artefakts")
        with source_path.open("rb") as handle:
            return self.cache_stream(
                handle,
                name or source_path.name,
                source=source,
                expected_size=source_path.stat().st_size,
                platform=platform,
                revision=revision,
            )

    def delete(self, digest: str) -> dict[str, Any]:
        normalized = normalize_sha256(digest)
        entry = self._entry(normalized)
        if not entry:
            raise ValueError("Installations-Artefakt nicht im Offline-Cache")
        shutil.rmtree(self.cache / normalized, ignore_errors=True)
        state = self._read()
        state["artifacts"] = [item for item in state.get("artifacts", []) if item.get("sha256") != normalized]
        self._save(state)
        return _public_entry(entry)

    def record_offer(self, peer_id: str, artifacts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if artifacts is None:
            return []
        if not isinstance(artifacts, list) or len(artifacts) > MAX_ARTIFACTS:
            raise ValueError("Ungültiger Installations-Artefakt-Katalog")
        accepted: list[dict[str, Any]] = []
        now = int(time.time())
        for raw in artifacts:
            if not isinstance(raw, dict):
                raise ValueError("Ungültiger Installations-Artefakt-Katalog")
            digest = normalize_sha256(raw.get("sha256", ""))
            name = _safe_filename(str(raw.get("name") or ""))
            size = int(raw.get("size") or 0)
            if size <= 0 or size > MAX_ARTIFACT_BYTES:
                raise ValueError("Ungültige Größe im Installations-Artefakt-Katalog")
            fallback_platform, kind = _classify(name)
            entry = {
                "peer_id": str(peer_id)[:128],
                "sha256": digest,
                "name": name,
                "size": size,
                "platform": _normalize_platform(raw.get("platform"), fallback_platform),
                "kind": str(raw.get("kind") or kind)[:40],
                "revision": str(raw.get("revision") or "")[:64],
                "offered_at": now,
            }
            accepted.append(entry)
        if not accepted:
            return []
        state = self._read()
        keys = {(item["peer_id"], item["sha256"]) for item in accepted}
        state["offers"] = accepted + [
            item for item in state.get("offers", [])
            if (str(item.get("peer_id") or ""), str(item.get("sha256") or "")) not in keys
        ]
        state["offers"] = state["offers"][:MAX_OFFERS]
        self._save(state)
        return accepted

    def cache_from_peer(self, peer_id: str, digest: str) -> dict[str, Any]:
        normalized = normalize_sha256(digest)
        federation = FederationStore(self.document_root)
        peer = federation.get_peer(peer_id)
        if not peer or not peer.get("enabled"):
            raise ValueError("Federation-Peer ist nicht aktiv")
        policy = peer.get("policy") or {}
        software_policy = policy.get("software", {}) if isinstance(policy, dict) else {}
        if software_policy.get("receive") is not True:
            raise ValueError("Peer-Policy muss software.receive=true explizit erlauben")

        metadata = next(
            (item for item in self.offers() if item.get("peer_id") == peer_id and item.get("sha256") == normalized),
            None,
        )
        token = federation.peer_token(peer_id)
        if metadata is None:
            remote = _json_request(peer["base_url"] + "/federation/v1/software/artifacts", token=token, timeout=60)
            self.record_offer(peer_id, remote.get("artifacts") or [])
            metadata = next(
                (item for item in self.offers() if item.get("peer_id") == peer_id and item.get("sha256") == normalized),
                None,
            )
        if metadata is None:
            raise ValueError("Peer bietet dieses Installations-Artefakt nicht an")

        remote_manifest = _json_request(
            f"{peer['base_url']}/federation/v1/software/artifacts/{normalized}/manifest",
            token=token,
            timeout=60,
        )
        if not manifest_valid(remote_manifest):
            raise ValueError("Peer lieferte kein gültiges Artefakt-Manifest")
        if normalize_sha256(remote_manifest.get("blob_hash", "")) != normalized:
            raise ValueError("Artefakt-Manifest passt nicht zum angebotenen SHA-256")
        size = int(remote_manifest.get("size") or 0)
        if size != int(metadata.get("size") or 0) or size <= 0 or size > MAX_ARTIFACT_BYTES:
            raise ValueError("Artefakt-Manifest meldet eine unerwartete Größe")

        partial = self.incoming / f"{normalized}.part"
        preallocate(partial, size)
        for chunk in remote_manifest.get("chunks") or []:
            index = int(chunk["index"])
            offset = int(chunk["offset"])
            length = int(chunk["length"])
            with partial.open("rb") as handle:
                handle.seek(offset)
                existing = handle.read(length)
            if len(existing) == length and verify_chunk(existing, chunk["hash"]):
                continue
            url = f"{peer['base_url']}/federation/v1/software/artifacts/{normalized}/chunks/{index}"
            with _request(url, token=token, timeout=120) as response:
                data = response.read()
            if len(data) != length or not verify_chunk(data, chunk["hash"]):
                raise ValueError(f"Installations-Artefakt-Chunk {index} ist beschädigt")
            write_chunk(partial, offset, data)
        if not verify_file(partial, normalized):
            raise ValueError("Gesamthash des Installations-Artefakts stimmt nicht")
        return self._adopt_verified(
            partial,
            name=str(metadata["name"]),
            digest=normalized,
            size=size,
            source=f"peer:{peer_id}",
            platform=str(metadata.get("platform") or ""),
            revision=str(metadata.get("revision") or ""),
        )

    def github_sync_info(self) -> dict[str, Any]:
        return {
            "configured": bool(self._github_token()),
            "repository": self._github_repository(),
        }

    def _github_repository(self) -> str:
        repository = os.environ.get("SIMPLEOFFICE_GITHUB_ARTIFACT_REPOSITORY", "JensKapitza/SimpleOffice4Me").strip()
        if not _GITHUB_REPOSITORY_RE.fullmatch(repository):
            raise ValueError("SIMPLEOFFICE_GITHUB_ARTIFACT_REPOSITORY ist ungültig")
        return repository

    def _github_token(self) -> str:
        token_file = os.environ.get("SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN_FILE", "").strip()
        if token_file:
            path = Path(token_file).expanduser()
            try:
                if path.is_file() and not path.is_symlink():
                    token = path.read_text(encoding="utf-8").strip()
                    if token:
                        return token
            except OSError:
                pass
        return os.environ.get("SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN", "").strip()

    @staticmethod
    def _github_request(url: str, token: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "SimpleOffice4Me-installer-cache/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _github_json(self, url: str, token: str) -> dict[str, Any]:
        try:
            with _open_https(self._github_request(url, token), timeout=60) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", "replace").strip()
            raise ValueError(f"GitHub API HTTP {exc.code}: {detail[:500]}") from exc
        except (OSError, ValueError) as exc:
            raise ValueError(f"GitHub API nicht erreichbar: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("GitHub API lieferte eine unerwartete Antwort")
        return payload

    def _download_action_archive(self, url: str, token: str, destination: Path) -> None:
        request = self._github_request(url, token)
        response = None
        try:
            try:
                response = _open_https(request, timeout=120, allow_redirects=False)
            except urllib.error.HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    detail = exc.read(2048).decode("utf-8", "replace").strip()
                    raise ValueError(f"GitHub Artefakt-Download HTTP {exc.code}: {detail[:500]}") from exc
                location = urllib.parse.urljoin(url, str(exc.headers.get("Location") or ""))
                _validate_https_url(location)
                response = _open_https(
                    urllib.request.Request(location, headers={"User-Agent": "SimpleOffice4Me-installer-cache/1"}),
                    timeout=120,
                )
            assert response is not None
            with response:
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > MAX_ACTION_ARCHIVE_BYTES:
                    raise ValueError("GitHub Actions Artefakt ist zu groß")
                total = 0
                with destination.open("wb") as handle:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > MAX_ACTION_ARCHIVE_BYTES:
                            raise ValueError("GitHub Actions Artefakt ist zu groß")
                        handle.write(block)
                    handle.flush()
                    os.fsync(handle.fileno())
        except urllib.error.URLError as exc:
            raise ValueError(f"GitHub Artefakt-Download fehlgeschlagen: {exc}") from exc

    def import_actions_archive(self, path: str | Path, *, source: str, platform: str | None = None, revision: str = "") -> list[dict[str, Any]]:
        archive_path = Path(path).resolve()
        if not archive_path.is_file() or archive_path.is_symlink() or archive_path.stat().st_size > MAX_ACTION_ARCHIVE_BYTES:
            raise ValueError("Ungültiges GitHub Actions Artefakt")
        imported: list[dict[str, Any]] = []
        with zipfile.ZipFile(archive_path, "r") as archive:
            seen_names: set[str] = set()
            for item in archive.infolist():
                if item.is_dir():
                    continue
                normalized = PurePosixPath(item.filename.replace("\\", "/"))
                if normalized.is_absolute() or ".." in normalized.parts or len(normalized.parts) != 1:
                    continue
                try:
                    name = _safe_filename(normalized.name)
                except ValueError:
                    continue
                if name in seen_names:
                    raise ValueError("GitHub Actions Artefakt enthält doppelte Installer-Dateien")
                seen_names.add(name)
                if item.file_size <= 0 or item.file_size > MAX_ARTIFACT_BYTES:
                    raise ValueError("Installer im GitHub Actions Artefakt ist zu groß")
                with archive.open(item, "r") as stream:
                    imported.append(
                        self.cache_stream(
                            stream,
                            name,
                            source=source,
                            expected_size=item.file_size,
                            platform=platform,
                            revision=revision,
                        )
                    )
        return imported

    def sync_from_github(self) -> dict[str, Any]:
        token = self._github_token()
        if not token:
            raise ValueError(
                "GitHub-Artefakt-Sync benötigt SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN_FILE oder SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN"
            )
        repository = self._github_repository()
        payload = self._github_json(f"https://api.github.com/repos/{repository}/actions/artifacts?per_page=100", token)
        artifacts = payload.get("artifacts") or []
        if not isinstance(artifacts, list):
            raise ValueError("GitHub API lieferte keinen Artefakt-Katalog")

        try:
            from .software_distribution import local_release_info
            local_revision = str(local_release_info().get("revision") or "")
        except Exception:
            local_revision = ""

        selected: dict[str, dict[str, Any]] = {}
        for raw in artifacts:
            if not isinstance(raw, dict) or raw.get("expired"):
                continue
            channel = _github_channel(str(raw.get("name") or ""))
            if not channel:
                continue
            run = raw.get("workflow_run") or {}
            head_sha = str(run.get("head_sha") or "")
            head_branch = str(run.get("head_branch") or "")
            score = (
                1 if local_revision and head_sha == local_revision else 0,
                1 if head_branch == "main" else 0,
                str(raw.get("created_at") or ""),
            )
            previous = selected.get(channel)
            if previous is None or score > previous["_score"]:
                candidate = dict(raw)
                candidate["_score"] = score
                selected[channel] = candidate

        if not selected:
            raise ValueError("Keine passenden, nicht abgelaufenen SimpleOffice4Me GitHub-Actions-Artefakte gefunden")

        imported: list[dict[str, Any]] = []
        downloaded = 0
        for channel, artifact in sorted(selected.items()):
            archive_url = str(artifact.get("archive_download_url") or "")
            if not archive_url.startswith("https://api.github.com/"):
                raise ValueError("GitHub lieferte eine unerwartete Artefakt-Download-URL")
            run = artifact.get("workflow_run") or {}
            revision = str(run.get("head_sha") or "")
            temp = self.incoming / f"github-{int(artifact.get('id') or 0)}.zip.part"
            try:
                self._download_action_archive(archive_url, token, temp)
                files = self.import_actions_archive(
                    temp,
                    source=f"github:{artifact.get('name')}",
                    platform=_github_platform(channel),
                    revision=revision,
                )
                imported.extend(files)
                downloaded += 1
            finally:
                temp.unlink(missing_ok=True)

        unique = {item["sha256"]: item for item in imported}
        return {
            "repository": repository,
            "downloaded_archives": downloaded,
            "artifacts": list(unique.values()),
            "local_revision": local_revision,
        }
