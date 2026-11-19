"""Small, self-contained binary packets for QR and acoustic transfers.

Wire format (ASCII): ``SO4ME1|<filename>|<byte-count>|<sha256>|<base64>``.
The MIME type is deliberately fixed to application/octet-stream on receipt.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re

MAX_PACKET_FILE_BYTES = 1100
MAX_AUDIO_PAYLOAD_BYTES = 128
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_PREFIX = "SO4ME1|"


def sanitize_filename(filename: object) -> str:
    """Return a short, path-free ASCII filename suitable for a download."""
    name = str(filename or "file").replace("\\", "/").rsplit("/", 1)[-1]
    name = _SAFE_FILENAME.sub("_", name).strip("._-")[:80]
    return name or "file.bin"


def encode_packet(filename: str, data: bytes, *, max_bytes: int = MAX_PACKET_FILE_BYTES) -> str:
    """Encode bytes in a compact ASCII envelope; raises ValueError on bad input."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) > max_bytes:
        raise ValueError(f"file exceeds the {max_bytes}-byte transfer limit")
    safe_name = sanitize_filename(filename)
    encoded = base64.b64encode(data).decode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    return f"{_PREFIX}{safe_name}|{len(data)}|{digest}|{encoded}"


def decode_packet(packet: str, *, max_bytes: int = MAX_PACKET_FILE_BYTES) -> dict:
    """Validate and decode a packet, returning filename, bytes and fixed MIME."""
    if not isinstance(packet, str) or len(packet) > (max_bytes * 4 // 3 + 256):
        raise ValueError("invalid or oversized transfer packet")
    parts = packet.split("|", 4)
    if len(parts) != 5 or parts[0] != "SO4ME1" or not packet.isascii():
        raise ValueError("unrecognized transfer packet")
    _, name, size_text, digest, encoded = parts
    if sanitize_filename(name) != name or not size_text.isdecimal() or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid transfer metadata")
    size = int(size_text)
    if size > max_bytes:
        raise ValueError(f"file exceeds the {max_bytes}-byte transfer limit")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid transfer data") from exc
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("transfer data is incomplete or corrupted")
    return {"filename": name, "data": data, "mime": "application/octet-stream"}
