"""Ciphertext-only transport helpers for vault packages over federation.

This module never decrypts a vault package. It splits the already encrypted SOVP
file into independently hashed transport chunks, assigns those chunks to peers,
and reconstructs the exact ciphertext file after collection. Peers therefore do
not receive the package password/recovery key or plaintext file metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .vault_packages import federation_chunk_plan

FORMAT = "simpleoffice-vault-package-federation"
VERSION = 1
DEFAULT_TRANSPORT_CHUNK = 4 * 1024 * 1024
MIN_TRANSPORT_CHUNK = 64 * 1024
MAX_TRANSPORT_CHUNK = 64 * 1024 * 1024
MAX_CHUNKS = 1_000_000


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_encrypted_package(
    package: str | Path,
    output_dir: str | Path,
    *,
    peers: list[str],
    replicas: int = 2,
    chunk_size: int = DEFAULT_TRANSPORT_CHUNK,
) -> dict[str, Any]:
    """Split an encrypted .sovp file into transport chunks plus a public manifest."""
    package_path = Path(package).expanduser().resolve()
    if not package_path.is_file() or package_path.is_symlink():
        raise ValueError("Verschlüsseltes Vault-Paket fehlt oder ist kein reguläres File")
    chunk_size = int(chunk_size)
    if not MIN_TRANSPORT_CHUNK <= chunk_size <= MAX_TRANSPORT_CHUNK:
        raise ValueError("Ungültige Federation-Chunk-Größe")
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("Federation-Chunk-Ziel darf kein Symlink sein")

    package_hash = _sha256_file(package_path)
    package_size = package_path.stat().st_size
    chunks: list[dict[str, Any]] = []
    with package_path.open("rb") as source:
        index = 0
        while True:
            block = source.read(chunk_size)
            if not block:
                break
            if index >= MAX_CHUNKS:
                raise ValueError("Vault-Paket erzeugt zu viele Federation-Chunks")
            digest = hashlib.sha256(block).hexdigest()
            name = f"{index:08d}-{digest[:16]}.sovpc"
            target = output / name
            tmp = output / (name + ".tmp")
            try:
                with tmp.open("xb") as handle:
                    handle.write(block)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, target)
                if os.name == "posix":
                    os.chmod(target, 0o600)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            chunks.append({"index": index, "name": name, "size": len(block), "sha256": digest})
            index += 1

    if not chunks:
        raise ValueError("Vault-Paket ist leer")
    assignments = federation_chunk_plan(len(chunks), peers, replicas=replicas)
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "package_sha256": package_hash,
        "package_size": package_size,
        "transport_chunk_size": chunk_size,
        "chunk_count": len(chunks),
        "replicas": int(replicas),
        "chunks": chunks,
        "assignments": assignments,
    }
    manifest_path = output / "manifest.json"
    tmp_manifest = output / "manifest.json.tmp"
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        with tmp_manifest.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_manifest, manifest_path)
        if os.name == "posix":
            os.chmod(manifest_path, 0o600)
    finally:
        try:
            tmp_manifest.unlink(missing_ok=True)
        except OSError:
            pass
    return manifest


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Ungültiges Vault-Federation-Manifest") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != VERSION:
        raise ValueError("Nicht unterstütztes Vault-Federation-Manifest")
    chunks = data.get("chunks")
    if not isinstance(chunks, list) or not chunks or len(chunks) > MAX_CHUNKS:
        raise ValueError("Ungültige Chunk-Liste")
    if int(data.get("chunk_count", -1)) != len(chunks):
        raise ValueError("Chunk-Anzahl stimmt nicht")
    for expected, item in enumerate(chunks):
        if not isinstance(item, dict) or int(item.get("index", -1)) != expected:
            raise ValueError("Ungültige Chunk-Reihenfolge")
        name = str(item.get("name") or "")
        digest = str(item.get("sha256") or "")
        if "/" in name or "\\" in name or len(digest) != 64:
            raise ValueError("Unsicherer Chunk-Eintrag")
    return data


def verify_chunk(chunk_path: str | Path, expected: dict[str, Any]) -> bool:
    path = Path(chunk_path).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        return False
    if path.stat().st_size != int(expected.get("size", -1)):
        return False
    return _sha256_file(path) == str(expected.get("sha256") or "")


def missing_chunks(manifest: dict[str, Any], chunk_dirs: list[str | Path]) -> list[int]:
    directories = [Path(value).expanduser().resolve() for value in chunk_dirs]
    missing: list[int] = []
    for item in manifest["chunks"]:
        found = False
        for directory in directories:
            candidate = directory / str(item["name"])
            if verify_chunk(candidate, item):
                found = True
                break
        if not found:
            missing.append(int(item["index"]))
    return missing


def reassemble_encrypted_package(
    manifest: dict[str, Any],
    chunk_dirs: list[str | Path],
    output: str | Path,
) -> dict[str, Any]:
    """Rebuild the exact encrypted package from any verified copy of each chunk."""
    missing = missing_chunks(manifest, chunk_dirs)
    if missing:
        raise ValueError("Federation-Backup ist unvollständig: " + ",".join(map(str, missing[:50])))
    directories = [Path(value).expanduser().resolve() for value in chunk_dirs]
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.is_symlink():
        raise ValueError("Rekonstruktionsziel darf kein Symlink sein")
    tmp = target.with_name(target.name + ".tmp")
    try:
        with tmp.open("wb") as handle:
            for item in manifest["chunks"]:
                source_path = None
                for directory in directories:
                    candidate = directory / str(item["name"])
                    if verify_chunk(candidate, item):
                        source_path = candidate
                        break
                if source_path is None:
                    raise ValueError("Federation-Chunk fehlt während Rekonstruktion")
                with source_path.open("rb") as source:
                    shutil.copyfileobj(source, handle, length=1024 * 1024)
            handle.flush()
            os.fsync(handle.fileno())
        if tmp.stat().st_size != int(manifest.get("package_size", -1)):
            raise ValueError("Rekonstruiertes Paket hat falsche Größe")
        if _sha256_file(tmp) != str(manifest.get("package_sha256") or ""):
            raise ValueError("Rekonstruiertes Paket hat falsche Prüfsumme")
        os.replace(tmp, target)
        if os.name == "posix":
            os.chmod(target, 0o600)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return {"path": str(target), "sha256": str(manifest["package_sha256"]), "chunks": len(manifest["chunks"])}


def peer_chunk_names(manifest: dict[str, Any], peer_id: str) -> list[str]:
    indices = manifest.get("assignments", {}).get(str(peer_id), [])
    chunks = manifest.get("chunks", [])
    return [str(chunks[int(index)]["name"]) for index in indices]
