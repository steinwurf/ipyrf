import io
import json
import subprocess
import sys

import pytest

from ipyrf.clock_offset import (
    REMOTE_CODE,
    OffsetError,
    OffsetSample,
    collect_samples,
    format_sample,
    measure_clock_offset,
    run_offset,
    ssh_probe_command,
    summarize,
)
from ipyrf.cli import parse_interval, parse_samples


def _local_python_popen(cmd, **kwargs):
    return subprocess.Popen(
        [sys.executable, "-u", "-c", REMOTE_CODE],
        **kwargs,
    )


def _stop(proc):
    if proc.stdin:
        try:
            proc.stdin.close()
        except OSError:
            pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def test_offset_from_times_uses_feasible_interval():
    sample = OffsetSample.from_times(
        t0_ns=1_000_000_000,
        t1_ns=1_002_000_000,
        remote_ns=1_001_500_000,
    )
    assert sample.rtt_ns == 2_000_000
    # Offset must lie in [remote - t1, remote - t0].
    assert sample.lower_ns == -500_000
    assert sample.upper_ns == 1_500_000
    assert sample.offset_ns == 500_000
    assert sample.uncertainty_ns == 1_000_000


def test_summarize_intersects_sample_intervals():
    samples = [
        OffsetSample.from_times(0, 10_000_000, 100_000_000),  # [90e6, 100e6]
        OffsetSample.from_times(0, 4_000_000, 95_000_000),  # [91e6, 95e6]
        OffsetSample.from_times(0, 8_000_000, 98_000_000),  # [90e6, 98e6]
    ]
    result = summarize("user@host", samples)
    assert result.host == "user@host"
    assert result.lower_ns == 91_000_000
    assert result.upper_ns == 95_000_000
    assert result.offset_ns == 93_000_000
    assert result.uncertainty_ns == 2_000_000
    assert result.min_rtt_ns == 4_000_000
    assert result.mean_rtt_ns == int(round((10_000_000 + 4_000_000 + 8_000_000) / 3))


def test_summarize_rejects_empty():
    with pytest.raises(OffsetError, match="no samples"):
        summarize("host", [])


def test_summarize_rejects_non_overlapping_intervals():
    samples = [
        OffsetSample.from_times(0, 2_000_000, 10_000_000),  # [8e6, 10e6]
        OffsetSample.from_times(0, 2_000_000, 20_000_000),  # [18e6, 20e6]
    ]
    with pytest.raises(OffsetError, match="no overlap"):
        summarize("host", samples)


def test_ssh_probe_command_layout():
    cmd = ssh_probe_command(
        "user@rasp1",
        ssh_args=["-p", "2222"],
        remote_python="python3.11",
    )
    assert cmd[0] == "ssh"
    assert cmd[1] == "-T"
    assert cmd[2:4] == ["-p", "2222"]
    assert cmd[-2] == "user@rasp1"
    assert cmd[-1].startswith("python3.11 -u -c ")
    assert "time.time_ns()" in cmd[-1]


def test_collect_samples_against_local_python():
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", REMOTE_CODE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        seen = []
        samples = collect_samples(
            proc,
            samples=5,
            interval_s=0.0,
            on_sample=seen.append,
        )
    finally:
        _stop(proc)

    assert len(samples) == 5
    assert seen == samples
    for sample in samples:
        assert sample.rtt_ns >= 0
        assert sample.rtt_ns < 1_000_000_000
        assert abs(sample.offset_ns) < 20_000_000


def test_warmup_excludes_startup_delay():
    delayed = """
import sys
import time

time.sleep(0.25)
for line in sys.stdin:
    print(time.time_ns(), flush=True)
"""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", delayed],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        samples = collect_samples(
            proc,
            samples=3,
            interval_s=0.0,
            warmup=True,
        )
    finally:
        _stop(proc)

    assert len(samples) == 3
    for sample in samples:
        assert sample.rtt_ns < 100_000_000


def test_measure_clock_offset_uses_ssh_command_and_local_probe():
    captured = {}

    def popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _local_python_popen(cmd, **kwargs)

    result = measure_clock_offset(
        "buildbot@rasp1",
        samples=3,
        interval_s=0.0,
        ssh_args=["-o", "BatchMode=yes"],
        popen=popen,
    )
    assert captured["cmd"][0] == "ssh"
    assert captured["cmd"][-2] == "buildbot@rasp1"
    assert "-o" in captured["cmd"]
    assert result.host == "buildbot@rasp1"
    assert len(result.samples) == 3
    assert abs(result.offset_ns) < 20_000_000


def test_collect_samples_invalid_timestamp():
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", "print('not-a-time', flush=True)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        with pytest.raises(OffsetError, match="invalid remote timestamp"):
            collect_samples(proc, samples=1, interval_s=0.0)
    finally:
        _stop(proc)


def test_collect_samples_remote_exit():
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", "raise SystemExit(7)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        with pytest.raises(OffsetError, match="remote process exited"):
            collect_samples(proc, samples=1, interval_s=0.0)
    finally:
        _stop(proc)


def test_run_offset_human_and_json():
    human = io.StringIO()
    result = run_offset(
        "local",
        samples=2,
        interval_s=0.0,
        file=human,
        popen=_local_python_popen,
    )
    text = human.getvalue()
    assert "offset:" in text
    assert "±" in text
    assert "host:         local" in text
    assert "Positive offset" in text
    assert "symmetric-delay" in text
    assert format_sample(result.samples[0]) in text
    assert "Connecting to" not in text

    raw = io.StringIO()
    json_result = run_offset(
        "local",
        samples=2,
        interval_s=0.0,
        json_output=True,
        file=raw,
        popen=_local_python_popen,
    )
    payload = json.loads(raw.getvalue())
    assert payload["host"] == "local"
    assert len(payload["samples"]) == 2
    assert payload["offset_ns"] == json_result.offset_ns
    assert payload["uncertainty_ns"] == json_result.uncertainty_ns
    assert "lower_ns" in payload
    assert "t0_ns" in payload["samples"][0]
    assert "uncertainty_ns" in payload["samples"][0]


def test_parse_samples_and_interval():
    assert parse_samples("10") == 10
    assert parse_interval("0") == 0.0
    assert parse_interval("0.25") == 0.25
    with pytest.raises(Exception, match="samples must be >= 1"):
        parse_samples("0")
    with pytest.raises(Exception, match="interval must be >= 0"):
        parse_interval("-1")


def test_offset_cli_help():
    completed = subprocess.run(
        [sys.executable, "-m", "ipyrf", "offset", "-h"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "SSH" in completed.stdout
    assert "--samples" in completed.stdout
    assert "--json" in completed.stdout


def test_offset_cli_rejects_missing_host_and_zero_samples():
    missing = subprocess.run(
        [sys.executable, "-m", "ipyrf", "offset"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0

    zero = subprocess.run(
        [sys.executable, "-m", "ipyrf", "offset", "host", "--samples", "0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert zero.returncode != 0
    assert "samples" in zero.stderr
