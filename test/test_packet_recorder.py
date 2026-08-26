import csv
import json
import struct
import subprocess
import sys
import time

from ipyrf.packet_recorder import (
    CSV_COLUMNS,
    DEFAULT_NUM_CHUNKS,
    DEFAULT_RECORDS_PER_CHUNK,
    RECORD_STRUCT,
    PacketRecorder,
)


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


def _read_csv_rows(csv_path):
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def test_defaults_target_one_gbit_s():
    assert DEFAULT_RECORDS_PER_CHUNK == 524288
    assert DEFAULT_NUM_CHUNKS == 8


def test_in_memory_encoding_and_csv_on_close(tmp_path):
    csv_path = tmp_path / "packets.csv"
    samples = [
        (1, 100, 150, 1200, 0),
        (2, 200, 240, 64, 3),
        (3, 1000, 1000, 1500, 1),
    ]
    with PacketRecorder(csv_path, records_per_chunk=2, num_chunks=2) as recorder:
        for seq, tx_ns, rx_ns, size, event_id in samples:
            recorder.add(seq, tx_ns, rx_ns, size, event_id)
        assert recorder.recorded == len(samples)
        assert RECORD_STRUCT.size == struct.calcsize("<QQIII")
        assert not csv_path.exists()

    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert rows[1:] == [
        ["1", "1200", "100", "150", "0"],
        ["2", "64", "200", "240", "3"],
        ["3", "1500", "1000", "1000", "1"],
    ]


def test_chunk_rotation_keeps_all_records(tmp_path):
    csv_path = tmp_path / "grown.csv"
    count = 100
    with PacketRecorder(csv_path, records_per_chunk=2, num_chunks=4) as recorder:
        for i in range(count):
            recorder.add(i + 1, i * 10, i * 10 + 3, 100 + i)
        assert recorder.recorded == count
        assert not csv_path.exists()

    rows = _read_csv_rows(csv_path)
    assert len(rows) == count
    assert int(rows[-1]["sequence"]) == count
    assert int(rows[-1]["size_bytes"]) == 100 + count - 1


def test_csv_written_only_on_close(tmp_path):
    csv_path = tmp_path / "deferred.csv"
    count = 100
    with PacketRecorder(csv_path, records_per_chunk=2, num_chunks=4) as recorder:
        for i in range(count):
            recorder.add(i + 1, i * 10, i * 10 + 3, 1200)
        assert recorder.recorded == count
        assert recorder.dropped == 0
        assert not csv_path.exists()

    rows = _read_csv_rows(csv_path)
    assert len(rows) == count
    assert int(rows[0]["sequence"]) == 1
    assert int(rows[0]["size_bytes"]) == 1200
    assert int(rows[-1]["sequence"]) == count


def test_allocates_chunk_when_pool_empty(tmp_path):
    csv_path = tmp_path / "alloc.csv"
    count = 20
    with PacketRecorder(csv_path, records_per_chunk=1, num_chunks=2) as recorder:
        for i in range(count):
            recorder.add(i + 1, i, i + 1, 64)
        assert recorder.recorded == count
        assert recorder.dropped == 0

    rows = _read_csv_rows(csv_path)
    assert len(rows) == count


def test_cli_packet_record_help():
    result = subprocess.run(
        [sys.executable, "-m", "ipyrf", "udp", "server", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--packet-record" in result.stdout


def test_cli_packet_record_roundtrip(tmp_path, free_port):
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
            str(free_port),
            "--packet-record",
            str(csv_path),
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
                str(free_port),
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

    rows = _read_csv_rows(csv_path)
    assert list(rows[0].keys()) == list(CSV_COLUMNS)
    assert len(rows) == summary["packet_record_count"]
    assert int(rows[0]["received_ns"]) >= int(rows[0]["transmitted_ns"])
    assert int(rows[0]["size_bytes"]) > 0
    assert int(rows[0]["event_id"]) == 0


def test_cli_packet_record_event_id_from_trace(tmp_path, free_port):
    csv_path = tmp_path / "packets.csv"
    server_log = tmp_path / "server.log"
    client_log = tmp_path / "client.log"
    pattern_path = tmp_path / "trace.json"
    pattern_path.write_text(
        json.dumps(
            {
                "version": 1,
                "type": "trace",
                "events": [
                    {"timestamp": 0.0, "nbytes": 600},
                    {"timestamp": 0.0, "nbytes": 900},
                ],
            }
        )
    )

    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ipyrf",
            "udp",
            "server",
            "127.0.0.1",
            "--port",
            str(free_port),
            "--packet-record",
            str(csv_path),
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
                str(free_port),
                "--traffic-pattern",
                str(pattern_path),
                "-l",
                "1200",
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

    rows = _read_csv_rows(csv_path)
    assert summary["packet_record_count"] == len(rows)
    assert [int(r["event_id"]) for r in rows] == [1, 2]
    assert [int(r["size_bytes"]) for r in rows] == [600, 900]
