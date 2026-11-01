"""Display-only fallbacks for invoice rendering (legacy rows)."""

from __future__ import annotations


def _display_zugferd(value):
    """Display-only ZUGFeRD fallback for legacy invoice rows.

    Old persisted records may omit ``zugferd`` entirely or carry a
    malformed value, while the detail template strictly requires the
    mapping. This builds a safe copy for rendering only: present values
    are kept, missing ones fall back to ``not_recorded``/empty display
    data (the template's vocabulary for "never captured"). The stored
    JSON is never touched, and unrelated template errors still
    surface unchanged.
    """
    source = value if isinstance(value, dict) else {}
    validation = source.get("validation")
    if not isinstance(validation, dict):
        validation = {}
    fallback = dict(source)
    fallback["status"] = source.get("status") or "not_recorded"
    fallback["version"] = source.get("version") or ""
    fallback["profile"] = source.get("profile") or ""
    fallback["pdfa_pipeline"] = source.get("pdfa_pipeline") or ""
    fallback["validation"] = validation
    return fallback
