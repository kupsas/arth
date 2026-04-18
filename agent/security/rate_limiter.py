"""
Simple sliding-window rate limiter for one REPL / WebSocket session (in-memory).
"""

from __future__ import annotations

import time
from collections import deque


class SessionRateLimiter:
    """
    Allow at most ``max_per_minute`` events in any rolling 60-second window.

    Call :meth:`check_and_record` once per user message before expensive work.
    """

    def __init__(self, max_per_minute: int) -> None:
        self._max = max(1, int(max_per_minute))
        self._ts: deque[float] = deque()

    def check_and_record(self) -> bool:
        """
        Record this request's timestamp and return True if still under the cap.

        If over the cap, the newest timestamp is **not** recorded (the call is rejected).
        """
        now = time.monotonic()
        cutoff = now - 60.0
        while self._ts and self._ts[0] < cutoff:
            self._ts.popleft()
        if len(self._ts) >= self._max:
            return False
        self._ts.append(now)
        return True
