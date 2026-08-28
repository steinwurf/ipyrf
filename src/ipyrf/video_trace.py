from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .traffic_pattern import SUPPORTED_TRACE_VERSION


class VideoTraceError(ValueError):
    """Raised when synthetic video-trace parameters are invalid."""


_FRAME_SIZES = {
    "I": "i_size",
    "P": "p_size",
    "B": "b_size",
}

_FFPROBE_TIME_KEYS = (
    "pts_time",
    "best_effort_timestamp_time",
    "pkt_pts_time",
    "pkt_dts_time",
)


def generate_video_trace(
    *,
    fps: float,
    gop: str,
    i_size: int,
    p_size: int,
    b_size: int,
    duration: float,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a normal ``trace`` traffic-pattern document for synthetic video.

    Frames are emitted at ``1 / fps`` intervals. Frame types cycle through the
    ``gop`` pattern (characters ``I``, ``P``, and ``B``). Each event uses the
    corresponding frame size and is tagged with that frame type. The result is
    a standard ``{"version": 1, "type": "trace", ...}`` document — not a
    video-specific pattern type.
    """
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or fps <= 0:
        raise VideoTraceError(f"fps must be > 0, got {fps!r}")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool):
        raise VideoTraceError(f"duration must be a number, got {duration!r}")
    if duration <= 0:
        raise VideoTraceError(f"duration must be > 0, got {duration}")

    pattern = _parse_gop(gop)
    sizes = {
        "I": _parse_frame_size(i_size, name="i_size"),
        "P": _parse_frame_size(p_size, name="p_size"),
        "B": _parse_frame_size(b_size, name="b_size"),
    }

    num_frames = int(float(duration) * float(fps))
    if num_frames <= 0:
        raise VideoTraceError(
            f"duration * fps must yield at least one frame, "
            f"got duration={duration}, fps={fps}"
        )

    frame_interval = 1.0 / float(fps)
    events: List[Dict[str, Any]] = []
    for index in range(num_frames):
        frame_type = pattern[index % len(pattern)]
        events.append(
            {
                "timestamp": index * frame_interval,
                "nbytes": sizes[frame_type],
                "tags": [frame_type],
            }
        )

    doc_metadata: Dict[str, Any] = {
        "generator": "synthetic-video",
        "fps": float(fps),
        "gop": "".join(pattern),
        "i_size": sizes["I"],
        "p_size": sizes["P"],
        "b_size": sizes["B"],
        "duration": float(duration),
        "frames": num_frames,
    }
    if metadata:
        if not isinstance(metadata, dict):
            raise VideoTraceError("metadata must be a dict when provided")
        doc_metadata.update(metadata)

    return _trace_document(events, doc_metadata)


def generate_video_trace_from_ffprobe(
    data: Any,
    *,
    duration: Optional[float] = None,
    source: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a ``trace`` document from ffprobe ``-show_frames`` JSON.

    Accepts the JSON object produced by::

        ffprobe -v quiet -select_streams v:0 -show_frames -print_format json INPUT

    Each video frame becomes one event using ``pkt_size`` and presentation
    time (``pts_time`` / ``best_effort_timestamp_time`` / …). Timestamps are
    normalized so the first frame starts at ``0``. Optional ``duration``
    keeps only events with relative timestamp ``< duration``.
    """
    if duration is not None:
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or duration <= 0
        ):
            raise VideoTraceError(f"duration must be > 0, got {duration!r}")

    frames = _extract_ffprobe_frames(data)
    parsed: List[Tuple[float, int, str]] = []
    for i, raw in enumerate(frames):
        if not isinstance(raw, dict):
            raise VideoTraceError(f"frames[{i}]: must be a JSON object")
        media_type = raw.get("media_type")
        if media_type is not None and media_type != "video":
            continue
        nbytes = _parse_ffprobe_pkt_size(raw.get("pkt_size"), index=i)
        timestamp = _parse_ffprobe_timestamp(raw, index=i)
        pict = raw.get("pict_type")
        if isinstance(pict, str) and pict.strip():
            frame_type = pict.strip().upper()[:1]
        else:
            frame_type = "?"
        parsed.append((timestamp, nbytes, frame_type))

    if not parsed:
        raise VideoTraceError("ffprobe JSON contains no video frames")

    parsed.sort(key=lambda item: item[0])
    origin = parsed[0][0]
    events: List[Dict[str, Any]] = []
    prev_ts = 0.0
    for absolute_ts, nbytes, frame_type in parsed:
        rel = absolute_ts - origin
        if rel < 0:
            rel = 0.0
        # Presentation order can still produce equal timestamps; keep
        # non-decreasing for the trace format.
        if events and rel < prev_ts:
            rel = prev_ts
        if duration is not None and rel >= float(duration):
            break
        events.append(
            {
                "timestamp": rel,
                "nbytes": nbytes,
                "tags": [frame_type],
            }
        )
        prev_ts = rel

    if not events:
        raise VideoTraceError(
            "no frames remain after applying duration "
            f"{duration!r}"
        )

    doc_metadata: Dict[str, Any] = {
        "generator": "ffprobe-video",
        "frames": len(events),
        "duration": events[-1]["timestamp"] if events else 0.0,
    }
    if source:
        doc_metadata["source"] = source
    if duration is not None:
        doc_metadata["requested_duration"] = float(duration)
    if metadata:
        if not isinstance(metadata, dict):
            raise VideoTraceError("metadata must be a dict when provided")
        doc_metadata.update(metadata)

    return _trace_document(events, doc_metadata)


def load_ffprobe_json(path: Union[str, Path]) -> Any:
    """Load an ffprobe JSON document from disk."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise VideoTraceError(f"cannot read {path}: {e}") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise VideoTraceError(
            f"{path}: invalid JSON: {e.msg} "
            f"(line {e.lineno}, column {e.colno})"
        ) from e


def run_ffprobe(path: Union[str, Path]) -> Any:
    """Run ffprobe on a media file and return parsed ``-show_frames`` JSON."""
    path = Path(path)
    if not path.is_file():
        raise VideoTraceError(f"media file not found: {path}")

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise VideoTraceError(
            "ffprobe not found on PATH; install ffmpeg or pass "
            "--ffprobe-json with a saved frames dump"
        )

    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-select_streams",
        "v:0",
        "-show_frames",
        "-print_format",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as e:
        raise VideoTraceError(f"failed to run ffprobe: {e}") from e

    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        detail = f": {err}" if err else ""
        raise VideoTraceError(
            f"ffprobe failed for {path} (exit {completed.returncode})"
            f"{detail}"
        )

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as e:
        raise VideoTraceError(
            f"ffprobe returned invalid JSON: {e.msg} "
            f"(line {e.lineno}, column {e.colno})"
        ) from e


def write_video_trace(
    path: Union[str, Path],
    *,
    fps: float,
    gop: str,
    i_size: int,
    p_size: int,
    b_size: int,
    duration: float,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a synthetic video trace and write it as JSON."""
    doc = generate_video_trace(
        fps=fps,
        gop=gop,
        i_size=i_size,
        p_size=p_size,
        b_size=b_size,
        duration=duration,
        metadata=metadata,
    )
    write_trace_document(path, doc)
    return doc


def write_trace_document(path: Union[str, Path], doc: Dict[str, Any]) -> None:
    """Write a traffic-pattern JSON document to ``path``."""
    Path(path).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def _trace_document(
    events: Sequence[Dict[str, Any]], metadata: Dict[str, Any]
) -> Dict[str, Any]:
    return {
        "version": SUPPORTED_TRACE_VERSION,
        "type": "trace",
        "metadata": metadata,
        "events": list(events),
    }


def _extract_ffprobe_frames(data: Any) -> List[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "frames" in data:
            frames = data["frames"]
            if not isinstance(frames, list):
                raise VideoTraceError("'frames' must be a JSON array")
            return frames
        raise VideoTraceError(
            "ffprobe JSON must be a frames array or an object with a "
            "'frames' field (use -show_frames -print_format json)"
        )
    raise VideoTraceError(
        "ffprobe JSON must be a JSON object or array, "
        f"got {type(data).__name__}"
    )


def _parse_ffprobe_pkt_size(value: Any, *, index: int) -> int:
    loc = f"frames[{index}]"
    if value is None:
        raise VideoTraceError(f"{loc}: missing pkt_size")
    if isinstance(value, bool):
        raise VideoTraceError(f"{loc}: invalid pkt_size {value!r}")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not value.is_integer():
            raise VideoTraceError(f"{loc}: pkt_size must be an integer, got {value!r}")
        size = int(value)
    elif isinstance(value, str):
        try:
            size = int(value.strip())
        except ValueError as e:
            raise VideoTraceError(f"{loc}: invalid pkt_size {value!r}") from e
    else:
        raise VideoTraceError(f"{loc}: invalid pkt_size {value!r}")
    if size < 0:
        raise VideoTraceError(f"{loc}: pkt_size must be >= 0, got {size}")
    return size


def _parse_ffprobe_timestamp(raw: Dict[str, Any], *, index: int) -> float:
    loc = f"frames[{index}]"
    for key in _FFPROBE_TIME_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if value in (None, "N/A", "n/a"):
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                continue
    raise VideoTraceError(
        f"{loc}: missing usable presentation timestamp "
        f"(tried {', '.join(_FFPROBE_TIME_KEYS)})"
    )


def _parse_gop(gop: str) -> List[str]:
    if not isinstance(gop, str) or not gop:
        raise VideoTraceError(f"gop must be a non-empty string, got {gop!r}")
    pattern: List[str] = []
    for i, ch in enumerate(gop):
        frame_type = ch.upper()
        if frame_type not in _FRAME_SIZES:
            raise VideoTraceError(
                f"gop character {i}: expected I, P, or B, got {ch!r}"
            )
        pattern.append(frame_type)
    return pattern


def _parse_frame_size(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VideoTraceError(f"{name} must be an integer, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise VideoTraceError(f"{name} must be an integer, got {value!r}")
    size = int(value)
    if size < 0:
        raise VideoTraceError(f"{name} must be >= 0, got {size}")
    return size
