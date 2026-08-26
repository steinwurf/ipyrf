import json
from pathlib import Path

import pytest

from ipyrf.traffic_pattern import TraceTrafficPattern, parse_traffic_pattern
from ipyrf.video_trace import (
    VideoTraceError,
    generate_video_trace,
    generate_video_trace_from_ffprobe,
    load_ffprobe_json,
    run_ffprobe,
    write_video_trace,
)


def test_generate_video_trace_basic_gop():
    doc = generate_video_trace(
        fps=30,
        gop="IPBB",
        i_size=40_000,
        p_size=8_000,
        b_size=2_000,
        duration=1.0,
    )
    assert doc["version"] == 1
    assert doc["type"] == "trace"
    assert len(doc["events"]) == 30
    assert doc["metadata"]["generator"] == "synthetic-video"
    assert doc["metadata"]["gop"] == "IPBB"
    assert doc["metadata"]["frames"] == 30

    # First GOP: I, P, B, B
    assert doc["events"][0] == {
        "timestamp": 0.0,
        "nbytes": 40_000,
        "tags": ["I"],
    }
    assert doc["events"][1]["tags"] == ["P"]
    assert doc["events"][1]["nbytes"] == 8_000
    assert doc["events"][2]["tags"] == ["B"]
    assert doc["events"][2]["nbytes"] == 2_000
    assert doc["events"][3]["tags"] == ["B"]
    assert doc["events"][4]["tags"] == ["I"]

    assert doc["events"][1]["timestamp"] == pytest.approx(1.0 / 30.0)
    assert doc["events"][-1]["timestamp"] == pytest.approx(29.0 / 30.0)


def test_generate_video_trace_loads_as_normal_trace():
    doc = generate_video_trace(
        fps=25,
        gop="IPPP",
        i_size=10_000,
        p_size=1_000,
        b_size=0,
        duration=0.2,
    )
    pattern = parse_traffic_pattern(doc)
    assert isinstance(pattern, TraceTrafficPattern)
    # 0.2 * 25 = 5 frames: I P P P I
    assert pattern.total_bytes() == 10_000 + 1_000 * 3 + 10_000
    assert pattern.duration() == pytest.approx(4.0 / 25.0)


def test_write_video_trace_roundtrip(tmp_path: Path):
    path = tmp_path / "video.json"
    write_video_trace(
        path,
        fps=10,
        gop="ib",
        i_size=100,
        p_size=0,
        b_size=50,
        duration=0.5,
    )
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["type"] == "trace"
    assert loaded["metadata"]["gop"] == "IB"
    assert len(loaded["events"]) == 5
    pattern = parse_traffic_pattern(loaded)
    assert pattern.total_bytes() == 100 + 50 + 100 + 50 + 100


def test_generate_video_trace_merges_metadata():
    doc = generate_video_trace(
        fps=1,
        gop="I",
        i_size=10,
        p_size=0,
        b_size=0,
        duration=1,
        metadata={"name": "clip"},
    )
    assert doc["metadata"]["name"] == "clip"
    assert doc["metadata"]["generator"] == "synthetic-video"


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (
            {
                "fps": 0,
                "gop": "I",
                "i_size": 1,
                "p_size": 0,
                "b_size": 0,
                "duration": 1,
            },
            "fps must be > 0",
        ),
        (
            {
                "fps": 30,
                "gop": "",
                "i_size": 1,
                "p_size": 0,
                "b_size": 0,
                "duration": 1,
            },
            "gop must be a non-empty string",
        ),
        (
            {
                "fps": 30,
                "gop": "IX",
                "i_size": 1,
                "p_size": 0,
                "b_size": 0,
                "duration": 1,
            },
            "expected I, P, or B",
        ),
        (
            {
                "fps": 30,
                "gop": "I",
                "i_size": -1,
                "p_size": 0,
                "b_size": 0,
                "duration": 1,
            },
            "i_size must be >= 0",
        ),
        (
            {
                "fps": 30,
                "gop": "I",
                "i_size": 1,
                "p_size": 0,
                "b_size": 0,
                "duration": 0,
            },
            "duration must be > 0",
        ),
        (
            {
                "fps": 0.1,
                "gop": "I",
                "i_size": 1,
                "p_size": 0,
                "b_size": 0,
                "duration": 1,
            },
            "at least one frame",
        ),
    ],
)
def test_generate_video_trace_rejects_invalid_params(kwargs, match):
    with pytest.raises(VideoTraceError, match=match):
        generate_video_trace(**kwargs)


def _sample_ffprobe_doc():
    return {
        "frames": [
            {
                "media_type": "video",
                "pict_type": "I",
                "pkt_size": "1000",
                "pts_time": "1.0",
            },
            {
                "media_type": "video",
                "pict_type": "P",
                "pkt_size": "200",
                "pts_time": "1.1",
            },
            {
                "media_type": "audio",
                "pkt_size": "50",
                "pts_time": "1.05",
            },
            {
                "media_type": "video",
                "pict_type": "B",
                "pkt_size": "80",
                "best_effort_timestamp_time": "1.05",
            },
        ]
    }


def test_generate_from_ffprobe_normalizes_and_tags():
    doc = generate_video_trace_from_ffprobe(
        _sample_ffprobe_doc(), source="clip.mp4"
    )
    assert doc["type"] == "trace"
    assert doc["metadata"]["generator"] == "ffprobe-video"
    assert doc["metadata"]["source"] == "clip.mp4"
    assert len(doc["events"]) == 3
    # Sorted by pts, origin shifted to 0: 1.0, 1.05, 1.1 -> 0, 0.05, 0.1
    assert doc["events"][0]["timestamp"] == pytest.approx(0.0)
    assert doc["events"][0]["nbytes"] == 1000
    assert doc["events"][0]["tags"] == ["I"]
    assert doc["events"][1]["timestamp"] == pytest.approx(0.05)
    assert doc["events"][1]["nbytes"] == 80
    assert doc["events"][1]["tags"] == ["B"]
    assert doc["events"][2]["timestamp"] == pytest.approx(0.1)
    assert doc["events"][2]["tags"] == ["P"]

    pattern = parse_traffic_pattern(doc)
    assert pattern.total_bytes() == 1000 + 80 + 200


def test_generate_from_ffprobe_duration_truncates():
    doc = generate_video_trace_from_ffprobe(
        _sample_ffprobe_doc(), duration=0.06
    )
    assert len(doc["events"]) == 2
    assert doc["events"][-1]["timestamp"] == pytest.approx(0.05)
    assert doc["metadata"]["requested_duration"] == 0.06


def test_generate_from_ffprobe_frames_array():
    doc = generate_video_trace_from_ffprobe(
        [
            {
                "media_type": "video",
                "pict_type": "I",
                "pkt_size": 10,
                "pts_time": 0,
            }
        ]
    )
    assert len(doc["events"]) == 1
    assert doc["events"][0]["nbytes"] == 10


def test_load_ffprobe_json(tmp_path: Path):
    path = tmp_path / "frames.json"
    path.write_text(json.dumps(_sample_ffprobe_doc()), encoding="utf-8")
    data = load_ffprobe_json(path)
    doc = generate_video_trace_from_ffprobe(data)
    assert len(doc["events"]) == 3


def test_run_ffprobe_missing_binary(monkeypatch, tmp_path: Path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"not really video")
    monkeypatch.setattr("ipyrf.video_trace.shutil.which", lambda _name: None)
    with pytest.raises(VideoTraceError, match="ffprobe not found"):
        run_ffprobe(media)


def test_run_ffprobe_invokes_subprocess(monkeypatch, tmp_path: Path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")

    class Result:
        returncode = 0
        stdout = json.dumps(_sample_ffprobe_doc())
        stderr = ""

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "/usr/bin/ffprobe"
        assert str(media) in cmd
        assert "-show_frames" in cmd
        return Result()

    monkeypatch.setattr(
        "ipyrf.video_trace.shutil.which", lambda _name: "/usr/bin/ffprobe"
    )
    monkeypatch.setattr("ipyrf.video_trace.subprocess.run", fake_run)
    data = run_ffprobe(media)
    assert len(data["frames"]) == 4


@pytest.mark.parametrize(
    "data, match",
    [
        ({}, "frames"),
        ({"frames": []}, "no video frames"),
        (
            {
                "frames": [
                    {"media_type": "video", "pkt_size": "1"},
                ]
            },
            "timestamp",
        ),
        (
            {
                "frames": [
                    {
                        "media_type": "video",
                        "pkt_size": "x",
                        "pts_time": "0",
                    }
                ]
            },
            "pkt_size",
        ),
    ],
)
def test_generate_from_ffprobe_rejects_bad_input(data, match):
    with pytest.raises(VideoTraceError, match=match):
        generate_video_trace_from_ffprobe(data)
