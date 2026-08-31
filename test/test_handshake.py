import json
import socket
import subprocess
import sys
import threading
import time

from ipyrf.handshake import (
    ACTION_CONFIG,
    ACTION_DATA,
    ACTION_ERROR,
    ACTION_REVERSE,
    ACTION_UNKNOWN,
    MAX_TRACE_FILE_NAME_LEN,
    REVERSE_CONFIG_SIZE,
    TCP_FLAG_CONFIG,
    TCP_FLAG_ERROR,
    TCP_FLAG_REVERSE,
    UDP_CONTROL_FLAG,
    UDP_CONTROL_SEQ_CONFIG,
    UDP_CONTROL_SEQ_ERROR,
    UDP_CONTROL_SEQ_REVERSE,
    ReverseConfig,
    classify_tcp_flag,
    classify_udp_first,
    pack_error_message,
    pack_reverse_config,
    resolve_trace_file,
    reverse_trace_file_name,
    unpack_error_message,
    unpack_reverse_config,
    validate_trace_file_name,
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
    assert got.traffic_pattern_name is None
    assert got.loops == 1

    unlimited = unpack_reverse_config(
        pack_reverse_config(ReverseConfig(duration_seconds=1.0))
    )
    assert unlimited.bandwidth_bps is None
    assert unlimited.payload_len == 0
    assert unlimited.traffic_pattern_name is None


def test_reverse_config_roundtrip_with_trace_name():
    config = ReverseConfig(
        duration_seconds=0.0,
        bandwidth_bps=50e6,
        payload_len=1200,
        traffic_pattern_name="video.json",
        loops=3,
    )
    packed = pack_reverse_config(config)
    assert packed == pack_reverse_config(config)
    assert len(packed) == REVERSE_CONFIG_SIZE + len("video.json")
    got = unpack_reverse_config(packed)
    assert got.traffic_pattern_name == "video.json"
    assert got.loops == 3
    assert got.bandwidth_bps == 50e6
    assert got.payload_len == 1200
    extra = packed + b"\x00ignored"
    assert unpack_reverse_config(extra).traffic_pattern_name == "video.json"


def test_reverse_config_rejects_path_as_trace_name():
    try:
        pack_reverse_config(
            ReverseConfig(
                duration_seconds=0.0, traffic_pattern_name="dir/trace.json"
            )
        )
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not a path" in str(e)

    packed = pack_reverse_config(
        ReverseConfig(duration_seconds=0.0, traffic_pattern_name="ok.json")
    )
    # Rewrite the name bytes to include a slash while keeping name_len.
    tampered = packed[:REVERSE_CONFIG_SIZE] + b"../x.json"
    try:
        unpack_reverse_config(tampered)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not a path" in str(e) or "too long" in str(e)


def test_reverse_config_trace_name_fits_one_packet():
    from ipyrf.udp import UDP_HDR

    name = "a" * MAX_TRACE_FILE_NAME_LEN
    packed = pack_reverse_config(
        ReverseConfig(duration_seconds=0.0, traffic_pattern_name=name)
    )
    assert unpack_reverse_config(packed).traffic_pattern_name == name
    assert UDP_HDR.size + len(packed) < 1200


def test_reverse_trace_file_name_strips_directories():
    assert reverse_trace_file_name("/tmp/traces/video.json") == "video.json"
    assert reverse_trace_file_name("video.json") == "video.json"


def test_validate_trace_file_name_rejects_invalid():
    for name in ("", ".", "..", "a/b", "a\\b", "x\x00y"):
        try:
            validate_trace_file_name(name)
            assert False, f"expected ValueError for {name!r}"
        except ValueError:
            pass


def test_resolve_trace_file_from_cwd_and_trace_dir(tmp_path, monkeypatch):
    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert resolve_trace_file("cwd.json") == cwd_file

    traces = tmp_path / "traces"
    traces.mkdir()
    named = traces / "named.json"
    named.write_text("{}", encoding="utf-8")
    assert resolve_trace_file("named.json", traces) == named

    try:
        resolve_trace_file("missing.json", traces)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not found" in str(e)


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


def test_error_message_roundtrip():
    packed = pack_error_message("trace file not found: missing.json")
    assert unpack_error_message(packed) == "trace file not found: missing.json"
    assert unpack_error_message(b"") == "reverse error"


def test_tcp_latency_flags_are_data():
    assert classify_tcp_flag(LATENCY_DISABLED) == ACTION_DATA
    assert classify_tcp_flag(LATENCY_ENABLED) == ACTION_DATA
    assert classify_first_record(None) == ACTION_DATA


def test_tcp_reserved_flags_are_reverse_config_and_error():
    assert classify_tcp_flag(TCP_FLAG_REVERSE) == ACTION_REVERSE
    assert classify_tcp_flag(TCP_FLAG_CONFIG) == ACTION_CONFIG
    assert classify_tcp_flag(TCP_FLAG_ERROR) == ACTION_ERROR
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
    assert (
        classify_udp_first(UDP_CONTROL_FLAG, seq=UDP_CONTROL_SEQ_ERROR)
        == ACTION_ERROR
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


def test_reverse_config_rejects_truncated_name():
    packed = pack_reverse_config(
        ReverseConfig(duration_seconds=0.0, traffic_pattern_name="abcd.json")
    )
    try:
        unpack_reverse_config(packed[:-1])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "truncated reverse config name" in str(e)


def test_tcp_server_missing_reverse_trace(tmp_path, free_port):
    from ipyrf import tcp

    log_path = tmp_path / "server.json"
    log = _logger(log_path)
    errors = []

    def run_server():
        try:
            tcp.server(
                log, "127.0.0.1", free_port, 1.0, None, trace_dir=str(tmp_path)
            )
        except Exception as e:
            errors.append(e)

    thread = threading.Thread(target=run_server)
    thread.start()
    try:
        _wait_for_start(log_path)
        packed = pack_reverse_config(
            ReverseConfig(
                duration_seconds=0.0, traffic_pattern_name="missing.json"
            )
        )
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", free_port))
        client.sendall(TCP_HDR.pack(TCP_FLAG_REVERSE, len(packed), 0, 0) + packed)
        client.settimeout(2.0)
        reply = bytearray()
        while len(reply) < TCP_HDR.size:
            chunk = client.recv(4096)
            assert chunk, "server closed without sending an error"
            reply.extend(chunk)
        latency_flag, payload_len, _, _ = TCP_HDR.unpack_from(reply)
        while len(reply) < TCP_HDR.size + payload_len:
            chunk = client.recv(4096)
            assert chunk, "server closed before error payload"
            reply.extend(chunk)
        assert latency_flag == TCP_FLAG_ERROR
        message = unpack_error_message(
            bytes(reply[TCP_HDR.size : TCP_HDR.size + payload_len])
        )
        assert "trace file not found" in message
        assert "missing.json" in message
        client.close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert errors == []
    finally:
        if thread.is_alive():
            thread.join(timeout=1)

    summary = next(obj for obj in _log_lines(log_path) if obj["type"] == "summary")
    assert "trace file not found" in summary["stop_reason"]
    assert "missing.json" in summary["stop_reason"]


_REVERSE_TRACE = {
    "version": 1,
    "type": "trace",
    "events": [
        {"timestamp": 0.0, "nbytes": 4000},
        {"timestamp": 0.2, "nbytes": 4000},
        {"timestamp": 0.4, "nbytes": 4000},
    ],
}


def _popen_ipyrf(args, logfile):
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ipyrf",
            *args,
            "--json_log",
            "--logfile",
            str(logfile),
        ]
    )


def _stop_process(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def test_tcp_reverse_trace_dir_uses_file_name_only(tmp_path, free_port):
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "rev.json").write_text(json.dumps(_REVERSE_TRACE), encoding="utf-8")
    server_log = tmp_path / "server.json"
    client_log = tmp_path / "client.json"

    server = _popen_ipyrf(
        [
            "tcp",
            "server",
            "127.0.0.1",
            "--port",
            str(free_port),
            "--trace-dir",
            str(traces),
        ],
        server_log,
    )
    try:
        _wait_for_start(server_log)
        client = subprocess.run(
            [
                sys.executable,
                "-m",
                "ipyrf",
                "tcp",
                "client",
                "127.0.0.1",
                "--port",
                str(free_port),
                "--traffic-pattern",
                "/not/a/real/dir/rev.json",
                "--reverse",
                "--json_log",
                "--logfile",
                str(client_log),
            ],
            check=True,
            timeout=10,
        )
        assert client.returncode == 0
        server.wait(timeout=10)
    finally:
        _stop_process(server)

    server_summary = next(
        obj for obj in _log_lines(server_log) if obj["type"] == "summary"
    )
    client_summary = next(
        obj for obj in _log_lines(client_log) if obj["type"] == "summary"
    )
    assert server_summary["direction"] == "tx"
    assert server_summary["stop_reason"] == "traffic-pattern"
    assert server_summary["bytes"] > 0
    assert client_summary["direction"] == "rx"
    assert client_summary["bytes"] > 0


def test_tcp_reverse_trace_from_working_directory(tmp_path, free_port):
    (tmp_path / "cwd.json").write_text(json.dumps(_REVERSE_TRACE), encoding="utf-8")
    server_log = tmp_path / "server.json"
    client_log = tmp_path / "client.json"

    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ipyrf",
            "tcp",
            "server",
            "127.0.0.1",
            "--port",
            str(free_port),
            "--json_log",
            "--logfile",
            str(server_log),
        ],
        cwd=str(tmp_path),
    )
    try:
        _wait_for_start(server_log)
        client = subprocess.run(
            [
                sys.executable,
                "-m",
                "ipyrf",
                "tcp",
                "client",
                "127.0.0.1",
                "--port",
                str(free_port),
                "--traffic-pattern",
                "cwd.json",
                "--reverse",
                "--json_log",
                "--logfile",
                str(client_log),
            ],
            check=True,
            timeout=10,
        )
        assert client.returncode == 0
        server.wait(timeout=10)
    finally:
        _stop_process(server)

    server_summary = next(
        obj for obj in _log_lines(server_log) if obj["type"] == "summary"
    )
    assert server_summary["stop_reason"] == "traffic-pattern"
    assert server_summary["bytes"] > 0


def test_udp_reverse_trace_dir(tmp_path, free_port):
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "rev.json").write_text(json.dumps(_REVERSE_TRACE), encoding="utf-8")
    server_log = tmp_path / "server.json"
    client_log = tmp_path / "client.json"

    server = _popen_ipyrf(
        [
            "udp",
            "server",
            "127.0.0.1",
            "--port",
            str(free_port),
            "--trace-dir",
            str(traces),
        ],
        server_log,
    )
    try:
        _wait_for_start(server_log)
        client = subprocess.run(
            [
                sys.executable,
                "-m",
                "ipyrf",
                "udp",
                "client",
                "127.0.0.1",
                "--port",
                str(free_port),
                "--traffic-pattern",
                str(traces / "rev.json"),
                "--reverse",
                "--json_log",
                "--logfile",
                str(client_log),
            ],
            check=True,
            timeout=10,
        )
        assert client.returncode == 0
        server.wait(timeout=10)
    finally:
        _stop_process(server)

    server_summary = next(
        obj for obj in _log_lines(server_log) if obj["type"] == "summary"
    )
    client_summary = next(
        obj for obj in _log_lines(client_log) if obj["type"] == "summary"
    )
    assert server_summary["direction"] == "tx"
    assert server_summary["stop_reason"] == "traffic-pattern"
    assert server_summary["bytes"] > 0
    assert client_summary["direction"] == "rx"
    assert client_summary["bytes"] > 0


def _run_reverse_missing_trace(tmp_path, free_port, protocol):
    traces = tmp_path / "traces"
    traces.mkdir()
    server_log = tmp_path / "server.json"
    client_log = tmp_path / "client.json"
    server = _popen_ipyrf(
        [
            protocol,
            "server",
            "127.0.0.1",
            "--port",
            str(free_port),
            "--trace-dir",
            str(traces),
        ],
        server_log,
    )
    try:
        _wait_for_start(server_log)
        client = subprocess.run(
            [
                sys.executable,
                "-m",
                "ipyrf",
                protocol,
                "client",
                "127.0.0.1",
                "--port",
                str(free_port),
                "--traffic-pattern",
                "missing.json",
                "--reverse",
                "--json_log",
                "--logfile",
                str(client_log),
            ],
            check=True,
            timeout=10,
        )
        assert client.returncode == 0
        server.wait(timeout=10)
    finally:
        _stop_process(server)

    server_summary = next(
        obj for obj in _log_lines(server_log) if obj["type"] == "summary"
    )
    client_summary = next(
        obj for obj in _log_lines(client_log) if obj["type"] == "summary"
    )
    assert "trace file not found" in server_summary["stop_reason"]
    assert "missing.json" in server_summary["stop_reason"]
    assert client_summary["stop_reason"] == server_summary["stop_reason"]
    assert client_summary["bytes"] == 0
    assert server_summary["bytes"] == 0


def test_tcp_reverse_client_fails_when_trace_missing(tmp_path, free_port):
    _run_reverse_missing_trace(tmp_path, free_port, "tcp")


def test_udp_reverse_client_fails_when_trace_missing(tmp_path, free_port):
    _run_reverse_missing_trace(tmp_path, free_port, "udp")
