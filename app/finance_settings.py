"""Defaults and validation for browser-assisted finance import settings."""

from __future__ import annotations

from typing import Any


DEFAULT_BANK_BROWSER_TIMEOUT_SECONDS = 300
MIN_BANK_BROWSER_TIMEOUT_SECONDS = 30
MAX_BANK_BROWSER_TIMEOUT_SECONDS = 1800
DEFAULT_FINANCE_IMPORT_LIMIT_BYTES = 25 * 1024 * 1024
MIN_FINANCE_IMPORT_LIMIT_BYTES = 1 * 1024 * 1024
MAX_FINANCE_IMPORT_LIMIT_BYTES = 25 * 1024 * 1024
DEFAULT_FINANCE_STAGING_TTL_SECONDS = 24 * 60 * 60
MIN_FINANCE_STAGING_TTL_SECONDS = 60 * 60
MAX_FINANCE_STAGING_TTL_SECONDS = 7 * 24 * 60 * 60


def validate_finance_settings(finance: Any) -> dict[str, Any]:
    """Validate and normalize persisted finance settings without losing fields."""
    if not isinstance(finance, dict):
        raise ValueError("finance settings must be an object")

    bank_browser_enabled = finance.get("bank_browser_enabled", True)
    if not isinstance(bank_browser_enabled, bool):
        raise ValueError("bank browser setting must be boolean")

    bank_browser_timeout = finance.get(
        "bank_browser_timeout_seconds", DEFAULT_BANK_BROWSER_TIMEOUT_SECONDS,
    )
    if (
        isinstance(bank_browser_timeout, bool)
        or not isinstance(bank_browser_timeout, int)
        or not MIN_BANK_BROWSER_TIMEOUT_SECONDS
        <= bank_browser_timeout
        <= MAX_BANK_BROWSER_TIMEOUT_SECONDS
    ):
        raise ValueError("bank browser timeout must be between 30 and 1800 seconds")

    import_limit = finance.get(
        "statement_import_limit_bytes", DEFAULT_FINANCE_IMPORT_LIMIT_BYTES,
    )
    if (
        isinstance(import_limit, bool)
        or not isinstance(import_limit, int)
        or not MIN_FINANCE_IMPORT_LIMIT_BYTES
        <= import_limit
        <= MAX_FINANCE_IMPORT_LIMIT_BYTES
    ):
        raise ValueError("finance import limit must be between 1 and 25 MiB")

    staging_ttl = finance.get(
        "statement_staging_ttl_seconds", DEFAULT_FINANCE_STAGING_TTL_SECONDS,
    )
    if (
        isinstance(staging_ttl, bool)
        or not isinstance(staging_ttl, int)
        or not MIN_FINANCE_STAGING_TTL_SECONDS
        <= staging_ttl
        <= MAX_FINANCE_STAGING_TTL_SECONDS
    ):
        raise ValueError("finance staging TTL must be between 1 hour and 7 days")

    return {
        "bank_browser_enabled": bank_browser_enabled,
        "bank_browser_timeout_seconds": bank_browser_timeout,
        "statement_import_limit_bytes": import_limit,
        "statement_staging_ttl_seconds": staging_ttl,
    }


def normalize_finance_settings(finance: Any) -> dict[str, Any]:
    """Normalize legacy persisted values independently without data loss.

    One invalid stored field must not reset unrelated valid controls when a
    later partial update writes the normalized settings back.
    """
    source = finance if isinstance(finance, dict) else {}
    normalized = {
        "bank_browser_enabled": source.get("bank_browser_enabled", True),
        "bank_browser_timeout_seconds": source.get(
            "bank_browser_timeout_seconds", DEFAULT_BANK_BROWSER_TIMEOUT_SECONDS,
        ),
        "statement_import_limit_bytes": source.get(
            "statement_import_limit_bytes", DEFAULT_FINANCE_IMPORT_LIMIT_BYTES,
        ),
        "statement_staging_ttl_seconds": source.get(
            "statement_staging_ttl_seconds", DEFAULT_FINANCE_STAGING_TTL_SECONDS,
        ),
    }
    if not isinstance(normalized["bank_browser_enabled"], bool):
        normalized["bank_browser_enabled"] = True
    ranges = {
        "bank_browser_timeout_seconds": (
            MIN_BANK_BROWSER_TIMEOUT_SECONDS, MAX_BANK_BROWSER_TIMEOUT_SECONDS,
        ),
        "statement_import_limit_bytes": (
            MIN_FINANCE_IMPORT_LIMIT_BYTES, MAX_FINANCE_IMPORT_LIMIT_BYTES,
        ),
        "statement_staging_ttl_seconds": (
            MIN_FINANCE_STAGING_TTL_SECONDS, MAX_FINANCE_STAGING_TTL_SECONDS,
        ),
    }
    defaults = {
        "bank_browser_timeout_seconds": DEFAULT_BANK_BROWSER_TIMEOUT_SECONDS,
        "statement_import_limit_bytes": DEFAULT_FINANCE_IMPORT_LIMIT_BYTES,
        "statement_staging_ttl_seconds": DEFAULT_FINANCE_STAGING_TTL_SECONDS,
    }
    for key, (minimum, maximum) in ranges.items():
        value = normalized[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            normalized[key] = defaults[key]
    return normalized
