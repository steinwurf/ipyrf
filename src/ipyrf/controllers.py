from __future__ import annotations
import time
from typing import Dict, Optional

from .pacer import Pacer


class BasePacingController:
    def __init__(self, interval_seconds: float = 1.0):
        self.interval_seconds = interval_seconds

    def is_pacing(self) -> bool:
        return False

    def maybe_sleep(self, n_bytes: int):
        return

    def get_update_fields(self) -> Dict[str, float]:
        return {}

    def should_stop(self) -> bool:
        return False

    def stop_reason(self) -> str:
        return "unknown"

    def start(self):
        pass

    def stop(self):
        pass


class StaticPacingController(BasePacingController):
    def __init__(
        self,
        bandwidth_bps: Optional[float],
        duration_seconds: float,
        interval_seconds: float,
    ):
        super().__init__(interval_seconds=interval_seconds)
        self.bandwidth_bps = bandwidth_bps
        self.duration_seconds = duration_seconds
        self.start_time = None
        self.tb: Optional[Pacer] = None
        if bandwidth_bps is not None:
            self.tb = Pacer(bandwidth_bps)

    def is_pacing(self) -> bool:
        return self.tb is not None

    def maybe_sleep(self, n_bytes: int):
        if self.tb is None:
            return
        sleep_time = self.tb.take(n_bytes)
        if sleep_time <= 0:
            return
        if sleep_time > 0.5:
            time.sleep(sleep_time)
        else:
            # Busy wait for very short sleeps
            end = time.perf_counter() + sleep_time
            while time.perf_counter() < end:
                pass

    def get_update_fields(self) -> Dict[str, float]:
        if self.bandwidth_bps is None:
            return {}
        return {"target_bandwidth_bps": float(self.bandwidth_bps)}

    def start(self):
        """Call this when the test starts to begin duration tracking."""
        self.start_time = time.time()

    def should_stop(self) -> bool:
        """Check if the test should stop based on duration."""
        assert self.start_time is not None
        return (time.time() - self.start_time) >= self.duration_seconds

    def stop_reason(self) -> str:
        return "duration"
