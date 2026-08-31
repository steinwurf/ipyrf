from __future__ import annotations
import time
from typing import Dict, Optional


class LatencyTracker:
    """Running and per-interval one-way latency statistics."""

    def __init__(self) -> None:
        self.enabled = False
        self._sum = 0.0
        self._count = 0
        self._min = float("inf")
        self._max = 0.0
        self._interval_sum = 0.0
        self._interval_count = 0
        self._interval_min = float("inf")
        self._interval_max = 0.0

    def enable(self) -> None:
        self.enabled = True

    def observe(self, timestamp_ns: int, now_ns: Optional[int] = None) -> None:
        if not self.enabled or timestamp_ns <= 0:
            return
        if now_ns is None:
            now_ns = time.time_ns()
        latency_ms = (now_ns - timestamp_ns) / 1e6
        self._sum += latency_ms
        self._count += 1
        self._min = min(self._min, latency_ms)
        self._max = max(self._max, latency_ms)
        self._interval_sum += latency_ms
        self._interval_count += 1
        self._interval_min = min(self._interval_min, latency_ms)
        self._interval_max = max(self._interval_max, latency_ms)

    def interval_fields(self) -> Dict[str, float]:
        if self._interval_count <= 0:
            return {}
        return {
            "latency_avg": self._interval_sum / self._interval_count,
            "latency_min": self._interval_min,
            "latency_max": self._interval_max,
            "latency_count": self._interval_count,
        }

    def reset_interval(self) -> None:
        self._interval_sum = 0.0
        self._interval_count = 0
        self._interval_min = float("inf")
        self._interval_max = 0.0

    def summary_fields(self) -> Dict[str, float]:
        if self._count <= 0:
            return {}
        return {
            "latency_avg": self._sum / self._count,
            "latency_min": self._min,
            "latency_max": self._max,
            "latency_count": self._count,
        }
