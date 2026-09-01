from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..packet_recorder import CSV_COLUMNS
from ..traffic_pattern import TrafficPatternError, parse_traffic_pattern
from ..utils import parse_bandwidth
from .data import PacketTrace, PatternEvent, PatternInfo

REQUIRED_COLUMNS = (
    "sequence",
    "size_bytes",
    "transmitted_ns",
    "received_ns",
)


class ReportError(ValueError):
    """Raised when a packet record, pattern file, or report option is invalid."""


def load_packet_record(path: Union[str, Path]) -> PacketTrace:
    """Load and validate an ipyrf ``--packet-record`` CSV file."""
    path = Path(path)
    try:
        text = path.open("r", newline="", encoding="utf-8-sig")
    except OSError as e:
        raise ReportError(f"cannot read {path}: {e}") from e

    with text as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ReportError(f"{path}: file has no header row")
        fields = [_normalize_header(name) for name in reader.fieldnames]
        missing = [c for c in REQUIRED_COLUMNS if c not in fields]
        if missing:
            raise ReportError(
                f"{path}: missing required column(s) {', '.join(missing)}; "
                f"expected {', '.join(CSV_COLUMNS)}"
            )
        reader.fieldnames = fields
        has_event_id = "event_id" in fields

        sequence: List[int] = []
        size_bytes: List[int] = []
        transmitted_ns: List[int] = []
        received_ns: List[int] = []
        event_id: List[int] = []

        for line_number, row in enumerate(reader, start=2):
            try:
                seq = _parse_int(row.get("sequence"), "sequence")
                size = _parse_int(row.get("size_bytes"), "size_bytes")
                tx_ns = _parse_int(row.get("transmitted_ns"), "transmitted_ns")
                rx_ns = _parse_int(row.get("received_ns"), "received_ns")
                if size < 0:
                    raise ReportError("size_bytes must be >= 0")
                if tx_ns < 0:
                    raise ReportError("transmitted_ns must be >= 0")
                if rx_ns < 0:
                    raise ReportError("received_ns must be >= 0")
                if has_event_id:
                    eid = _parse_int(row.get("event_id"), "event_id")
                    if eid < 0:
                        raise ReportError("event_id must be >= 0")
                else:
                    eid = 0
            except ReportError as e:
                raise ReportError(f"{path}:{line_number}: {e}") from e
            sequence.append(seq)
            size_bytes.append(size)
            transmitted_ns.append(tx_ns)
            received_ns.append(rx_ns)
            event_id.append(eid)

    if not sequence:
        raise ReportError(f"{path}: no packet records")

    return PacketTrace(
        sequence=sequence,
        size_bytes=size_bytes,
        transmitted_ns=transmitted_ns,
        received_ns=received_ns,
        event_id=event_id,
        source=str(path),
    )


def load_pattern_info(path: Union[str, Path]) -> PatternInfo:
    """Load traffic-pattern JSON for event labels, tags, and expected sizes."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ReportError(f"cannot read {path}: {e}") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ReportError(
            f"{path}: invalid JSON: {e.msg} (line {e.lineno}, column {e.colno})"
        ) from e

    try:
        parse_traffic_pattern(data, source=str(path))
    except TrafficPatternError as e:
        raise ReportError(str(e)) from e

    return _pattern_info_from_document(data, source=str(path))


def _pattern_info_from_document(data: Dict[str, Any], *, source: str) -> PatternInfo:
    pattern_type = data["type"]
    metadata = data.get("metadata") or {}
    events: List[PatternEvent] = []

    if pattern_type == "trace":
        raw_events = data.get("events") or []
        times = [_as_float(raw["timestamp"]) for raw in raw_events]
        for i, raw in enumerate(raw_events):
            start = times[i]
            end = times[i + 1] if i + 1 < len(times) else start
            events.append(
                PatternEvent(
                    event_id=i + 1,
                    start_s=start,
                    end_s=end,
                    nbytes=int(raw["nbytes"]),
                    tags=_tags(raw),
                )
            )
    else:
        t = 0.0
        for i, raw in enumerate(data.get("periods") or []):
            duration = _as_float(raw["duration"])
            bitrate = _as_bitrate(raw["bitrate"])
            nbytes = int(bitrate * duration / 8.0)
            events.append(
                PatternEvent(
                    event_id=i + 1,
                    start_s=t,
                    end_s=t + duration,
                    nbytes=nbytes,
                    bitrate_bps=bitrate,
                    tags=_tags(raw),
                )
            )
            t += duration

    return PatternInfo(
        source=source,
        pattern_type=pattern_type,
        metadata=dict(metadata),
        events=events,
    )


def _normalize_header(name: Optional[str]) -> str:
    if name is None:
        return ""
    return name.strip().lstrip("\ufeff")


def _parse_int(value: Optional[str], field: str) -> int:
    if value is None or str(value).strip() == "":
        raise ReportError(f"missing {field}")
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        raise ReportError(f"invalid integer for {field}: {value!r}") from None


def _tags(raw: Dict[str, Any]) -> List[str]:
    tags = raw.get("tags") or []
    return [str(tag) for tag in tags]


def _as_float(value: Any) -> float:
    return float(value)


def _as_bitrate(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        parsed = parse_bandwidth(value)
        if parsed is None:
            raise ReportError(f"invalid bitrate {value!r}")
        return float(parsed)
    raise ReportError(f"invalid bitrate {value!r}")
