"""Bounded storage reads and local text extraction for Reader speech."""
from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tempfile
import threading
from typing import Any

from .private_read_aloud import MAX_SPEECH_CHARS, ReadAloudError
from .v2.contracts import LogicalObjectId, StoragePort

MAX_READ_ALOUD_PDF_BYTES = 16 * 1024 * 1024
MAX_READ_ALOUD_TEXT_BYTES = 256 * 1024
MAX_PDF_STDOUT_BYTES = 256 * 1024
_PDF_TIMEOUT_SECONDS = 6


class _CappedBuffer(io.BytesIO):
    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit
        self.exceeded = False

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self.limit:
            self.exceeded = True
            raise ValueError("private reader copy limit exceeded")
        return super().write(data)


def verified_speech_source(
    storage: StoragePort, document: dict[str, Any], maximum: int, *, expected_location: str = "",
) -> bytes:
    object_id = LogicalObjectId(str(document["document_id"]))
    expected = str(document.get("sha256") or document.get("content_sha256") or "").strip().casefold()
    before = storage.stat(object_id)
    if not before.ok:
        raise ReadAloudError("document_unavailable", 404)
    if expected_location and before.value.location.relative_path != expected_location:
        raise ReadAloudError("document_unavailable", 404)
    if before.value.size > maximum:
        raise ReadAloudError("document_too_large", 413)
    if not expected:
        raise ReadAloudError("document_changed", 409)
    with _CappedBuffer(maximum) as target:
        result = storage.copy_verified_to(object_id, target)
        if target.exceeded:
            raise ReadAloudError("document_too_large", 413)
        if not result.ok:
            raise ReadAloudError("document_unavailable", 404)
        if result.value != before.value or target.tell() != before.value.size:
            raise ReadAloudError("document_changed", 409)
        payload = target.getvalue()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ReadAloudError("document_changed", 409)
        return payload


def plain_text_sections(payload: bytes) -> list[str]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ReadAloudError("invalid_text", 422) from exc
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text) or "\x7f" in text:
        raise ReadAloudError("invalid_text", 422)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sections = []
    start = 0
    while start < len(text):
        end = min(start + MAX_SPEECH_CHARS, len(text))
        if end < len(text):
            boundary = max(text.rfind(char, start + MAX_SPEECH_CHARS // 2, end) for char in ("\n", " ", "\t"))
            if boundary >= 0:
                end = boundary + 1
        sections.append(text[start:end])
        start = end
    return sections or [""]


def pdf_page_text(payload: bytes, page: int) -> str:
    tool = shutil.which("pdftotext")
    if not tool:
        raise ReadAloudError("pdf_extractor_unavailable", 503)
    with tempfile.TemporaryFile(mode="w+b") as source:
        source.write(payload)
        source.seek(0)
        command = [tool, "-f", str(page), "-l", str(page), "-enc", "UTF-8", "-", "-"]
        with subprocess.Popen(command, stdin=source, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, close_fds=True) as process:
            output = bytearray()
            overflow = threading.Event()
            assert process.stdout is not None

            def collect() -> None:
                while block := process.stdout.read(8192):
                    if len(output) + len(block) > MAX_PDF_STDOUT_BYTES:
                        overflow.set()
                        process.kill()
                        return
                    output.extend(block)

            thread = threading.Thread(target=collect, daemon=True)
            thread.start()
            try:
                process.wait(timeout=_PDF_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait(timeout=2)
                thread.join(timeout=1)
                raise ReadAloudError("read_aloud_timeout", 503) from exc
            thread.join(timeout=1)
            if overflow.is_set():
                raise ReadAloudError("too_much_text", 413)
            if thread.is_alive() or process.returncode != 0:
                raise ReadAloudError("pdf_text_unavailable", 422)
    return output.decode("utf-8", errors="replace")
