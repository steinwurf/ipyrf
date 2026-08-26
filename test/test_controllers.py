import time

import pytest

from ipyrf.controllers import (
    BasePacingController,
    StaticPacingController,
    TrafficPatternController,
)
from ipyrf.traffic_pattern import PiecewiseRateTrafficPattern, TraceTrafficPattern


def test_base_next_send_returns_requested_bytes():
    controller = BasePacingController()
    assert controller.next_send(1200) == 1200


def test_static_next_send_preserves_size_without_pacing():
    controller = StaticPacingController(None, duration_seconds=1.0, interval_seconds=1.0)
    assert not controller.is_pacing()
    assert controller.next_send(1200) == 1200


def test_static_next_send_preserves_size_with_pacing():
    controller = StaticPacingController(
        50e6, duration_seconds=1.0, interval_seconds=1.0
    )
    assert controller.is_pacing()
    assert controller.next_send(1200) == 1200


def test_custom_controller_can_choose_size_and_skip():
    class CustomController(BasePacingController):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def next_send(self, n_bytes: int) -> int:
            self.calls += 1
            if self.calls == 1:
                return 0
            return min(100, n_bytes)

    controller = CustomController()
    assert controller.next_send(1200) == 0
    assert controller.next_send(1200) == 100


def test_traffic_pattern_coalesces_to_requested_size():
    # Continuous low rate used to emit tiny sends as soon as any byte
    # became available, inflating TCP wire rate via per-record headers.
    pattern = PiecewiseRateTrafficPattern([(1.0, 2e6)])
    controller = TrafficPatternController(pattern, interval_seconds=1.0)
    controller.start()

    sizes = []
    while not controller.should_stop():
        n = controller.next_send(1200)
        if n <= 0:
            break
        sizes.append(n)

    assert sizes
    assert all(n == 1200 for n in sizes[:-1])
    assert sum(sizes) == pattern.total_bytes()
    remainder = pattern.total_bytes() % 1200
    assert sizes[-1] == (remainder if remainder else 1200)


def test_traffic_pattern_sends_small_trace_event_in_one_chunk():
    pattern = TraceTrafficPattern([(0.0, 100)])
    controller = TrafficPatternController(pattern, interval_seconds=1.0)
    controller.start()
    assert controller.next_send(1200) == 100
    assert controller.should_stop()


def test_traffic_pattern_header_overhead_reserves_wire_budget():
    # 1200 payload + 17 header per send against a 1217-byte wire budget.
    pattern = TraceTrafficPattern([(0.0, 1217)])
    controller = TrafficPatternController(
        pattern, interval_seconds=1.0, header_overhead=17
    )
    controller.start()
    assert controller.next_send(1200) == 1200
    assert controller.current_event_id() == 1
    assert controller.should_stop()
    assert controller.bytes_sent == 1200
    assert controller._wire_used() == 1217


def test_traffic_pattern_does_not_cross_event_boundary():
    pattern = TraceTrafficPattern([(0.0, 500), (0.0, 700)])
    controller = TrafficPatternController(pattern, interval_seconds=1.0)
    controller.start()
    assert controller.next_send(1200) == 500
    assert controller.current_event_id() == 1
    assert controller.next_send(1200) == 700
    assert controller.current_event_id() == 2
    assert controller.should_stop()


def test_traffic_pattern_piecewise_event_ids():
    # Two 1-second periods at 9600 bit/s => 1200 bytes each.
    pattern = PiecewiseRateTrafficPattern([(1.0, 9600), (1.0, 9600)])
    controller = TrafficPatternController(pattern, interval_seconds=1.0)
    controller.start()
    assert controller.next_send(1200) == 1200
    assert controller.current_event_id() == 1
    assert controller.next_send(1200) == 1200
    assert controller.current_event_id() == 2
    assert controller.should_stop()


def test_static_pacing_charges_header_overhead(monkeypatch):
    taken = []

    class FakePacer:
        def __init__(self, _rate):
            pass

        def take(self, n_bytes):
            taken.append(n_bytes)
            return 0.0

    monkeypatch.setattr("ipyrf.controllers.Pacer", FakePacer)
    controller = StaticPacingController(
        10e6, duration_seconds=1.0, interval_seconds=1.0, header_overhead=17
    )
    assert controller.next_send(1200) == 1200
    assert controller.current_event_id() == 0
    assert taken == [1217]
