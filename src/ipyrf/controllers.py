from __future__ import annotations
import time
from typing import Dict, Optional

from .pacer import Pacer
from .traffic_pattern import TrafficPattern
from .utils import sleep_until_ns

_NS_PER_SECOND = 1_000_000_000


class BasePacingController:
    """Base class for client send pacing.

    Subclasses can override :meth:`next_send` to decide both *when* to
    send and *how many* bytes to send. The default implementation keeps
    the historical behavior: sleep via :meth:`maybe_sleep` when
    :meth:`is_pacing` is true, then send exactly ``n_bytes``.

    ``header_overhead`` is extra framing bytes added on the wire per send
    beyond the size returned by :meth:`next_send` (e.g. TCP record headers).
    UDP datagrams include their header in the send size, so overhead is 0.
    """

    def __init__(
        self, interval_seconds: float = 1.0, header_overhead: int = 0
    ):
        self.interval_seconds = interval_seconds
        self.header_overhead = max(0, int(header_overhead))

    def is_pacing(self) -> bool:
        return False

    def maybe_sleep(self, n_bytes: int):
        return

    def next_send(self, n_bytes: int) -> int:
        """Wait until ready to send, then return how many bytes to send.

        Args:
            n_bytes: Suggested/maximum payload size for this send.

        Returns:
            Number of bytes to send now. Must be ``> 0`` and ``<= n_bytes``.
            Return ``0`` to skip this iteration without sending.
        """
        if self.is_pacing():
            self.maybe_sleep(n_bytes)
        return n_bytes

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
        header_overhead: int = 0,
    ):
        super().__init__(
            interval_seconds=interval_seconds, header_overhead=header_overhead
        )
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
        sleep_time = self.tb.take(n_bytes + self.header_overhead)
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


class TrafficPatternController(BasePacingController):
    """Release bytes according to a :class:`TrafficPattern`.

    Pattern budgets are in *wire* bytes (what the logger counts). When
    ``header_overhead`` is set, each send of ``P`` payload bytes consumes
    ``P + header_overhead`` of that budget so measured rates match the
    pattern bitrate. With overhead 0 (UDP), payload size equals wire size.

    Sends are coalesced up to the ``n_bytes`` passed to :meth:`next_send`
    (or the remaining wire budget after reserving overhead). Large bursts
    are fragmented without delaying remaining bytes from the same event.

    When ``bandwidth_bps`` is set, a :class:`Pacer` additionally caps how
    quickly available bytes may be transmitted (egress-rate limit). The
    pattern still controls *when* bytes become available.

    Event timing uses a monotonic clock and deadline waits: long waits sleep
    for most of the delay, then a short final sleep near the deadline.
    """

    def __init__(
        self,
        pattern: TrafficPattern,
        interval_seconds: float,
        bandwidth_bps: Optional[float] = None,
        header_overhead: int = 0,
    ):
        super().__init__(
            interval_seconds=interval_seconds, header_overhead=header_overhead
        )
        self.pattern = pattern
        self.bandwidth_bps = bandwidth_bps
        self.start_mono_ns: Optional[int] = None
        self.bytes_sent = 0
        self.send_count = 0
        self.tb: Optional[Pacer] = None
        if bandwidth_bps is not None:
            self.tb = Pacer(bandwidth_bps)

    def start(self):
        self.start_mono_ns = time.monotonic_ns()
        self.bytes_sent = 0
        self.send_count = 0

    def _wire_used(self) -> int:
        return self.bytes_sent + self.send_count * self.header_overhead

    def next_send(self, n_bytes: int) -> int:
        if n_bytes <= 0:
            return 0
        assert self.start_mono_ns is not None

        while True:
            elapsed = (time.monotonic_ns() - self.start_mono_ns) / _NS_PER_SECOND
            wire_used = self._wire_used()
            remaining_wire = self.pattern.total_bytes() - wire_used
            if remaining_wire <= 0:
                return 0

            # Reserve framing overhead out of the wire budget when present.
            if self.header_overhead > 0:
                if remaining_wire <= self.header_overhead:
                    return 0
                want = min(n_bytes, remaining_wire - self.header_overhead)
            else:
                want = min(n_bytes, remaining_wire)
            if want <= 0:
                return 0

            need_wire = want + self.header_overhead
            available_wire = self.pattern.cumulative_bytes(elapsed) - wire_used
            if available_wire >= need_wire:
                self._pace(want)
                self.bytes_sent += want
                self.send_count += 1
                return want

            need = need_wire - max(available_wire, 0)
            next_t = self.pattern.next_event_time(elapsed, min_bytes=need)
            if next_t is None:
                # Pattern has no further availability; flush any remaining
                # payload that still fits in the wire budget.
                if self.header_overhead > 0:
                    flush = min(
                        n_bytes,
                        max(0, remaining_wire - self.header_overhead),
                        max(0, available_wire - self.header_overhead),
                    )
                else:
                    flush = min(n_bytes, remaining_wire, max(0, available_wire))
                if flush > 0:
                    self._pace(flush)
                    self.bytes_sent += flush
                    self.send_count += 1
                    return flush
                return 0

            sleep_until_ns(
                self.start_mono_ns + int(next_t * _NS_PER_SECOND)
            )

    def should_stop(self) -> bool:
        return self._wire_used() >= self.pattern.total_bytes()

    def stop_reason(self) -> str:
        return "traffic-pattern"

    def get_update_fields(self) -> Dict[str, float]:
        fields = {
            "traffic_pattern_bytes": float(self.pattern.total_bytes()),
            "traffic_pattern_duration": float(self.pattern.duration()),
        }
        if self.bandwidth_bps is not None:
            fields["target_bandwidth_bps"] = float(self.bandwidth_bps)
        return fields

    def _pace(self, n_bytes: int) -> None:
        if self.tb is None:
            return
        wait = self.tb.take(n_bytes + self.header_overhead)
        if wait > 0:
            sleep_until_ns(time.monotonic_ns() + int(wait * _NS_PER_SECOND))
