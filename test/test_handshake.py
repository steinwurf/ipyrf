import json
import socket
import threading
import time

from ipyrf.handshake import (
    ACTION_CONFIG,
    ACTION_DATA,
    ACTION_REVERSE,
    ACTION_UNKNOWN,
    REVERSE_CONFIG_SIZE,
    TCP_FLAG_CONFIG,
    TCP_FLAG_REVERSE,
    UDP_CONTROL_FLAG,
    UDP_CONTROL_SEQ_CONFIG,
    UDP_CONTROL_SEQ_REVERSE,
    ReverseConfig,
    classify_tcp_flag,
    classify_udp_first,
    pack_reverse_config,
    unpack_reverse_config,
)
from ipyrf.logger import Logger
from ipyrf.tcp import (
    LATENCY_DISABLED,
    LATENCY_ENABLED,
    TCP_HDR,
    TcpReceiver,
    classify_first_record,
    parse_tcp_record,
)
from ipyrf.udp import (
    FIN_FLAG,
    LATENCY_FLAG,
    UDP_HDR,
    classify_first_packet,
    parse_udp_packet,
)


def test_reverse_config_roundtrip():
    config = ReverseConfig(
        duration_seconds=5.0, bandwidth_bps=10e6, payload_len=1200
    )
    packed = pack_reverse_config(config)
    assert len(packed) == REVERSE_CONFIG_SIZE
    got = unpack_reverse_config(packed)
    assert got.duration_seconds == 5.0
    assert got.bandwidth_bps == 10e6
    assert got.payload_len == 1200

    unlimited = unpack_reverse_config(
        pack_reverse_config(ReverseConfig(duration_seconds=1.0))
    )
    assert unlimited.bandwidth_bps is None
    assert unlimited.payload_len == 0


def test_reverse_config_rejects_truncated_and_unknown_version():
    try:
        unpack_reverse_config(b"\x00")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "truncated reverse config" in str(e)

    packed = bytearray(pack_reverse_config(ReverseConfig(duration_seconds=1.0)))
    packed[0] = 99
    try:
        unpack_reverse_config(bytes(packed))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "unsupported reverse config version" in str(e)


def test_tcp_latency_flags_are_data():
    assert classify_tcp_flag(LATENCY_DISABLED) == ACTION_DATA
    assert classify_tcp_flag(LATENCY_ENABLED) == ACTION_DATA
    assert classify_first_record(None) == ACTION_DATA


def test_tcp_reserved_flags_are_reverse_and_config():
    assert classify_tcp_flag(TCP_FLAG_REVERSE) == ACTION_REVERSE
    assert classify_tcp_flag(TCP_FLAG_CONFIG) == ACTION_CONFIG
    assert classify_tcp_flag(99) == ACTION_UNKNOWN


def test_udp_datagrams_without_control_flag_are_data():
    assert classify_udp_first(0, seq=1) == ACTION_DATA
    assert classify_udp_first(LATENCY_FLAG, seq=1) == ACTION_DATA
    assert classify_udp_first(FIN_FLAG, seq=1) == ACTION_DATA
    assert classify_udp_first(FIN_FLAG | LATENCY_FLAG, seq=1) == ACTION_DATA


def test_udp_control_flag_selects_reserved_actions():
    assert (
        classify_udp_first(UDP_CONTROL_FLAG, seq=UDP_CONTROL_SEQ_REVERSE)
        == ACTION_REVERSE
    )
    assert (
        classify_udp_first(UDP_CONTROL_FLAG, seq=UDP_CONTROL_SEQ_CONFIG)
        == ACTION_CONFIG
    )
    assert classify_udp_first(UDP_CONTROL_FLAG, seq=0) == ACTION_UNKNOWN
    assert (
        classify_udp_first(UDP_CONTROL_FLAG | LATENCY_FLAG, seq=99)
        == ACTION_UNKNOWN
    )


def test_udp_parse_then_classify_first_packet():
    payload = bytearray(64)
    UDP_HDR.pack_into(payload, 0, 1, 123, LATENCY_FLAG, 0)
    packet = parse_udp_packet(payload, len(payload), ("127.0.0.1", 5201), 456)
    assert classify_first_packet(packet) == ACTION_DATA
    assert packet.latency_enabled
    assert not packet.is_fin
    assert not packet.is_control

    UDP_HDR.pack_into(
        payload, 0, UDP_CONTROL_SEQ_REVERSE, 0, UDP_CONTROL_FLAG, 0
    )
    control = parse_udp_packet(payload, len(payload), ("127.0.0.1", 5201), 456)
    assert classify_first_packet(control) == ACTION_REVERSE
    assert control.is_control


def _logger(path, mode="tcp") -> Logger:
    return Logger(json_log=True, mode=mode, role="server", logfile=str(path))


def _log_lines(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _wait_for_start(path, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = _log_lines(path)
        if lines and lines[0].get("type") == "start":
            return
        time.sleep(0.05)
    raise TimeoutError(f"server did not log start: {_log_lines(path)}")


def test_tcp_receiver_first_record_is_part_of_the_test(tmp_path):
    client, server = socket.socketpair()
    server.settimeout(1.0)
    log_path = tmp_path / "tcp.json"
    receiver = TcpReceiver(
        server, _logger(log_path), 1.0, "127.0.0.1", 5201, ("127.0.0.1", 1)
    )

    payload = TCP_HDR.pack(LATENCY_ENABLED, 4, 1, 0) + b"abcd"
    client.sendall(payload)
    client.shutdown(socket.SHUT_WR)

    first = receiver.read_first_record()
    assert first is not None
    assert classify_first_record(first) == ACTION_DATA
    leftover, error = parse_tcp_record(receiver.recv_buffer)
    assert leftover is None and error is None

    receiver.begin()
    receiver.run(first=first)
    receiver.summarize()
    client.close()
    server.close()

    assert receiver.stop_reason == "end-of-test"
    assert receiver.bytes_recv == len(payload)
    assert receiver.latency.enabled


def test_tcp_server_rejects_truncated_reverse_config(tmp_path, free_port):
    from ipyrf import tcp

    log_path = tmp_path / "server.json"
    log = _logger(log_path)
    errors = []

    def run_server():
        try:
            tcp.server(log, "127.0.0.1", free_port, 1.0, None)
        except Exception as e:
            errors.append(e)

    thread = threading.Thread(target=run_server)
    thread.start()
    try:
        _wait_for_start(log_path)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", free_port))
        client.sendall(TCP_HDR.pack(TCP_FLAG_REVERSE, 0, 0, 0))
        client.close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert errors == []
    finally:
        if thread.is_alive():
            thread.join(timeout=1)

    summary = next(obj for obj in _log_lines(log_path) if obj["type"] == "summary")
    assert "truncated reverse config" in summary["stop_reason"]


def test_udp_server_rejects_invalid_reverse_config(tmp_path, free_port):
    from ipyrf import udp

    log_path = tmp_path / "udp.json"
    log = _logger(log_path, mode="udp")
    errors = []

    def run_server():
        try:
            udp.server(log, "127.0.0.1", free_port, 1.0)
        except Exception as e:
            errors.append(e)

    thread = threading.Thread(target=run_server)
    thread.start()
    try:
        _wait_for_start(log_path)
        payload = bytearray(64)
        UDP_HDR.pack_into(
            payload, 0, UDP_CONTROL_SEQ_REVERSE, 0, UDP_CONTROL_FLAG, 0
        )
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.sendto(payload, ("127.0.0.1", free_port))
        client.close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert errors == []
    finally:
        if thread.is_alive():
            thread.join(timeout=1)

    summary = next(obj for obj in _log_lines(log_path) if obj["type"] == "summary")
    assert "unsupported reverse config version" in summary["stop_reason"]
    assert summary["bytes"] == 0
    assert summary["packets"] == 0
