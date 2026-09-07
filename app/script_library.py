"""Versioned, non-executing host script library with safe import/export bundles."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path, PurePosixPath

from .document_store import CONTROL_DIR

SCRIPT_ID_RE = re.compile(r"^[a-f0-9]{16,64}$")
SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,119}$")
LANGUAGES = {"sh": ".sh", "python": ".py", "powershell": ".ps1"}
MAX_SCRIPT_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_FILES = 1000


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_content(content: str) -> bytes:
    if "\x00" in content:
        raise ValueError("Scripts dürfen keine NUL-Zeichen enthalten")
    raw = content.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    if len(raw) > MAX_SCRIPT_BYTES:
        raise ValueError("Script ist größer als 1 MiB")
    return raw


def _safe_zip_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Unsicherer Pfad im Script-Bundle")
    return path


class ScriptLibrary:
    """Store immutable revisions; import never executes imported code."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.directory = self.control / "host-scripts"
        self.index = self.control / "host-scripts.json"

    def all(self) -> list[dict[str, object]]:
        try:
            payload = json.loads(self.index.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = payload.get("scripts") if isinstance(payload, dict) else None
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def get(self, script_id: str) -> dict[str, object] | None:
        if not SCRIPT_ID_RE.fullmatch(script_id):
            return None
        return next((row for row in self.all() if row.get("id") == script_id), None)

    def save(
        self,
        *,
        script_id: str,
        name: str,
        language: str,
        content: str,
        description: str = "",
        actor: str = "",
    ) -> dict[str, object]:
        if not SCRIPT_ID_RE.fullmatch(script_id):
            raise ValueError("Ungültige Script-ID")
        name = " ".join(name.split()).strip()
        if not SCRIPT_NAME_RE.fullmatch(name):
            raise ValueError("Ungültiger Scriptname")
        if language not in LANGUAGES:
            raise ValueError("Nicht unterstützte Scriptsprache")
        raw = _normalize_content(content)
        current = self.get(script_id)
        revision = int(current.get("revision", 0)) + 1 if current else 1
        digest = _sha256(raw)
        revision_path = self._revision_path(script_id, revision, language)
        revision_path.parent.mkdir(parents=True, exist_ok=True)
        if revision_path.exists():
            raise RuntimeError("Script-Revision existiert bereits")
        tmp = revision_path.with_suffix(revision_path.suffix + ".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, revision_path)
        if os.name == "posix":
            os.chmod(revision_path, 0o600)
        row = {
            "id": script_id,
            "name": name,
            "language": language,
            "description": " ".join(description.split()).strip()[:1000],
            "revision": revision,
            "sha256": digest,
            "size": len(raw),
            "updated_at": _now(),
            "updated_by": " ".join(actor.split()).strip()[:160],
        }
        rows = [item for item in self.all() if item.get("id") != script_id]
        rows.append(row)
        self._write_index(rows)
        return row

    def read(self, script_id: str, revision: int | None = None) -> str:
        row = self.get(script_id)
        if row is None:
            raise KeyError(script_id)
        selected = int(revision or row["revision"])
        if selected < 1 or selected > int(row["revision"]):
            raise ValueError("Ungültige Script-Revision")
        path = self._revision_path(script_id, selected, str(row["language"]))
        data = path.read_bytes()
        if selected == int(row["revision"]) and _sha256(data) != row["sha256"]:
            raise RuntimeError("Script-Hash stimmt nicht mit dem Index überein")
        return data.decode("utf-8")

    def history(self, script_id: str) -> list[dict[str, object]]:
        row = self.get(script_id)
        if row is None:
            return []
        result = []
        for revision in range(1, int(row["revision"]) + 1):
            path = self._revision_path(script_id, revision, str(row["language"]))
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            result.append({"revision": revision, "sha256": _sha256(raw), "size": len(raw)})
        return result

    def export_bundle(self, script_ids: list[str] | None = None) -> bytes:
        selected = self.all()
        if script_ids is not None:
            wanted = set(script_ids)
            if any(not SCRIPT_ID_RE.fullmatch(item) for item in wanted):
                raise ValueError("Ungültige Script-ID")
            selected = [row for row in selected if str(row.get("id")) in wanted]
        manifest_scripts = []
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for row in sorted(selected, key=lambda item: str(item.get("id"))):
                script_id = str(row["id"])
                language = str(row["language"])
                revision = int(row["revision"])
                versions = []
                for number in range(1, revision + 1):
                    path = self._revision_path(script_id, number, language)
                    raw = path.read_bytes()
                    bundle_path = f"scripts/{script_id}/{number}{LANGUAGES[language]}"
                    archive.writestr(bundle_path, raw)
                    versions.append({"revision": number, "path": bundle_path, "sha256": _sha256(raw), "size": len(raw)})
                manifest_scripts.append({**row, "versions": versions})
            manifest = {"format": "simpleoffice-script-library", "version": 1, "generated_at": _now(), "scripts": manifest_scripts}
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
        data = buffer.getvalue()
        if len(data) > MAX_BUNDLE_BYTES:
            raise ValueError("Script-Bundle ist größer als 16 MiB")
        return data

    def import_bundle(self, data: bytes, *, actor: str = "", conflict: str = "newer") -> dict[str, object]:
        if conflict not in {"newer", "replace", "skip"}:
            raise ValueError("Unbekannte Konfliktstrategie")
        if not data or len(data) > MAX_BUNDLE_BYTES:
            raise ValueError("Ungültige Bundle-Größe")
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_BUNDLE_FILES:
                raise ValueError("Zu viele Dateien im Script-Bundle")
            for info in infos:
                _safe_zip_name(info.filename)
                if info.file_size > MAX_SCRIPT_BYTES and info.filename != "manifest.json":
                    raise ValueError("Script-Datei im Bundle ist zu groß")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError("Symlinks sind in Script-Bundles nicht erlaubt")
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("Ungültiges Script-Bundle-Manifest") from exc
            if not isinstance(manifest, dict) or manifest.get("format") != "simpleoffice-script-library" or manifest.get("version") != 1:
                raise ValueError("Nicht unterstütztes Script-Bundle")
            scripts = manifest.get("scripts")
            if not isinstance(scripts, list):
                raise ValueError("Script-Liste fehlt")
            imported = 0
            skipped = 0
            for item in scripts:
                if not isinstance(item, dict):
                    raise ValueError("Ungültiger Script-Eintrag")
                script_id = str(item.get("id", ""))
                name = " ".join(str(item.get("name", "")).split()).strip()
                language = str(item.get("language", ""))
                incoming_revision = int(item.get("revision", 0))
                if not SCRIPT_ID_RE.fullmatch(script_id) or not SCRIPT_NAME_RE.fullmatch(name) or language not in LANGUAGES or incoming_revision < 1:
                    raise ValueError("Ungültige Script-Metadaten")
                current = self.get(script_id)
                if current and conflict == "skip":
                    skipped += 1
                    continue
                if current and conflict == "newer" and incoming_revision <= int(current.get("revision", 0)):
                    skipped += 1
                    continue
                versions = item.get("versions")
                if not isinstance(versions, list) or not versions:
                    raise ValueError("Script-Versionen fehlen")
                staged: list[tuple[int, bytes]] = []
                for version in versions:
                    if not isinstance(version, dict):
                        raise ValueError("Ungültiger Versions-Eintrag")
                    revision = int(version.get("revision", 0))
                    path = str(version.get("path", ""))
                    if revision < 1 or revision > incoming_revision:
                        raise ValueError("Ungültige Versionsnummer")
                    _safe_zip_name(path)
                    expected = f"scripts/{script_id}/{revision}{LANGUAGES[language]}"
                    if path != expected:
                        raise ValueError("Script-Pfad passt nicht zum Manifest")
                    raw = archive.read(path)
                    if len(raw) > MAX_SCRIPT_BYTES or _sha256(raw) != str(version.get("sha256", "")):
                        raise ValueError("Script-Datei ist beschädigt")
                    raw.decode("utf-8")
                    staged.append((revision, raw))
                if not any(revision == incoming_revision for revision, _raw in staged):
                    raise ValueError("Aktuelle Script-Revision fehlt")
                directory = self.directory / script_id
                directory.mkdir(parents=True, exist_ok=True)
                for revision, raw in staged:
                    path = self._revision_path(script_id, revision, language)
                    if path.exists() and _sha256(path.read_bytes()) == _sha256(raw):
                        continue
                    tmp = path.with_suffix(path.suffix + ".tmp")
                    tmp.write_bytes(raw)
                    os.replace(tmp, path)
                    if os.name == "posix":
                        os.chmod(path, 0o600)
                latest = next(raw for revision, raw in staged if revision == incoming_revision)
                row = {
                    "id": script_id,
                    "name": name,
                    "language": language,
                    "description": " ".join(str(item.get("description", "")).split()).strip()[:1000],
                    "revision": incoming_revision,
                    "sha256": _sha256(latest),
                    "size": len(latest),
                    "updated_at": _now(),
                    "updated_by": " ".join(actor.split()).strip()[:160],
                }
                rows = [entry for entry in self.all() if entry.get("id") != script_id]
                rows.append(row)
                self._write_index(rows)
                imported += 1
        return {"imported": imported, "skipped": skipped, "total": imported + skipped}

    def _revision_path(self, script_id: str, revision: int, language: str) -> Path:
        if not SCRIPT_ID_RE.fullmatch(script_id) or language not in LANGUAGES or revision < 1:
            raise ValueError("Ungültige Script-Revision")
        return self.directory / script_id / f"{revision}{LANGUAGES[language]}"

    def _write_index(self, rows: list[dict[str, object]]) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "scripts": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.index)
        if os.name == "posix":
            os.chmod(self.index, 0o600)
