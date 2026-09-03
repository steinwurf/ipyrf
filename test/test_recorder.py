import csv
import io
import json
import struct
import subprocess
import sys
import time
from pathlib import Path

from ipyrf.recorder import (
    CSV_COLUMNS,
    DEFAULT_NUM_CHUNKS,
    DEFAULT_RECORDS_PER_CHUNK,
    METADATA_MAGIC,
    RECORD_STRUCT,
    Recorder,
    parse_record_text,
)
from ipyrf.tcp import TCP_HDR_SIZE


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
    _meta, csv_text = parse_record_text(Path(csv_path).read_text(encoding="utf-8"))
    return list(csv.DictReader(io.StringIO(csv_text)))


def _read_metadata(csv_path):
    meta, _csv_text = parse_record_text(Path(csv_path).read_text(encoding="utf-8"))
    return meta


def test_defaults_target_one_gbit_s():
    assert DEFAULT_RECORDS_PER_CHUNK == 524288
    assert DEFAULT_NUM_CHUNKS == 8


def test_in_memory_encoding_and_csv_on_close(tmp_path):
    csv_path = tmp_path / "records.csv"
    samples = [
        (100, 150, 1200, 0, 1),
        (200, 240, 64, 3, 2),
        (1000, 1000, 1500, 1, 3),
    ]
    with Recorder(csv_path, records_per_chunk=2, num_chunks=2) as recorder:
        for tx_ns, rx_ns, size, event_id, seq in samples:
            recorder.add(tx_ns, rx_ns, size, event_id, seq=seq)
        assert recorder.recorded == len(samples)
        assert RECORD_STRUCT.size == struct.calcsize("<QQIII")
        assert not csv_path.exists()

    with open(csv_path, newline="", encoding="utf-8") as f:
        text = f.read()
    assert text.startswith(f"# {METADATA_MAGIC}\n")
    meta, csv_text = parse_record_text(text)
    assert meta["format"] == "ipyrf-record"
    assert meta["version"] == 1
    assert meta["record_count"] == len(samples)
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert rows[1:] == [
        ["1", "1200", "100", "150", "0"],
        ["2", "64", "200", "240", "3"],
        ["3", "1500", "1000", "1000", "1"],
    ]


def test_implicit_sequence(tmp_path):
    csv_path = tmp_path / "implicit.csv"
    with Recorder(csv_path, records_per_chunk=2, num_chunks=2) as recorder:
        recorder.add(100, 150, 1200)
        recorder.add(200, 240, 64, 3)
        recorder.add(300, 310, 80, seq=9)
        assert recorder.recorded == 3

    rows = _read_csv_rows(csv_path)
    assert [int(r["sequence"]) for r in rows] == [1, 2, 9]
    assert [int(r["event_id"]) for r in rows] == [0, 3, 0]


def test_chunk_rotation_keeps_all_records(tmp_path):
    csv_path = tmp_path / "grown.csv"
    count = 100
    with Recorder(csv_path, records_per_chunk=2, num_chunks=4) as recorder:
        for i in range(count):
            recorder.add(i * 10, i * 10 + 3, 100 + i)
        assert recorder.recorded == count
        assert not csv_path.exists()

    rows = _read_csv_rows(csv_path)
    assert len(rows) == count
    assert int(rows[-1]["sequence"]) == count
    assert int(rows[-1]["size_bytes"]) == 100 + count - 1


def test_csv_written_only_on_close(tmp_path):
    csv_path = tmp_path / "deferred.csv"
    count = 100
    with Recorder(csv_path, records_per_chunk=2, num_chunks=4) as recorder:
        for i in range(count):
            recorder.add(i * 10, i * 10 + 3, 1200)
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
    with Recorder(csv_path, records_per_chunk=1, num_chunks=2) as recorder:
        for i in range(count):
            recorder.add(i, i + 1, 64)
        assert recorder.recorded == count
        assert recorder.dropped == 0

    rows = _read_csv_rows(csv_path)
    assert len(rows) == count


def test_cli_record_help():
    for protocol in ("udp", "tcp"):
        result = subprocess.run(
            [sys.executable, "-m", "ipyrf", protocol, "server", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "--record" in result.stdout
        assert "--packet-record" not in result.stdout


def test_cli_record_rejected_on_sending_client():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ipyrf",
            "tcp",
            "client",
            "127.0.0.1",
            "--record",
            "out.csv",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--record is only used when receiving" in result.stderr


def _run_recorded_test(
    tmp_path,
    free_port,
    *,
    protocol,
    extra_client=(),
    record_flag="--record",
):
    csv_path = tmp_path / "records.csv"
    server_log = tmp_path / "server.log"
    client_log = tmp_path / "client.log"
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ipyrf",
            protocol,
            "server",
            "127.0.0.1",
            "--port",
            str(free_port),
            record_flag,
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
                protocol,
                "client",
                "127.0.0.1",
                "--port",
                str(free_port),
                *extra_client,
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
    return summary, _read_csv_rows(csv_path), _read_metadata(csv_path)


def test_cli_udp_record_roundtrip(tmp_path, free_port):
    summary, rows, meta = _run_recorded_test(
        tmp_path,
        free_port,
        protocol="udp",
        extra_client=("--time", "1", "--bandwidth", "5M"),
    )
    assert summary["record_count"] == summary["packets"]
    assert summary["record_dropped"] == 0
    assert summary["record_count"] > 0
    assert list(rows[0].keys()) == list(CSV_COLUMNS)
    assert len(rows) == summary["record_count"]
    assert int(rows[0]["received_ns"]) >= int(rows[0]["transmitted_ns"])
    assert int(rows[0]["size_bytes"]) > 0
    assert int(rows[0]["event_id"]) == 0
    assert meta["protocol"] == "udp"
    assert meta["sequence"] == "sender"
    assert meta["record_unit"] == "datagram"
    assert meta["role"] == "server"
    assert meta["reverse"] is False
    assert meta["record_count"] == summary["record_count"]


def test_cli_packet_record_alias(tmp_path, free_port):
    summary, rows, meta = _run_recorded_test(
        tmp_path,
        free_port,
        protocol="udp",
        extra_client=("--time", "1", "--bandwidth", "5M"),
        record_flag="--packet-record",
    )
    assert summary["record_count"] == len(rows)
    assert summary["record_count"] > 0


def test_cli_udp_record_event_id_from_trace(tmp_path, free_port):
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
    summary, rows, meta = _run_recorded_test(
        tmp_path,
        free_port,
        protocol="udp",
        extra_client=("--traffic-pattern", str(pattern_path), "-l", "1200"),
    )
    assert summary["record_count"] == len(rows)
    assert [int(r["event_id"]) for r in rows] == [1, 2]
    assert [int(r["size_bytes"]) for r in rows] == [600, 900]


def test_cli_tcp_record_roundtrip(tmp_path, free_port):
    summary, rows, meta = _run_recorded_test(
        tmp_path,
        free_port,
        protocol="tcp",
        extra_client=("--time", "1", "--bandwidth", "5M"),
    )
    assert summary["record_count"] > 0
    assert summary["record_dropped"] == 0
    assert list(rows[0].keys()) == list(CSV_COLUMNS)
    assert len(rows) == summary["record_count"]
    assert [int(r["sequence"]) for r in rows] == list(range(1, len(rows) + 1))
    assert int(rows[0]["received_ns"]) >= int(rows[0]["transmitted_ns"])
    assert int(rows[0]["size_bytes"]) >= TCP_HDR_SIZE
    assert int(rows[0]["event_id"]) == 0
    assert meta["protocol"] == "tcp"
    assert meta["sequence"] == "receive_order"
    assert meta["record_unit"] == "tcp_record"
    assert meta["role"] == "server"


def test_cli_tcp_record_event_id_from_trace(tmp_path, free_port):
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
    summary, rows, meta = _run_recorded_test(
        tmp_path,
        free_port,
        protocol="tcp",
        extra_client=("--traffic-pattern", str(pattern_path)),
    )
    assert summary["record_count"] == len(rows)
    assert [int(r["event_id"]) for r in rows] == [1, 2]
    assert [int(r["size_bytes"]) for r in rows] == [600, 900]
    assert [int(r["sequence"]) for r in rows] == [1, 2]
