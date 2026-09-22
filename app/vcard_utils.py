"""Small RFC-oriented helpers shared by vCard import/export code."""
from __future__ import annotations

import hashlib
import re
from typing import Iterable


MAX_VCARD_BYTES = 16 * 1024 * 1024
MAX_CONTACT_PHOTO_BYTES = 8 * 1024 * 1024
# 8 MiB binary payloads expand to roughly 10.67 MiB in base64. Keep enough
# headroom for the property header/data-URI without silently truncating data.
MAX_RAW_PHOTO_LINE_CHARS = 4 * ((MAX_CONTACT_PHOTO_BYTES + 2) // 3) + 4096
MAX_VCARD_UID_BYTES = 2048
VCARD_VERSIONS = {"3.0", "4.0"}
_SAFE_RESOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")


def property_name(line: str) -> str:
    header = str(line).partition(":")[0]
    return header.split(";", 1)[0].rsplit(".", 1)[-1].upper()


def unfold_vcard_lines(card: str) -> list[str]:
    if not isinstance(card, str):
        raise ValueError("vCard must be text")
    if len(card.encode("utf-8")) > MAX_VCARD_BYTES:
        raise ValueError("vCard is too large")
    physical = card.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for physical_line in physical:
        if physical_line.startswith((" ", "\t")):
            if not lines:
                raise ValueError("vCard continuation has no previous line")
            lines[-1] += physical_line[1:]
        else:
            lines.append(physical_line)
    while lines and not lines[-1]:
        lines.pop()
    if lines and lines[0].startswith("\ufeff"):
        lines[0] = lines[0].lstrip("\ufeff")
    return lines


def validate_single_vcard(card: str) -> tuple[list[str], str]:
    """Validate one complete vCard 3.0/4.0 envelope before parsing fields."""
    lines = unfold_vcard_lines(card)
    if not lines:
        raise ValueError("empty vCard")
    begins = [index for index, line in enumerate(lines) if line.upper() == "BEGIN:VCARD"]
    ends = [index for index, line in enumerate(lines) if line.upper() == "END:VCARD"]
    if begins != [0] or ends != [len(lines) - 1]:
        raise ValueError("vCard must contain exactly one complete BEGIN:VCARD/END:VCARD record")

    versions = [
        (index, line.partition(":")[2].strip())
        for index, line in enumerate(lines)
        if property_name(line) == "VERSION"
    ]
    if len(versions) != 1:
        raise ValueError("vCard must contain exactly one VERSION property")
    version_index, version = versions[0]
    if version not in VCARD_VERSIONS:
        raise ValueError("only vCard 3.0 and 4.0 are supported")
    if version_index != 1:
        raise ValueError("VERSION must immediately follow BEGIN:VCARD")
    if not any(property_name(line) == "FN" for line in lines[2:-1]):
        raise ValueError("vCard requires an FN property")
    return lines, version


def safe_resource_id(uid: str) -> str:
    """Map arbitrary RFC UID text to a URL-safe stable local resource id."""
    value = str(uid or "").strip()
    if not value:
        return ""
    if len(value.encode("utf-8")) > MAX_VCARD_UID_BYTES:
        raise ValueError("vCard UID is too long")
    if _SAFE_RESOURCE_ID.fullmatch(value) and value not in {".", ".."}:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return f"vcard-{digest}"


def fold_content_line(line: str, limit: int = 75) -> list[str]:
    """Fold one UTF-8 content line without splitting a code point."""
    text = str(line)
    if not text:
        return [""]
    chunks: list[str] = []
    current = ""
    current_bytes = 0
    capacity = limit
    for char in text:
        size = len(char.encode("utf-8"))
        if size > capacity and current:
            chunks.append(current)
            current = ""
            current_bytes = 0
            capacity = limit - 1
        if size > capacity:
            # A Unicode scalar is at most four UTF-8 bytes, so this can only
            # happen with an invalid tiny custom limit.
            raise ValueError("vCard fold limit is too small")
        if current_bytes + size > capacity:
            chunks.append(current)
            current = char
            current_bytes = size
            capacity = limit - 1
        else:
            current += char
            current_bytes += size
    if current or not chunks:
        chunks.append(current)
    return [chunks[0], *[" " + chunk for chunk in chunks[1:]]]


def serialize_folded(lines: Iterable[str]) -> str:
    physical: list[str] = []
    for line in lines:
        physical.extend(fold_content_line(str(line)))
    return "\r\n".join([*physical, ""])
