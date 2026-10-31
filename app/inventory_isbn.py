"""ISBN normalization shared by inventory capture and ContextFlow."""

from __future__ import annotations

import re
from typing import Any


def _isbn13_checksum(prefix: str) -> str:
    total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(prefix))
    return str((10 - total % 10) % 10)


def normalize_isbn(value: Any) -> str:
    """Return a validated canonical ISBN-13, converting valid ISBN-10 values."""
    raw = re.sub(r"[^0-9Xx]", "", str(value or ""))
    if len(raw) == 13 and raw.isdigit():
        if not raw.startswith(("978", "979")):
            raise ValueError("ISBN-13 muss mit 978 oder 979 beginnen")
        if _isbn13_checksum(raw[:12]) != raw[-1]:
            raise ValueError("Ungültige ISBN-Prüfziffer")
        return raw
    if len(raw) == 10 and raw[:9].isdigit() and (raw[-1].isdigit() or raw[-1].upper() == "X"):
        digits = [int(char) for char in raw[:9]] + [10 if raw[-1].upper() == "X" else int(raw[-1])]
        if sum((10 - index) * digit for index, digit in enumerate(digits)) % 11:
            raise ValueError("Ungültige ISBN-Prüfziffer")
        prefix = "978" + raw[:9]
        return prefix + _isbn13_checksum(prefix)
    raise ValueError("ISBN muss eine gültige ISBN-10 oder ISBN-13 sein")


def isbn_from_barcode(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 13 and digits.startswith(("978", "979")):
        try:
            return normalize_isbn(digits)
        except ValueError:
            return ""
    return ""
