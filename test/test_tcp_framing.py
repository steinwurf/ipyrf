import time

from ipyrf.tcp import (
    DEFAULT_TCP_PAYLOAD,
    LATENCY_DISABLED,
    LATENCY_ENABLED,
    MAX_TCP_PAYLOAD,
    TCP_HDR,
    TCP_HDR_SIZE,
    TcpSender,
    parse_tcp_record,
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
    while True:
        record, error = parse_tcp_record(recv_buffer)
        assert error is None
        if record is None:
            break
        parsed.append(
            (
                record.latency_flag,
                record.payload_len,
                record.timestamp_ns,
                record.event_id,
            )
        )

    assert recv_buffer == b""
    assert parsed == [
        (LATENCY_ENABLED, 100, 1_000, 1),
        (LATENCY_DISABLED, DEFAULT_TCP_PAYLOAD, 2_000, 0),
        (LATENCY_ENABLED, 64, 3_000, 2),
    ]


def test_tcp_framing_waits_for_complete_record():
    partial = TCP_HDR.pack(LATENCY_ENABLED, 200, time.time_ns(), 3) + b"\x00" * 50
    recv_buffer = bytearray(partial)

    record, error = parse_tcp_record(recv_buffer)
    assert record is None
    assert error is None
    assert len(recv_buffer) == len(partial)

    flag, payload_len, timestamp_ns, event_id = TCP_HDR.unpack_from(recv_buffer)
    record_size = TCP_HDR_SIZE + payload_len
    assert len(recv_buffer) < record_size
    assert payload_len == 200
    assert flag == LATENCY_ENABLED
    assert timestamp_ns > 0
    assert event_id == 3


def test_tcp_framing_rejects_oversized_payload():
    recv_buffer = bytearray(
        TCP_HDR.pack(LATENCY_DISABLED, MAX_TCP_PAYLOAD + 1, 0, 0)
    )
    record, error = parse_tcp_record(recv_buffer)
    assert record is None
    assert error == f"invalid payload length: {MAX_TCP_PAYLOAD + 1}"
    assert len(recv_buffer) == TCP_HDR_SIZE


class _RecordingSocket:
    """Accepts at most ``chunk`` bytes per send() and keeps every call."""

    def __init__(self, chunk=None):
        self.chunk = chunk
        self.sends = []

    def send(self, data):
        data = bytes(data[: self.chunk] if self.chunk else data)
        self.sends.append(data)
        return len(data)


def _sender(sock):
    return TcpSender(
        sock, log=None, controller=None, enable_latency=True, host="", port=0
    )


def test_tcp_sender_sends_each_record_in_one_call():
    sock = _RecordingSocket()
    sender = _sender(sock)
    assert sender.send_record(DEFAULT_TCP_PAYLOAD, LATENCY_ENABLED, 11, 1)
    assert sender.send_record(0, LATENCY_DISABLED, 22, 2)
    assert sender.send_record(MAX_TCP_PAYLOAD, LATENCY_ENABLED, 33, 3)

    assert [len(d) for d in sock.sends] == [
        TCP_HDR_SIZE + DEFAULT_TCP_PAYLOAD,
        TCP_HDR_SIZE,
        TCP_HDR_SIZE + MAX_TCP_PAYLOAD,
    ]
    assert sender.bytes_sent == sum(len(d) for d in sock.sends)

    recv_buffer = bytearray(b"".join(sock.sends))
    parsed = []
    while True:
        record, error = parse_tcp_record(recv_buffer)
        assert error is None
        if record is None:
            break
        parsed.append(
            (
                record.latency_flag,
                record.payload_len,
                record.timestamp_ns,
                record.event_id,
            )
        )
    assert recv_buffer == b""
    assert parsed == [
        (LATENCY_ENABLED, DEFAULT_TCP_PAYLOAD, 11, 1),
        (LATENCY_DISABLED, 0, 22, 2),
        (LATENCY_ENABLED, MAX_TCP_PAYLOAD, 33, 3),
    ]


def test_tcp_sender_finishes_a_record_after_a_short_send():
    sock = _RecordingSocket(chunk=500)
    sender = _sender(sock)
    assert sender.send_record(DEFAULT_TCP_PAYLOAD, LATENCY_ENABLED, 44, 4)

    data = b"".join(sock.sends)
    assert len(data) == TCP_HDR_SIZE + DEFAULT_TCP_PAYLOAD
    assert TCP_HDR.unpack_from(data) == (LATENCY_ENABLED, DEFAULT_TCP_PAYLOAD, 44, 4)
    assert data[TCP_HDR_SIZE:] == b"\x00" * DEFAULT_TCP_PAYLOAD
