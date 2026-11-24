"""Bounded storage reads and local text extraction for Reader speech."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from .private_read_aloud import MAX_SPEECH_CHARS, ReadAloudError
from .v2.contracts import LogicalObjectId, StoragePort

MAX_READ_ALOUD_PDF_MIB = 64
MAX_READ_ALOUD_PDF_BYTES = MAX_READ_ALOUD_PDF_MIB * 1024 * 1024
MAX_READ_ALOUD_TEXT_BYTES = 256 * 1024
MAX_PDF_STDOUT_BYTES = 256 * 1024
_PDF_TIMEOUT_SECONDS = 6
_PDF_STAGE_SECONDS = 10


class _VerifiedCopyTarget:
    def __init__(self, stream: BinaryIO, maximum: int, expected_size: int, deadline: float | None):
        self.stream = stream
        self.maximum = maximum
        self.expected_size = expected_size
        self.deadline = deadline
        self.size = 0
        self.digest = hashlib.sha256()
        self.error: ReadAloudError | None = None

    def check_deadline(self) -> None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.error = ReadAloudError("pdf_source_timeout", 503)
            raise self.error

    def write(self, data: bytes) -> int:
        self.check_deadline()
        total = self.size + len(data)
        if total > self.maximum:
            self.error = ReadAloudError("document_too_large", 413)
        elif total > self.expected_size:
            self.error = ReadAloudError("document_changed", 409)
        if self.error:
            raise self.error
        try:
            if self.stream.write(data) != len(data):
                raise OSError("incomplete Reader staging write")
        except OSError as exc:
            self.error = ReadAloudError("pdf_stage_unavailable", 503)
            raise self.error from exc
        self.digest.update(data)
        self.size = total
        return len(data)


def copy_verified_reader_source(
    storage: StoragePort, document: dict[str, Any], stream: BinaryIO, maximum: int, *,
    expected_location: str = "", deadline: float | None = None,
) -> None:
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
    # Bytes written before StoragePort reports success are provisional: no consumer sees them.
    target = _VerifiedCopyTarget(stream, maximum, before.value.size, deadline)
    target.check_deadline()
    result = storage.copy_verified_to(object_id, target)
    # Some adapters map a writer exception to their generic StoragePort error.
    if target.error is not None:
        raise target.error
    target.check_deadline()
    if not result.ok:
        raise ReadAloudError("document_unavailable", 404)
    if result.value != before.value or target.size != before.value.size or target.digest.hexdigest() != expected:
        raise ReadAloudError("document_changed", 409)


def verified_speech_source(
    storage: StoragePort, document: dict[str, Any], maximum: int, *, expected_location: str = "",
) -> bytes:
    with io.BytesIO() as target:
        copy_verified_reader_source(storage, document, target, maximum, expected_location=expected_location)
        return target.getvalue()


@contextmanager
def staged_speech_pdf(storage: StoragePort, document: dict[str, Any], maximum: int) -> Iterator[Path]:
    """Supply a single verified, seekable file to Poppler; never materialize the PDF in Python."""
    deadline = time.monotonic() + _PDF_STAGE_SECONDS
    try:
        with tempfile.TemporaryDirectory(prefix="simpleoffice-private-pdf-") as directory:
            source = Path(directory) / "source.pdf"
            with open(source, "xb", opener=lambda path, flags: os.open(path, flags, 0o600)) as target:
                copy_verified_reader_source(storage, document, target, maximum, deadline=deadline)
            # Opening by pathname lets Poppler seek rather than buffering stdin as a stream.
            yield source
    except OSError as exc:
        raise ReadAloudError("pdf_stage_unavailable", 503) from exc


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


def bounded_tool_output(
    command: list[str], *, payload: bytes | None = None, max_output: int, timeout: float,
    unavailable_code: str, failure_code: str, timeout_code: str, failure_status: int = 422,
    overflow_code: str = "too_much_text", env: dict[str, str] | None = None,
) -> bytes:
    """Run a selected local tool with optional private stdin, bounded output and no diagnostics."""
    if timeout <= 0:
        raise ReadAloudError(timeout_code, 503)
    input_file = tempfile.TemporaryFile(mode="w+b") if payload is not None else nullcontext(subprocess.DEVNULL)
    with input_file as source:
        if payload is not None:
            assert not isinstance(source, int)
            source.write(payload)
            source.seek(0)
        try:
            process = subprocess.Popen(
                command, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, close_fds=True,
            )
        except OSError as exc:
            raise ReadAloudError(unavailable_code, 503) from exc
        with process:
            output = bytearray()
            overflow = threading.Event()
            read_failed = threading.Event()
            assert process.stdout is not None

            def collect() -> None:
                try:
                    while block := process.stdout.read(8192):
                        if len(output) + len(block) > max_output:
                            overflow.set()
                            process.kill()
                            return
                        output.extend(block)
                except OSError:
                    read_failed.set()

            thread = threading.Thread(target=collect, daemon=True)
            thread.start()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait(timeout=2)
                thread.join(timeout=1)
                raise ReadAloudError(timeout_code, 503) from exc
            thread.join(timeout=1)
            if overflow.is_set():
                raise ReadAloudError(overflow_code, 413)
            if read_failed.is_set() or thread.is_alive() or process.returncode != 0:
                raise ReadAloudError(failure_code, failure_status)
    return bytes(output)


def pdf_page_text(source: Path, page: int) -> str:
    tool = shutil.which("pdftotext")
    if not tool:
        raise ReadAloudError("pdf_extractor_unavailable", 503)
    return bounded_tool_output(
        [tool, "-f", str(page), "-l", str(page), "-enc", "UTF-8", str(source), "-"],
        max_output=MAX_PDF_STDOUT_BYTES, timeout=_PDF_TIMEOUT_SECONDS,
        unavailable_code="pdf_extractor_unavailable", failure_code="pdf_text_unavailable",
        timeout_code="read_aloud_timeout",
    ).decode("utf-8", errors="replace")
