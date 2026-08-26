import time

from ipyrf.tcp import (
    DEFAULT_TCP_PAYLOAD,
    LATENCY_DISABLED,
    LATENCY_ENABLED,
    MAX_TCP_PAYLOAD,
    TCP_HDR,
    TCP_HDR_SIZE,
)


def _build_record(
    flag: int, payload_len: int, timestamp_ns: int = 0, event_id: int = 0
) -> bytes:
    return TCP_HDR.pack(flag, payload_len, timestamp_ns, event_id) + (
        b"\x00" * payload_len
    )


def test_tcp_header_includes_payload_length():
    packed = TCP_HDR.pack(LATENCY_ENABLED, 500, 123456789, 0)
    flag, payload_len, timestamp_ns, event_id = TCP_HDR.unpack(packed)
    assert flag == LATENCY_ENABLED
    assert payload_len == 500
    assert timestamp_ns == 123456789
    assert event_id == 0
    assert TCP_HDR_SIZE == 1 + 4 + 8 + 4


def test_tcp_header_includes_event_id():
    packed = TCP_HDR.pack(LATENCY_DISABLED, 64, 42, 7)
    flag, payload_len, timestamp_ns, event_id = TCP_HDR.unpack(packed)
    assert flag == LATENCY_DISABLED
    assert payload_len == 64
    assert timestamp_ns == 42
    assert event_id == 7


def test_tcp_framing_parses_variable_payload_sizes():
    records = [
        _build_record(LATENCY_ENABLED, 100, 1_000, 1),
        _build_record(LATENCY_DISABLED, DEFAULT_TCP_PAYLOAD, 2_000, 0),
        _build_record(LATENCY_ENABLED, 64, 3_000, 2),
    ]
    recv_buffer = bytearray(b"".join(records))

    parsed = []
    while len(recv_buffer) >= TCP_HDR_SIZE:
        flag, payload_len, timestamp_ns, event_id = TCP_HDR.unpack_from(recv_buffer)
        assert payload_len <= MAX_TCP_PAYLOAD
        record_size = TCP_HDR_SIZE + payload_len
        assert len(recv_buffer) >= record_size
        parsed.append((flag, payload_len, timestamp_ns, event_id))
        del recv_buffer[:record_size]

    assert recv_buffer == b""
    assert parsed == [
        (LATENCY_ENABLED, 100, 1_000, 1),
        (LATENCY_DISABLED, DEFAULT_TCP_PAYLOAD, 2_000, 0),
        (LATENCY_ENABLED, 64, 3_000, 2),
    ]


def test_tcp_framing_waits_for_complete_record():
    partial = TCP_HDR.pack(LATENCY_ENABLED, 200, time.time_ns(), 3) + b"\x00" * 50
    recv_buffer = bytearray(partial)

    flag, payload_len, timestamp_ns, event_id = TCP_HDR.unpack_from(recv_buffer)
    record_size = TCP_HDR_SIZE + payload_len
    assert len(recv_buffer) < record_size
    assert payload_len == 200
    assert flag == LATENCY_ENABLED
    assert timestamp_ns > 0
    assert event_id == 3
