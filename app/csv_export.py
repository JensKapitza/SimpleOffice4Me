"""Formatting shared by CSV exports that contain untrusted text."""


def spreadsheet_safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    first = text.lstrip(" \t\r\n\x00")[:1]
    return "'" + text if first in {"=", "+", "-", "@"} else text
