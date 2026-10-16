"""Bounded retry with exponential backoff for temporary failures only."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from .results import StageError, Status

T = TypeVar("T")


def with_retries(fn: Callable[[], T], limit: int, base: float, log: Callable[[str], None] | None = None, sleep=time.sleep) -> T:
    attempt = 0
    while True:
        try:
            return fn()
        except StageError as exc:
            if exc.status != Status.RETRYABLE_FAILURE or attempt >= limit:
                raise
            delay = base * (2 ** attempt)
            if log:
                log(f"retryable failure ({exc}); retrying in {delay:.1f}s [{attempt + 1}/{limit}]")
            sleep(delay)
            attempt += 1
