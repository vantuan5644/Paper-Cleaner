"""Crossref rate limiter — base interval + exponential backoff on 429.

Crossref's "polite pool" (entered by sending a ``mailto``) publishes generous
limits and advertises the current quota in ``X-Rate-Limit-Limit`` /
``X-Rate-Limit-Interval`` headers, typically ~50 req/s. A 0.2s base interval
(5 req/s) stays comfortably polite while keeping batch runs fast. ``Retry-After``
is honored when the server sends it.
"""

from __future__ import annotations

import logging
import random
import threading
import time

logger = logging.getLogger(__name__)


class CrossrefRateLimiter:
    def __init__(
        self,
        *,
        base_interval_seconds: float = 0.2,
        backoff_factor: float = 2.0,
        max_retries: int = 3,
        jitter: float = 0.2,
    ) -> None:
        self.base_interval = float(base_interval_seconds)
        self.backoff_factor = float(backoff_factor)
        self.max_retries = int(max_retries)
        self.jitter = float(jitter)
        self._lock = threading.Lock()
        self._last_call_at: float = 0.0

    @property
    def min_interval(self) -> float:
        return self.base_interval

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last_call_at + self.base_interval - now
            if wait > 0:
                logger.debug("crossref rate limiter sleeping %.2fs", wait)
                time.sleep(wait + self._jitter())
            self._last_call_at = time.monotonic()

    def backoff_for_attempt(
        self, attempt: int, retry_after_seconds: float | None = None
    ) -> float:
        if retry_after_seconds is not None and retry_after_seconds > 0:
            return float(retry_after_seconds) + self._jitter()
        return self.base_interval * (self.backoff_factor**attempt) + self._jitter()

    def _jitter(self) -> float:
        if self.jitter <= 0:
            return 0.0
        return random.uniform(-self.jitter, self.jitter) * self.base_interval
