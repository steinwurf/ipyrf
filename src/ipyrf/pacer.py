from __future__ import annotations
import time


class Pacer:
    def __init__(self, rate_bps: float):
        self.rate_bps = rate_bps / 8.0  # bits/s → bytes/s
        self.next_eligible = None  # monotonic seconds

    def take(self, n_bytes: int) -> float:
        """
        Return how many seconds to sleep before sending `n_bytes`
        to maintain `rate_bps`. 0.0 means go now.
        """
        now = time.monotonic()
        if self.next_eligible is None or now > self.next_eligible:
            # No accumulated credit: start from now
            self.next_eligible = now + n_bytes / self.rate_bps

        wait = max(0.0, self.next_eligible - now)
        # Schedule the next slot spaced by the time this chunk 'costs'
        self.next_eligible += n_bytes / self.rate_bps

        return wait

    def set_rate_bps(self, new_rate_bps: float):
        self.rate_bps = new_rate_bps / 8.0
