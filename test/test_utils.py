import threading
import time

from ipyrf.utils import sleep_precise, sleep_until_ns


def test_sleep_until_ns_past_deadline_returns_immediately():
    started = time.perf_counter()
    sleep_until_ns(time.monotonic_ns() - 1_000_000)
    assert time.perf_counter() - started < 0.05


def test_sleep_until_ns_short_deadline_is_accurate():
    deadline = time.monotonic_ns() + 20_000_000  # 20ms
    sleep_until_ns(deadline)
    overshoot_ms = (time.monotonic_ns() - deadline) / 1e6
    assert -2.0 <= overshoot_ms < 15.0


def test_sleep_precise_can_be_interrupted():
    stop = threading.Event()
    stop.set()
    started = time.perf_counter()
    sleep_precise(0.2, stop)
    assert time.perf_counter() - started < 0.05
