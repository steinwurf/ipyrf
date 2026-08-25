import csv
import io
import json
import os
import socket
import struct
import subprocess
import sys
import time

from ipyrf.packet_recorder import (
    CSV_COLUMNS,
    RECORD_STRUCT,
    PacketRecorder,
    export_csv,
    main as packet_record_main,
    records_from_file,
)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_log_type(path, log_type, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == log_type:
                    return obj
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {log_type} in {path}")


def test_binary_encoding_and_csv_export(tmp_path):
    record_path = tmp_path / "packets.bin"
    samples = [
        (1, 100, 150),
        (2, 200, 240),
        (3, 1000, 1000),
    ]
    with PacketRecorder(
        record_path, records_per_buffer=8, num_buffers=2
    ) as recorder:
        for seq, tx_ns, rx_ns in samples:
            recorder.add(seq, tx_ns, rx_ns)

    decoded = list(records_from_file(record_path))
    assert decoded == samples
    assert RECORD_STRUCT.size == struct.calcsize("<IQQ")

    csv_path = tmp_path / "packets.csv"
    export_csv(record_path, csv_path)
    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert rows[1:] == [
        ["1", "100", "150", "50"],
        ["2", "200", "240", "40"],
        ["3", "1000", "1000", "0"],
    ]

    stdout = io.StringIO()
    export_csv(record_path, fileobj=stdout)
    assert stdout.getvalue().splitlines()[0] == ",".join(CSV_COLUMNS)

    out_csv = tmp_path / "from_main.csv"
    assert packet_record_main([str(record_path), str(out_csv)]) == 0
    assert out_csv.read_text().splitlines()[0] == ",".join(CSV_COLUMNS)


def test_buffer_rotation_writes_all_records(tmp_path):
    record_path = tmp_path / "rotated.bin"
    count = 7
    with PacketRecorder(
        record_path,
        records_per_buffer=2,
        num_buffers=8,
        start_writer=False,
    ) as recorder:
        for i in range(count):
            recorder.add(i + 1, i * 10, i * 10 + 3)
        assert recorder.dropped == 0
        assert recorder.recorded == count

    records = list(records_from_file(record_path))
    assert [seq for seq, _, _ in records] == list(range(1, count + 1))
    assert len(records) == count
    assert os.path.getsize(record_path) == count * RECORD_STRUCT.size


def test_background_writer_rotation(tmp_path):
    record_path = tmp_path / "async.bin"
    count = 7
    with PacketRecorder(
        record_path, records_per_buffer=2, num_buffers=8
    ) as recorder:
        for i in range(count):
            recorder.add(i + 1, i * 10, i * 10 + 3)
        assert recorder.dropped == 0

    records = list(records_from_file(record_path))
    assert [seq for seq, _, _ in records] == list(range(1, count + 1))


def test_dropped_record_accounting(tmp_path):
    record_path = tmp_path / "dropped.bin"
    recorder = PacketRecorder(
        record_path,
        records_per_buffer=2,
        num_buffers=2,
        start_writer=False,
    )
    try:
        for i in range(6):
            recorder.add(i + 1, i, i + 1)
        assert recorder.recorded == 2
        assert recorder.dropped == 4
    finally:
        recorder.close()

    records = list(records_from_file(record_path))
    assert records == [(1, 0, 1), (2, 1, 2)]
    assert recorder.recorded + recorder.dropped == 6


def test_cli_packet_record_help():
    result = subprocess.run(
        [sys.executable, "-m", "ipyrf", "udp", "server", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--packet-record" in result.stdout


def test_cli_packet_record_roundtrip(tmp_path):
    port = _free_port()
    record_path = tmp_path / "packets.bin"
    csv_path = tmp_path / "packets.csv"
    server_log = tmp_path / "server.log"
    client_log = tmp_path / "client.log"

    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ipyrf",
            "udp",
            "server",
            "127.0.0.1",
            "--port",
            str(port),
            "--packet-record",
            str(record_path),
            "--json_log",
            "--logfile",
            str(server_log),
        ]
    )
    try:
        _wait_for_log_type(server_log, "start")
        client = subprocess.run(
            [
                sys.executable,
                "-m",
                "ipyrf",
                "udp",
                "client",
                "127.0.0.1",
                "--port",
                str(port),
                "--time",
                "1",
                "--bandwidth",
                "5M",
                "--json_log",
                "--logfile",
                str(client_log),
            ],
            check=True,
            timeout=10,
        )
        assert client.returncode == 0
        summary = _wait_for_log_type(server_log, "summary", timeout=10)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

    assert summary["packet_record_count"] == summary["packets"]
    assert summary["packet_record_dropped"] == 0
    assert summary["packet_record_count"] > 0

    records = list(records_from_file(record_path))
    assert len(records) == summary["packet_record_count"]
    export_csv(record_path, csv_path)
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == list(CSV_COLUMNS)
    assert len(rows) == summary["packet_record_count"]
    assert int(rows[0]["latency_ns"]) == int(rows[0]["rx_ns"]) - int(rows[0]["tx_ns"])
