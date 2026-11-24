"""Bounded, local scan recognition for a single protected Reader PDF page."""
from __future__ import annotations

import shutil
import struct
import time
from pathlib import Path

from .document_store_core import ocr_subprocess_environment
from .private_read_aloud import MAX_SPEECH_CHARS, ReadAloudError
from .reader_speech_text import MAX_PDF_STDOUT_BYTES, bounded_tool_output

MAX_OCR_EDGE = 2200
MAX_OCR_RASTER_BYTES = 8 * 1024 * 1024
_OCR_TIMEOUT_SECONDS = 24


def tesseract_languages(tesseract: str, env: dict[str, str], remaining: float) -> set[str]:
    output = bounded_tool_output(
        [tesseract, "--list-langs"], max_output=8192, timeout=min(remaining, 3),
        unavailable_code="ocr_unavailable", failure_code="ocr_unavailable", timeout_code="ocr_timeout",
        failure_status=503, env=env,
    )
    return set(output.decode("utf-8", errors="replace").splitlines())


def _language_pack(tesseract: str, language: str, env: dict[str, str], remaining: float) -> str:
    installed = tesseract_languages(tesseract, env, remaining)
    order = ("eng", "deu") if language.lower().startswith("en") else ("deu", "eng")
    matched = [pack for pack in order if pack in installed]
    if not matched:
        raise ReadAloudError("ocr_unavailable", 503)
    return "+".join(matched)


def _check_raster(raster: bytes) -> None:
    if len(raster) < 33 or raster[:16] != b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR":
        raise ReadAloudError("ocr_render_failed", 422)
    width, height = struct.unpack(">II", raster[16:24])
    if not (1 <= width <= MAX_OCR_EDGE and 1 <= height <= MAX_OCR_EDGE):
        raise ReadAloudError("ocr_raster_too_large", 413)


def pdf_page_has_images(source: Path, page: int) -> bool:
    """Inspect only the selected page's image table; do not extract any images."""
    tool = shutil.which("pdfimages")
    if not tool:
        raise ReadAloudError("ocr_unavailable", 503)
    table = bounded_tool_output(
        [tool, "-f", str(page), "-l", str(page), "-list", str(source)],
        max_output=128 * 1024, timeout=4,
        unavailable_code="ocr_unavailable", failure_code="ocr_render_failed", timeout_code="ocr_timeout",
        overflow_code="ocr_raster_too_large",
    )
    return any(
        len(fields := line.split()) >= 3 and fields[0].isdigit() and fields[1].isdigit()
        for line in table.splitlines()
    )


def supplement_native_text(native: str, recognized: str) -> str:
    """Preserve native text and suppress duplicate re-recognition of native lines."""
    def key(line: str) -> str:
        return " ".join(line.split()).casefold()

    native_keys = {key(line) for line in native.splitlines() if line.strip()}
    if len(" ".join(native.split())) > MAX_SPEECH_CHARS or len(" ".join(recognized.split())) > 2 * MAX_SPEECH_CHARS:
        raise ReadAloudError("too_much_text", 413)
    # Only suppress a near-copy when OCR omitted up to three letters inside
    # otherwise identical words. Do not conflate added/removed words, changed
    # numbers, or altered prefixes/suffixes from a distinct raster passage.
    def missed_letters(previous: str, current: str) -> bool:
        original_words, current_words = previous.split(), current.split()
        if len(original_words) != len(current_words):
            return False
        differences = 0
        for original, current_word in zip(original_words, current_words):
            if original == current_word:
                continue
            if (not original.isalpha() or len(original) < 5
                    or len(original) - len(current_word) != 1
                    or not any(original[:i] + original[i + 1:] == current_word
                               for i in range(1, len(original) - 1))):
                return False
            differences += 1
        return 1 <= differences <= 3

    by_start: dict[str, list[str]] = {}
    for row in native.splitlines():
        line = key(row)
        if 10 <= len(line) <= 120 and line not in by_start.setdefault(line[0], []):
            if len(by_start[line[0]]) < 16:
                by_start[line[0]].append(line)
    seen = set(native_keys)
    additions = []
    for line in recognized.splitlines():
        identity = key(line)
        if not identity or identity in seen:
            continue
        candidates = by_start.get(identity[0], ()) if 10 <= len(identity) <= 120 else ()
        if any(0 < len(previous) - len(identity) <= 3 and missed_letters(previous, identity)
               for previous in candidates):
            continue
        seen.add(identity)
        additions.append(line.strip())
    return "\n".join([native.strip(), *additions]).strip()


def recognize_pdf_page(source: Path, page: int, language: str) -> str:
    """Render and recognize one page without storing an OCR copy or changing the source."""
    poppler, tesseract = shutil.which("pdftoppm"), shutil.which("tesseract")
    if not poppler or not tesseract:
        raise ReadAloudError("ocr_unavailable", 503)
    env = ocr_subprocess_environment()
    env["OMP_THREAD_LIMIT"] = "1"
    deadline = time.monotonic() + _OCR_TIMEOUT_SECONDS
    pack = _language_pack(tesseract, language, env, deadline - time.monotonic())
    raster = bounded_tool_output(
        [poppler, "-f", str(page), "-l", str(page), "-scale-to", str(MAX_OCR_EDGE),
         "-singlefile", "-gray", "-png", str(source)],
        max_output=MAX_OCR_RASTER_BYTES, timeout=min(8, deadline - time.monotonic()),
        unavailable_code="ocr_unavailable", failure_code="ocr_render_failed", timeout_code="ocr_timeout",
        overflow_code="ocr_raster_too_large",
    )
    _check_raster(raster)
    raw = bounded_tool_output(
        [tesseract, "stdin", "stdout", "--dpi", "180", "-l", pack, "--psm", "3"],
        payload=raster, max_output=MAX_PDF_STDOUT_BYTES, timeout=deadline - time.monotonic(),
        unavailable_code="ocr_unavailable", failure_code="ocr_failed", timeout_code="ocr_timeout", env=env,
    )
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise ReadAloudError("ocr_no_text", 422)
    return text
