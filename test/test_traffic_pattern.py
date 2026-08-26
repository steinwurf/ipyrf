import json
from pathlib import Path

import pytest

from ipyrf.traffic_pattern import (
    PiecewiseRateTrafficPattern,
    TrafficPatternError,
    TraceTrafficPattern,
    load_traffic_pattern,
    parse_traffic_pattern,
)


def test_empty_trace():
    pattern = TraceTrafficPattern([])
    assert pattern.cumulative_bytes(0.0) == 0
    assert pattern.cumulative_bytes(10.0) == 0
    assert pattern.duration() == 0.0
    assert pattern.total_bytes() == 0
    assert pattern.next_event_time(0.0) is None


def test_single_event_at_zero():
    pattern = TraceTrafficPattern([(0.0, 100)])
    assert pattern.cumulative_bytes(-0.1) == 0
    assert pattern.cumulative_bytes(0.0) == 100
    assert pattern.cumulative_bytes(1.0) == 100
    assert pattern.duration() == 0.0
    assert pattern.total_bytes() == 100
    assert pattern.next_event_time(0.0) is None


def test_multiple_events_accumulate():
    pattern = TraceTrafficPattern(
        [
            (0.0, 100),
            (0.5, 200),
            (1.0, 50),
        ]
    )
    assert pattern.cumulative_bytes(0.0) == 100
    assert pattern.cumulative_bytes(0.49) == 100
    assert pattern.cumulative_bytes(0.5) == 300
    assert pattern.cumulative_bytes(0.99) == 300
    assert pattern.cumulative_bytes(1.0) == 350
    assert pattern.cumulative_bytes(2.0) == 350
    assert pattern.duration() == 1.0
    assert pattern.total_bytes() == 350
    assert pattern.next_event_time(0.0) == 0.5
    assert pattern.next_event_time(0.5) == 1.0
    assert pattern.next_event_time(1.0) is None


def test_same_timestamp_events():
    pattern = TraceTrafficPattern(
        [
            (1.0, 10),
            (1.0, 20),
        ]
    )
    assert pattern.cumulative_bytes(0.9) == 0
    assert pattern.cumulative_bytes(1.0) == 30
    assert pattern.next_event_time(0.9) == 1.0
    assert pattern.next_event_time(1.0) is None


def test_next_event_time_skips_zero_byte_events():
    pattern = TraceTrafficPattern([(0.5, 0), (1.0, 10)])
    assert pattern.next_event_time(0.0) == 1.0


def test_rejects_negative_timestamp():
    with pytest.raises(TrafficPatternError, match="timestamp"):
        TraceTrafficPattern([(-0.1, 10)])


def test_rejects_negative_nbytes():
    with pytest.raises(TrafficPatternError, match="nbytes"):
        TraceTrafficPattern([(0.0, -1)])


def test_rejects_decreasing_timestamps():
    with pytest.raises(TrafficPatternError, match="non-decreasing"):
        TraceTrafficPattern([(1.0, 10), (0.5, 10)])


def _valid_doc(**overrides):
    doc = {
        "version": 1,
        "type": "trace",
        "events": [
            {"timestamp": 0.0, "nbytes": 100},
            {"timestamp": 0.25, "nbytes": 200, "tags": ["burst"]},
        ],
        "metadata": {"name": "example"},
    }
    doc.update(overrides)
    return doc


def test_parse_valid_trace_document():
    pattern = parse_traffic_pattern(_valid_doc())
    assert pattern.total_bytes() == 300
    assert pattern.duration() == 0.25
    assert pattern.metadata == {"name": "example"}
    assert pattern.cumulative_bytes(0.25) == 300


def test_load_traffic_pattern_from_file(tmp_path: Path):
    path = tmp_path / "pattern.json"
    path.write_text(json.dumps(_valid_doc()), encoding="utf-8")
    pattern = load_traffic_pattern(path)
    assert pattern.total_bytes() == 300


def test_load_rejects_invalid_json(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(TrafficPatternError, match="invalid JSON"):
        load_traffic_pattern(path)


@pytest.mark.parametrize(
    "doc, match",
    [
        (["not", "an", "object"], "top level must be a JSON object"),
        ({"type": "trace", "events": []}, "missing required field 'version'"),
        (
            {"version": "1", "type": "trace", "events": []},
            "version.*must be an integer",
        ),
        (
            {"version": 2, "type": "trace", "events": []},
            "unsupported version 2",
        ),
        (
            {"version": 1, "events": []},
            "missing required field 'type'",
        ),
        (
            {"version": 1, "type": "video", "events": []},
            "unsupported type 'video'",
        ),
        (
            {"version": 1, "type": "piecewise_rate"},
            "missing required field 'periods'",
        ),
        (
            {"version": 1, "type": "piecewise_rate", "periods": {}},
            "'periods' must be a JSON array",
        ),
        (
            {
                "version": 1,
                "type": "piecewise_rate",
                "periods": [{"bitrate": 1}],
            },
            "missing required field 'duration'",
        ),
        (
            {
                "version": 1,
                "type": "piecewise_rate",
                "periods": [{"duration": 1.0}],
            },
            "missing required field 'bitrate'",
        ),
        (
            {
                "version": 1,
                "type": "piecewise_rate",
                "periods": [{"duration": 0, "bitrate": 1}],
            },
            "duration.*> 0",
        ),
        (
            {
                "version": 1,
                "type": "piecewise_rate",
                "periods": [{"duration": 1.0, "bitrate": -1}],
            },
            "bitrate.*>= 0",
        ),
        (
            {"version": 1, "type": "trace"},
            "missing required field 'events'",
        ),
        (
            {"version": 1, "type": "trace", "events": {}},
            "'events' must be a JSON array",
        ),
        (
            {
                "version": 1,
                "type": "trace",
                "events": [],
                "metadata": ["nope"],
            },
            "'metadata' must be a JSON object",
        ),
        (
            {
                "version": 1,
                "type": "trace",
                "events": [{"nbytes": 1}],
            },
            "missing required field 'timestamp'",
        ),
        (
            {
                "version": 1,
                "type": "trace",
                "events": [{"timestamp": 0.0}],
            },
            "missing required field 'nbytes'",
        ),
        (
            {
                "version": 1,
                "type": "trace",
                "events": [{"timestamp": -1, "nbytes": 1}],
            },
            "timestamp.*>= 0",
        ),
        (
            {
                "version": 1,
                "type": "trace",
                "events": [{"timestamp": 0.0, "nbytes": -1}],
            },
            "nbytes.*>= 0",
        ),
        (
            {
                "version": 1,
                "type": "trace",
                "events": [{"timestamp": 0.0, "nbytes": 1.5}],
            },
            "nbytes.*must be an integer",
        ),
        (
            {
                "version": 1,
                "type": "trace",
                "events": [
                    {"timestamp": 1.0, "nbytes": 1},
                    {"timestamp": 0.5, "nbytes": 1},
                ],
            },
            "non-decreasing",
        ),
        (
            {
                "version": 1,
                "type": "trace",
                "events": [
                    {"timestamp": 0.0, "nbytes": 1, "tags": "burst"},
                ],
            },
            "'tags' must be a JSON array",
        ),
        (
            {
                "version": 1,
                "type": "trace",
                "events": [
                    {"timestamp": 0.0, "nbytes": 1, "tags": [1]},
                ],
            },
            "tags\\[0\\] must be a string",
        ),
    ],
)
def test_parse_rejects_malformed_documents(doc, match):
    with pytest.raises(TrafficPatternError, match=match):
        parse_traffic_pattern(doc)


def test_empty_piecewise_rate():
    pattern = PiecewiseRateTrafficPattern([])
    assert pattern.cumulative_bytes(0.0) == 0
    assert pattern.cumulative_bytes(10.0) == 0
    assert pattern.duration() == 0.0
    assert pattern.total_bytes() == 0
    assert pattern.next_event_time(0.0) is None


def test_piecewise_rate_example_periods():
    # 2 s @ 2 Mbps, 1 s idle, 3 s @ 10 Mbps
    pattern = PiecewiseRateTrafficPattern(
        [
            (2.0, 2e6),
            (1.0, 0.0),
            (3.0, 10e6),
        ]
    )
    # 2e6 bit/s * 2 s / 8 = 500_000 bytes; 10e6 * 3 / 8 = 3_750_000
    assert pattern.total_bytes() == 500_000 + 3_750_000
    assert pattern.duration() == 6.0

    assert pattern.cumulative_bytes(0.0) == 0
    assert pattern.cumulative_bytes(1.0) == 250_000
    assert pattern.cumulative_bytes(2.0) == 500_000
    assert pattern.cumulative_bytes(2.5) == 500_000  # idle
    assert pattern.cumulative_bytes(3.0) == 500_000
    assert pattern.cumulative_bytes(4.0) == 500_000 + 1_250_000
    assert pattern.cumulative_bytes(6.0) == pattern.total_bytes()
    assert pattern.cumulative_bytes(10.0) == pattern.total_bytes()


def test_piecewise_rate_next_event_skips_idle():
    pattern = PiecewiseRateTrafficPattern(
        [
            (2.0, 2e6),
            (1.0, 0.0),
            (3.0, 10e6),
        ]
    )
    # During idle, next bytes arrive at the start of the 10 Mbps period.
    assert pattern.next_event_time(2.0) == pytest.approx(3.0)
    assert pattern.next_event_time(2.5) == pytest.approx(3.0)
    # After the pattern ends, nothing remains.
    assert pattern.next_event_time(6.0) is None


def test_piecewise_rate_next_event_waits_for_min_bytes():
    # 8 Mbps => 1 byte/us
    pattern = PiecewiseRateTrafficPattern([(1.0, 8e6)])
    assert pattern.cumulative_bytes(0.0) == 0
    t = pattern.next_event_time(0.0, min_bytes=1000)
    assert t == pytest.approx(1000 / 1e6)
    assert pattern.cumulative_bytes(t) == 1000


def test_piecewise_rate_rejects_non_positive_duration():
    with pytest.raises(TrafficPatternError, match="duration"):
        PiecewiseRateTrafficPattern([(0.0, 1e6)])


def test_piecewise_rate_rejects_negative_bitrate():
    with pytest.raises(TrafficPatternError, match="bitrate"):
        PiecewiseRateTrafficPattern([(1.0, -1.0)])


def _valid_piecewise_doc(**overrides):
    doc = {
        "version": 1,
        "type": "piecewise_rate",
        "periods": [
            {"duration": 2.0, "bitrate": "2M"},
            {"duration": 1.0, "bitrate": 0},
            {"duration": 3.0, "bitrate": 10e6, "tags": ["burst"]},
        ],
        "metadata": {"name": "rates"},
    }
    doc.update(overrides)
    return doc


def test_parse_valid_piecewise_rate_document():
    pattern = parse_traffic_pattern(_valid_piecewise_doc())
    assert isinstance(pattern, PiecewiseRateTrafficPattern)
    assert pattern.total_bytes() == 500_000 + 3_750_000
    assert pattern.duration() == 6.0
    assert pattern.metadata == {"name": "rates"}
    assert pattern.cumulative_bytes(2.0) == 500_000


def test_load_piecewise_rate_from_file(tmp_path: Path):
    path = tmp_path / "rates.json"
    path.write_text(json.dumps(_valid_piecewise_doc()), encoding="utf-8")
    pattern = load_traffic_pattern(path)
    assert pattern.total_bytes() == 500_000 + 3_750_000
