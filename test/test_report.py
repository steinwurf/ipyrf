import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ipyrf.recorder import CSV_COLUMNS, Recorder
from ipyrf.report import (
    ReportError,
    analyze,
    generate_report,
    load_record,
    load_pattern_info,
)
from ipyrf.report.data import EventSpan
from ipyrf.report.export import write_images
from ipyrf.report.html import _default_title, _intro_html, _kpi_cards
from ipyrf.report.loader import pattern_from_record_metadata
from ipyrf.report.plots import _merged_shading_spans, _overview_figure


def _write_csv(path: Path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)
    return path


def _fixture_rows():
    """19 packets: lose seq 5, receive seq 16 before 15."""
    rows = []
    for seq in range(1, 21):
        if seq == 5:
            continue
        tx = 1_000_000_000 + seq * 1_000_000
        rx = tx + 2_000_000
        if seq == 15:
            rx = 1_000_000_000 + 17_500_000
        elif seq == 16:
            rx = 1_000_000_000 + 16_500_000
        event_id = 1 if seq <= 10 else 2
        rows.append((seq, 1000, tx, rx, event_id))
    rows.sort(key=lambda r: r[3])
    return rows


def test_loader_reads_csv(tmp_path):
    path = _write_csv(tmp_path / "packets.csv", _fixture_rows())
    packets = load_record(path)
    assert len(packets) == 19
    assert packets.sequence[0] == 1
    assert packets.event_id[0] == 1
    assert packets.size_bytes[0] == 1000


def test_loader_event_id_optional(tmp_path):
    path = tmp_path / "old.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("sequence", "size_bytes", "transmitted_ns", "received_ns"))
        writer.writerow((1, 64, 10, 20))
    packets = load_record(path)
    assert packets.event_id == [0]


def test_loader_missing_column(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("sequence,size_bytes,transmitted_ns\n1,10,1\n")
    with pytest.raises(ReportError, match="missing required column"):
        load_record(path)


def test_loader_bad_integer(tmp_path):
    path = _write_csv(tmp_path / "bad.csv", [("x", 10, 1, 2, 0)])
    with pytest.raises(ReportError, match="invalid integer"):
        load_record(path)


def test_loader_empty(tmp_path):
    path = _write_csv(tmp_path / "empty.csv", [])
    with pytest.raises(ReportError, match="no records"):
        load_record(path)


def test_loader_missing_file(tmp_path):
    with pytest.raises(ReportError, match="cannot read"):
        load_record(tmp_path / "missing.csv")


def test_loader_reads_metadata_comment(tmp_path):
    path = tmp_path / "records.csv"
    with Recorder(
        path,
        metadata={"protocol": "tcp", "sequence": "receive_order"},
        records_per_chunk=2,
        num_chunks=2,
    ) as recorder:
        recorder.add(100, 150, 64)
    packets = load_record(path)
    assert packets.metadata["protocol"] == "tcp"
    assert packets.metadata["sequence"] == "receive_order"
    assert packets.metadata["record_count"] == 1
    assert len(packets) == 1
    assert packets.metadata.get("format") == "ipyrf-record"


def test_analyze_tcp_hides_loss(tmp_path):
    path = tmp_path / "records.csv"
    with Recorder(
        path,
        metadata={
            "protocol": "tcp",
            "sequence": "receive_order",
            "record_unit": "tcp_record",
            "sender": "127.0.0.1:1",
            "receiver": "127.0.0.1:5201",
            "bandwidth_bps": 5_000_000,
        },
        records_per_chunk=2,
        num_chunks=2,
    ) as recorder:
        recorder.add(100, 150, 64, seq=1)
        recorder.add(200, 250, 64, seq=2)
    data = analyze(load_record(path), bin_ns=1_000_000)
    assert data.summary.show_loss is False
    assert data.summary.protocol == "tcp"
    assert data.summary.lost_packets == 0
    assert data.summary.reordered_packets == 0
    assert data.loss_runs == []
    assert "Loss" not in _kpi_cards(data.summary)
    assert "Records" in _kpi_cards(data.summary)
    assert "TCP" in _default_title(data)
    assert "framed TCP" in _intro_html(data.summary)
    assert data.summary.bandwidth_bps == 5_000_000


def test_embedded_pattern_from_metadata(tmp_path):
    pattern = {
        "version": 1,
        "type": "trace",
        "metadata": {"name": "embedded"},
        "events": [
            {"timestamp": 0.0, "nbytes": 64, "tags": ["I"]},
            {"timestamp": 0.1, "nbytes": 64, "tags": ["P"]},
        ],
    }
    path = tmp_path / "records.csv"
    with Recorder(
        path,
        metadata={
            "protocol": "udp",
            "sequence": "sender",
            "traffic_pattern": pattern,
            "traffic_pattern_name": "trace.json",
        },
        records_per_chunk=2,
        num_chunks=2,
    ) as recorder:
        recorder.add(0, 1_000_000, 64, 1, seq=1)
        recorder.add(100_000_000, 101_000_000, 64, 2, seq=2)
    packets = load_record(path)
    info = pattern_from_record_metadata(packets.metadata)
    assert info is not None
    assert info.pattern_type == "trace"
    data = analyze(packets, info, bin_ns=1_000_000)
    assert data.summary.pattern_name == "embedded"
    assert data.summary.show_loss is True
    by_id = {e.event_id: e for e in data.events}
    assert by_id[1].tags == ["I"]
    assert by_id[2].tags == ["P"]


def test_invalid_traffic_pattern(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{}")
    with pytest.raises(ReportError, match="version"):
        load_pattern_info(path)


def test_analyze_loss_reorder_latency(tmp_path):
    path = _write_csv(tmp_path / "packets.csv", _fixture_rows())
    data = analyze(load_record(path), bin_ns=1_000_000)
    s = data.summary
    assert s.packets == 19
    assert s.bytes == 19_000
    assert s.lost_packets == 1
    assert s.first_sequence == 1
    assert s.last_sequence == 20
    assert s.reordered_packets == 1
    assert s.duplicate_packets == 0
    assert s.latency_p50_ms == pytest.approx(2.0, abs=0.6)
    assert s.event_ids == 2
    assert data.loss_runs[0].first_sequence == 5
    assert data.loss_runs[0].count == 1
    assert data.reordered.values == [15.0]
    assert any(b.lost_packets for b in data.bins)


def test_analyze_clock_offset_shifts_latency(tmp_path):
    path = _write_csv(tmp_path / "packets.csv", _fixture_rows())
    base = analyze(load_record(path), bin_ns=1_000_000)
    shifted = analyze(
        load_record(path), bin_ns=1_000_000, clock_offset_ms=3.5
    )
    assert shifted.summary.clock_offset_ms == 3.5
    assert shifted.summary.latency_p50_ms == pytest.approx(
        base.summary.latency_p50_ms + 3.5
    )
    assert shifted.summary.latency_p99_ms == pytest.approx(
        base.summary.latency_p99_ms + 3.5
    )
    for base_bin, shifted_bin in zip(base.bins, shifted.bins):
        if base_bin.latency_p50_ms is None:
            assert shifted_bin.latency_p50_ms is None
            continue
        assert shifted_bin.latency_p50_ms == pytest.approx(
            base_bin.latency_p50_ms + 3.5
        )


def test_analyze_event_stats_with_pattern(tmp_path):
    path = _write_csv(tmp_path / "packets.csv", _fixture_rows())
    pattern_path = tmp_path / "trace.json"
    pattern_path.write_text(
        json.dumps(
            {
                "version": 1,
                "type": "trace",
                "metadata": {"name": "fixture"},
                "events": [
                    {"timestamp": 0.0, "nbytes": 10000, "tags": ["A"]},
                    {"timestamp": 0.01, "nbytes": 10000, "tags": ["B"]},
                ],
            }
        )
    )
    pattern = load_pattern_info(pattern_path)
    data = analyze(load_record(path), pattern, bin_ns=1_000_000)
    assert data.summary.pattern_name == "fixture"
    by_id = {e.event_id: e for e in data.events}
    assert by_id[1].tags == ["A"]
    assert by_id[1].packets == 9
    assert by_id[1].bytes == 9000
    assert by_id[1].expected_bytes == 10000
    assert by_id[1].lost_packets == 1
    assert by_id[2].tags == ["B"]
    assert by_id[2].packets == 10
    assert by_id[2].lost_packets == 0
    assert data.event_spans
    assert {span.primary_tag for span in data.event_spans} == {"A", "B"}


def test_load_piecewise_rate_pattern(tmp_path):
    path = tmp_path / "rates.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "type": "piecewise_rate",
                "periods": [
                    {"duration": 2.0, "bitrate": "2M", "tags": ["burst"]},
                    {"duration": 1.0, "bitrate": 0},
                ],
            }
        )
    )
    info = load_pattern_info(path)
    assert info.pattern_type == "piecewise_rate"
    assert info.events[0].tags == ["burst"]
    assert info.events[0].bitrate_bps == pytest.approx(2e6)
    assert info.events[0].end_s == pytest.approx(2.0)
    assert info.events[1].nbytes == 0


def test_merged_shading_spans_joins_same_tags_across_gaps():
    spans = [
        EventSpan(1, 0.00, 0.01, ["I"]),
        EventSpan(2, 0.03, 0.04, ["B"]),
        EventSpan(3, 0.06, 0.07, ["B"]),
        EventSpan(4, 0.10, 0.11, ["P"]),
    ]
    merged = _merged_shading_spans(spans)
    assert [(round(s, 2), round(e, 2), key) for s, e, key, _color in merged] == [
        (0.00, 0.01, "I"),
        (0.03, 0.07, "B"),
        (0.10, 0.11, "P"),
    ]


def test_generate_html_and_json(tmp_path):
    record = _write_csv(tmp_path / "packets.csv", _fixture_rows())
    pattern_path = tmp_path / "trace.json"
    pattern_path.write_text(
        json.dumps(
            {
                "version": 1,
                "type": "trace",
                "metadata": {"name": "fixture"},
                "events": [
                    {"timestamp": 0.0, "nbytes": 10000, "tags": ["A"]},
                    {"timestamp": 0.01, "nbytes": 10000, "tags": ["B"]},
                ],
            }
        )
    )
    html_path = tmp_path / "report.html"
    json_path = tmp_path / "report.json"
    data = generate_report(
        record,
        html_path,
        traffic_pattern_path=pattern_path,
        json_path=json_path,
        title="Fixture",
        clock_offset_ms=1.25,
    )
    html = html_path.read_text(encoding="utf-8")
    assert "Fixture" in html
    assert "Throughput" in html
    assert "Latency" in html
    assert 'id="clock-offset-ms"' in html
    assert 'data-baked="1.25"' in html
    assert "data-latency-ms=" in html
    assert "ipyrf offset" in html
    assert "<!--OFFSET-SCRIPT-->" not in html
    assert "clock-offset-ms" in html
    assert "Per-event stats" not in html
    assert "Traffic events" in html
    assert "Empirical Cumulative Distribution Function" in html
    assert "About this report" in html
    assert "Glossary" in html
    assert "data-tip=" in html
    assert "plotly" in html.lower()
    assert "Plotly.newPlot" in html or "Plotly[" in html or "window.PLOTLY" in html or "plotly.js" in html.lower()
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["version"] == 1
    assert parsed["summary"]["packets"] == 19
    assert parsed["summary"]["lost_packets"] == 1
    assert parsed["summary"]["clock_offset_ms"] == pytest.approx(1.25)
    assert data.summary.packets == 19
    assert data.summary.clock_offset_ms == pytest.approx(1.25)


def test_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "ipyrf.report", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "RECORD" in result.stdout
    assert "--traffic-pattern" in result.stdout
    assert "--clock-offset" in result.stdout
    assert "--record" in result.stdout or "Record CSV" in result.stdout


def test_cli_writes_html(tmp_path):
    record = _write_csv(tmp_path / "packets.csv", _fixture_rows())
    html_path = tmp_path / "out.html"
    json_path = tmp_path / "out.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ipyrf.report",
            str(record),
            "-o",
            str(html_path),
            "--json",
            str(json_path),
            "--bin-ms",
            "1",
            "--title",
            "CLI report",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert html_path.exists()
    assert json_path.exists()
    assert "Wrote" in result.stdout
    assert "CLI report" in html_path.read_text(encoding="utf-8")


def test_cli_missing_record_exits_2(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "ipyrf.report", str(tmp_path / "nope.csv")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "error:" in result.stderr


def test_png_without_kaleido_errors(tmp_path, monkeypatch):
    record = _write_csv(tmp_path / "packets.csv", _fixture_rows())
    data = analyze(load_record(record), bin_ns=1_000_000)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "kaleido" or name.startswith("kaleido."):
            raise ImportError("no kaleido")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ReportError, match="kaleido"):
        write_images(data, tmp_path / "png", "png")
