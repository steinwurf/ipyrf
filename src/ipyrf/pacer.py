from __future__ import annotations
import time


class Pacer:
    def __init__(self, bandwidth_bps: float):
        self.bandwidth_bps = bandwidth_bps
        self.next_eligible = None  # monotonic seconds

    def take(self, n_bytes: int) -> float:
        """
        Return how many seconds to sleep before sending `n_bytes`
        to maintain `rate_bps`. 0.0 means go now.
        """
        now = time.monotonic()
        bytes_per_second = self.bandwidth_bps / 8.0  # bits/s → bytes/s
        if self.next_eligible is None or now > self.next_eligible:
            # No accumulated credit: start from now
            self.next_eligible = now + n_bytes / bytes_per_second

        wait = max(0.0, self.next_eligible - now)
        # Schedule the next slot spaced by the time this chunk 'costs'
        self.next_eligible += n_bytes / bytes_per_second

        return wait

    def set_bandwidth_bps(self, new_bandwidth_bps: float):
        self.bandwidth_bps = new_bandwidth_bps
