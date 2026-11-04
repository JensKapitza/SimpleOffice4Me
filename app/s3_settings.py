"""S3 overlay connection settings (no environment).

Region, clock skew, staging quota and the optional upload cap live in the
user-home disk store (``s3`` section) and are edited from the admin S3 page.
``max_upload_mib`` of ``None`` keeps the historic behavior of following the
global request cap.
"""
from __future__ import annotations

import logging
import re
from typing import Any


_LOGGER = logging.getLogger(__name__)

DEFAULT_S3_REGION = "us-east-1"
MAX_S3_REGION_CHARS = 63
_S3_REGION = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DEFAULT_S3_CLOCK_SKEW_SECONDS = 900
MIN_S3_CLOCK_SKEW_SECONDS = 0
MAX_S3_CLOCK_SKEW_SECONDS = 3600
DEFAULT_S3_MAX_STAGING_MIB = 2048
MIN_S3_MAX_STAGING_MIB = 64
MAX_S3_MAX_STAGING_MIB = 65536
DEFAULT_S3_MAX_UPLOAD_MIB: int | None = None
MIN_S3_MAX_UPLOAD_MIB = 1
MAX_S3_MAX_UPLOAD_MIB = 65536


def normalize_s3_settings(value: Any) -> dict[str, Any]:
    """Coerce a stored s3 section to known settings with safe defaults."""
    stored = value if isinstance(value, dict) else {}
    enabled = stored.get("enabled", False)
    if not isinstance(enabled, bool):
        _LOGGER.warning("Invalid stored S3 enabled flag; disabling overlay")
        enabled = False
    region = stored.get("region", DEFAULT_S3_REGION)
    if not isinstance(region, str) or not _S3_REGION.fullmatch(region.strip().lower()):
        _LOGGER.warning("Invalid stored S3 region; using the default")
        region = DEFAULT_S3_REGION
    else:
        region = region.strip().lower()
    clock_skew = stored.get("clock_skew_seconds", DEFAULT_S3_CLOCK_SKEW_SECONDS)
    if (
        isinstance(clock_skew, bool)
        or not isinstance(clock_skew, int)
        or not MIN_S3_CLOCK_SKEW_SECONDS <= clock_skew <= MAX_S3_CLOCK_SKEW_SECONDS
    ):
        _LOGGER.warning("Invalid stored S3 clock skew; using the default")
        clock_skew = DEFAULT_S3_CLOCK_SKEW_SECONDS
    staging = stored.get("max_staging_mib", DEFAULT_S3_MAX_STAGING_MIB)
    if (
        isinstance(staging, bool)
        or not isinstance(staging, int)
        or not MIN_S3_MAX_STAGING_MIB <= staging <= MAX_S3_MAX_STAGING_MIB
    ):
        _LOGGER.warning("Invalid stored S3 staging quota; using the default")
        staging = DEFAULT_S3_MAX_STAGING_MIB
    max_upload = stored.get("max_upload_mib", DEFAULT_S3_MAX_UPLOAD_MIB)
    if max_upload is not None and (
        isinstance(max_upload, bool)
        or not isinstance(max_upload, int)
        or not MIN_S3_MAX_UPLOAD_MIB <= max_upload <= MAX_S3_MAX_UPLOAD_MIB
    ):
        _LOGGER.warning("Invalid stored S3 upload cap; following the request cap")
        max_upload = DEFAULT_S3_MAX_UPLOAD_MIB
    return {
        "enabled": enabled,
        "region": region,
        "clock_skew_seconds": clock_skew,
        "max_staging_mib": staging,
        "max_upload_mib": max_upload,
    }


def validate_s3_settings(value: Any) -> dict[str, Any]:
    """Validate s3 settings for saving and return their canonical schema."""
    if not isinstance(value, dict):
        raise ValueError("S3 settings must be an object")
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("S3 enabled must be a boolean")
    region = value.get("region", DEFAULT_S3_REGION)
    if not isinstance(region, str) or not _S3_REGION.fullmatch(region.strip().lower()):
        raise ValueError("S3 region must look like us-east-1")
    clock_skew = value.get("clock_skew_seconds", DEFAULT_S3_CLOCK_SKEW_SECONDS)
    if (
        isinstance(clock_skew, bool)
        or not isinstance(clock_skew, int)
        or not MIN_S3_CLOCK_SKEW_SECONDS <= clock_skew <= MAX_S3_CLOCK_SKEW_SECONDS
    ):
        raise ValueError("S3 clock skew must be between 0 and 3600 seconds")
    staging = value.get("max_staging_mib", DEFAULT_S3_MAX_STAGING_MIB)
    if (
        isinstance(staging, bool)
        or not isinstance(staging, int)
        or not MIN_S3_MAX_STAGING_MIB <= staging <= MAX_S3_MAX_STAGING_MIB
    ):
        raise ValueError("S3 staging quota must be between 64 and 65536 MiB")
    max_upload = value.get("max_upload_mib", DEFAULT_S3_MAX_UPLOAD_MIB)
    if max_upload is not None and (
        isinstance(max_upload, bool)
        or not isinstance(max_upload, int)
        or not MIN_S3_MAX_UPLOAD_MIB <= max_upload <= MAX_S3_MAX_UPLOAD_MIB
    ):
        raise ValueError("S3 upload cap must be empty or between 1 and 65536 MiB")
    normalized = normalize_s3_settings(value)
    normalized["enabled"] = enabled
    return normalized
