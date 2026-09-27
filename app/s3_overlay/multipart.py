"""Temporary, bounded S3 multipart staging for Inbox-only imports."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import BinaryIO

from defusedxml import ElementTree
from flask import current_app

from app.document_store import CONTROL_DIR, atomic_json_write
from app.file_lock import exclusive_file_lock


class MultipartError(ValueError):
    pass


class MultipartStore:
    MAX_PARTS = 10_000
    MIN_PART_BYTES = 5 * 1024 * 1024
    UPLOAD_TTL_SECONDS = 24 * 60 * 60

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve() / CONTROL_DIR / "s3-multipart"
        self.uploads = self.root / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            self.root.chmod(0o700)
            self.uploads.chmod(0o700)
        self.max_staging_bytes = max(1024 * 1024, int(current_app.config.get(
            "S3_OVERLAY_MAX_STAGING_BYTES", 2 * 1024 * 1024 * 1024)))
        self.cleanup_expired()

    def cleanup_expired(self) -> None:
        cutoff = int(time.time()) - self.UPLOAD_TTL_SECONDS
        with exclusive_file_lock(self.root / ".staging-quota.lock"):
            for folder in self.uploads.iterdir():
                if not folder.is_dir() or folder.is_symlink():
                    continue
                try:
                    with exclusive_file_lock(folder / ".lock"):
                        metadata = self._read(folder)
                        if int(metadata.get("updated_at", 0)) < cutoff:
                            shutil.rmtree(folder)
                except (OSError, ValueError):
                    continue

    @staticmethod
    def _read(folder: Path) -> dict:
        try:
            value = json.loads((folder / "upload.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _folder(self, upload_id: str, *, username: str, access_key: str, key: str) -> tuple[Path, dict]:
        try:
            safe_id = str(uuid.UUID(upload_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise MultipartError("The multipart upload does not exist") from exc
        folder = self.uploads / safe_id
        if folder.is_symlink() or not folder.is_dir():
            raise MultipartError("The multipart upload does not exist")
        metadata = self._read(folder)
        if (not metadata or metadata.get("username") != username or metadata.get("access_key") != access_key
                or metadata.get("key") != key):
            raise MultipartError("The multipart upload does not exist")
        return folder, metadata

    @staticmethod
    def validate_key(key: str) -> None:
        if not key.startswith("inbox/") or len(key) <= len("inbox/") or len(key.encode("utf-8")) > 1024:
            raise MultipartError("Multipart uploads are allowed only for inbox keys")
        parts = key.split("/")
        if any(part in {"", ".", ".."} or any(ord(char) < 32 for char in part) for part in parts):
            raise MultipartError("The inbox key is invalid")

    def initiate(self, key: str, username: str, access_key: str) -> str:
        self.validate_key(key)
        with exclusive_file_lock(self.root / ".staging-quota.lock"):
            if len(self.list_uploads(username, access_key)) >= 20:
                raise MultipartError("At most 20 multipart uploads may be active per credential")
            upload_id = str(uuid.uuid4())
            folder = self.uploads / upload_id
            folder.mkdir(mode=0o700)
            now = int(time.time())
            atomic_json_write(folder / "upload.json", {
                "upload_id": upload_id, "key": key, "username": username,
                "access_key": access_key, "created_at": now, "updated_at": now, "parts": {},
            })
            if os.name == "posix":
                (folder / "upload.json").chmod(0o600)
            return upload_id

    def put_part(self, key: str, upload_id: str, part_number: int, username: str, access_key: str,
                 source: BinaryIO, max_bytes: int, expected_sha256: str) -> tuple[str, int]:
        if isinstance(part_number, bool) or not 1 <= int(part_number) <= self.MAX_PARTS:
            raise MultipartError("partNumber must be between 1 and 10000")
        folder, metadata = self._folder(upload_id, username=username, access_key=access_key, key=key)
        if metadata.get("completed") or metadata.get("completing"):
            raise MultipartError("The multipart upload is already completing or completed")
        staged = folder / f"part-{int(part_number):05d}.tmp"
        final = folder / f"part-{int(part_number):05d}.bin"
        sha256, md5, total = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
        try:
            with exclusive_file_lock(self.root / ".staging-quota.lock"), exclusive_file_lock(folder / ".lock"):
                used_elsewhere = 0
                for candidate in self.uploads.glob("*/part-*.bin"):
                    if candidate.parent != folder:
                        try:
                            used_elsewhere += candidate.stat().st_size
                        except OSError:
                            continue
                existing_part_size = final.stat().st_size if final.exists() else 0
                existing_upload_size = sum(part.stat().st_size for part in folder.glob("part-*.bin")) - existing_part_size
                if used_elsewhere + existing_upload_size >= self.max_staging_bytes:
                    raise MultipartError("Multipart staging capacity is full")
                with staged.open("wb") as output:
                    if os.name == "posix":
                        os.chmod(staged, 0o600)
                    while True:
                        block = source.read(min(1024 * 1024, max_bytes - total + 1))
                        if not block:
                            break
                        total += len(block)
                        if total > max_bytes:
                            raise MultipartError("The part exceeds the configured upload limit")
                        if used_elsewhere + existing_upload_size + total > self.max_staging_bytes:
                            raise MultipartError("Multipart staging capacity is full")
                        sha256.update(block)
                        md5.update(block)
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                if sha256.hexdigest() != expected_sha256.casefold():
                    raise MultipartError("The part payload SHA-256 does not match its signed hash")
                os.replace(staged, final)
                metadata.setdefault("parts", {})[str(part_number)] = {
                    "etag": md5.hexdigest(), "size": total, "sha256": sha256.hexdigest(),
                    "updated_at": int(time.time()),
                }
                metadata["updated_at"] = int(time.time())
                atomic_json_write(folder / "upload.json", metadata)
                if os.name == "posix":
                    (folder / "upload.json").chmod(0o600)
        except MultipartError:
            staged.unlink(missing_ok=True)
            raise
        except OSError as exc:
            staged.unlink(missing_ok=True)
            raise MultipartError("The multipart part could not be staged") from exc
        return md5.hexdigest(), total

    def complete(self, key: str, upload_id: str, username: str, access_key: str, xml_body: bytes,
                 max_bytes: int) -> tuple[BinaryIO, str, int, str]:
        folder, _metadata = self._folder(upload_id, username=username, access_key=access_key, key=key)
        with exclusive_file_lock(folder / ".lock"):
            return self._complete_locked(key, upload_id, username, access_key, xml_body, max_bytes)

    def _complete_locked(self, key: str, upload_id: str, username: str, access_key: str,
                         xml_body: bytes, max_bytes: int) -> tuple[BinaryIO, str, int, str]:
        folder, metadata = self._folder(upload_id, username=username, access_key=access_key, key=key)
        try:
            root = ElementTree.fromstring(xml_body)
            if root.tag.rsplit("}", 1)[-1] != "CompleteMultipartUpload":
                raise MultipartError("The completion manifest root is invalid")
            requested = []
            for part in root.iter():
                if part.tag.rsplit("}", 1)[-1] != "Part":
                    continue
                values = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in part}
                requested.append((int(values["PartNumber"]), values["ETag"].strip('"').casefold()))
        except MultipartError:
            raise
        except (ElementTree.ParseError, KeyError, TypeError, ValueError) as exc:
            raise MultipartError("The completion manifest is invalid") from exc
        parts = metadata.get("parts", {})
        if not requested or len(requested) > self.MAX_PARTS:
            raise MultipartError("The completion manifest must contain uploaded parts")
        numbers = [number for number, _etag in requested]
        if numbers != sorted(set(numbers)):
            raise MultipartError("The completion manifest must be ordered and unique")
        for index, (number, etag) in enumerate(requested):
            stored = parts.get(str(number))
            if not stored or not hmac.compare_digest(str(stored.get("etag", "")), etag):
                raise MultipartError("A completion part does not match the uploaded part")
            if index < len(requested) - 1 and int(stored.get("size", 0)) < self.MIN_PART_BYTES:
                raise MultipartError("Every non-final part must be at least 5 MiB")
        total = sum(int(parts[str(number)]["size"]) for number, _ in requested)
        if total > max_bytes:
            raise MultipartError("The completed upload exceeds the configured limit")
        spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        sha256 = hashlib.sha256()
        try:
            for number, _etag in requested:
                part_path = folder / f"part-{number:05d}.bin"
                with part_path.open("rb") as part:
                    while block := part.read(1024 * 1024):
                        spool.write(block)
                        sha256.update(block)
            spool.seek(0)
            digest = sha256.hexdigest()
            part_md5s = b"".join(bytes.fromhex(parts[str(number)]["etag"]) for number, _etag in requested)
            multipart_etag = f"{hashlib.md5(part_md5s, usedforsecurity=False).hexdigest()}-{len(requested)}"
            metadata["completing"] = True
            metadata["updated_at"] = int(time.time())
            atomic_json_write(folder / "upload.json", metadata)
            if os.name == "posix":
                (folder / "upload.json").chmod(0o600)
            return spool, digest, total, multipart_etag
        except OSError as exc:
            spool.close()
            raise MultipartError("The multipart upload could not be assembled") from exc

    def finish(self, upload_id: str, username: str, access_key: str, key: str) -> None:
        folder, _metadata = self._folder(upload_id, username=username, access_key=access_key, key=key)
        with exclusive_file_lock(folder / ".lock"):
            _folder, metadata = self._folder(upload_id, username=username, access_key=access_key, key=key)
            metadata["completed"] = True
            metadata.pop("completing", None)
            metadata["updated_at"] = int(time.time())
            atomic_json_write(folder / "upload.json", metadata)
            if os.name == "posix":
                (folder / "upload.json").chmod(0o600)

    def abort(self, upload_id: str, username: str, access_key: str, key: str) -> None:
        folder, _metadata = self._folder(upload_id, username=username, access_key=access_key, key=key)
        with exclusive_file_lock(self.root / ".staging-quota.lock"), exclusive_file_lock(folder / ".lock"):
            _folder, metadata = self._folder(upload_id, username=username, access_key=access_key, key=key)
            if metadata.get("completed") or metadata.get("completing"):
                raise MultipartError("The multipart upload does not exist")
            shutil.rmtree(folder)

    def list_parts(self, upload_id: str, username: str, access_key: str, key: str) -> list[dict]:
        _folder, metadata = self._folder(upload_id, username=username, access_key=access_key, key=key)
        if metadata.get("completed"):
            raise MultipartError("The multipart upload does not exist")
        return [
            {"part_number": int(number), **value}
            for number, value in sorted(metadata.get("parts", {}).items(), key=lambda row: int(row[0]))
        ]

    def list_uploads(self, username: str, access_key: str, prefix: str = "") -> list[dict]:
        result = []
        for folder in self.uploads.iterdir():
            if not folder.is_dir() or folder.is_symlink():
                continue
            metadata = self._read(folder)
            if (metadata.get("username") == username and metadata.get("access_key") == access_key
                    and not metadata.get("completed")):
                if str(metadata.get("key", "")).startswith(prefix):
                    result.append(metadata)
        return sorted(result, key=lambda row: (row.get("key", ""), row.get("created_at", 0)))
