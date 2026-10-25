"""Bounded retries for idempotent FinTS read transport failures."""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar


_T = TypeVar("_T")
_MAX_ATTEMPTS = 3
_BASE_DELAY_SECONDS = 0.5


def is_retryable_transport_error(error: Exception) -> bool:
    """Retry only python-fints transport failures backed by transient requests errors."""
    try:
        from fints.exceptions import FinTSConnectionError
        from requests import exceptions as request_errors
    except ImportError:
        return False

    if not isinstance(error, FinTSConnectionError):
        return False
    cause = error.__cause__
    if cause is None or isinstance(
        cause, (request_errors.SSLError, request_errors.ProxyError),
    ):
        return False
    retryable_causes = (
        request_errors.Timeout,
        request_errors.ConnectionError,
        request_errors.ChunkedEncodingError,
    )
    return isinstance(cause, retryable_causes)


def run_read_with_bounded_retry(operation: Callable[[], _T]) -> _T:
    """Retry a read at most twice, using capped exponential jitter between attempts."""
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return operation()
        except Exception as exc:
            if attempt + 1 >= _MAX_ATTEMPTS or not is_retryable_transport_error(exc):
                raise
            maximum_delay = _BASE_DELAY_SECONDS * (2**attempt)
            time.sleep(random.uniform(maximum_delay / 2, maximum_delay))
    raise AssertionError("bounded read retry loop exited without returning or raising")
