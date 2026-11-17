"""In-process sliding-window rate limiter (no Redis needed for a single API process)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, float]:
        """Record a hit. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(0.0, q[0] + self.window - now)
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory under abuse
                for k in list(self._hits)[:10_000]:
                    if not self._hits[k]:
                        del self._hits[k]
            return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
