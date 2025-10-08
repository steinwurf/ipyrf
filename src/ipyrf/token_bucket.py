from __future__ import annotations
import time


class TokenBucket:
    def __init__(self, rate_bps: float, quantum_bytes: int):
        self.rate_bps = rate_bps / 8.0
        self.capacity = max(quantum_bytes * 2, int(self.rate_bps))
        self.tokens = 0.0
        self.last = time.monotonic()

    def take(self, n: int) -> float:
        """
        Try to take n bytes worth of tokens. Return sleep time if insufficient.
        """
        now = time.monotonic()
        elapsed = now - self.last
        self.last = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_bps)
        if self.tokens >= n:
            self.tokens -= n
            return 0.0
        deficit = n - self.tokens
        return deficit / self.rate_bps
