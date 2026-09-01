from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


REPORT_VERSION = 1


@dataclass
class PacketTrace:
    """Columnar packet-record data (one list per CSV column)."""

    sequence: List[int]
    size_bytes: List[int]
    transmitted_ns: List[int]
    received_ns: List[int]
    event_id: List[int]
    source: str = ""

    def __len__(self) -> int:
        return len(self.sequence)


@dataclass
class PatternEvent:
    event_id: int
    start_s: float
    end_s: float
    nbytes: Optional[int] = None
    bitrate_bps: Optional[float] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class PatternInfo:
    source: str
    pattern_type: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    events: List[PatternEvent] = field(default_factory=list)


@dataclass
class TimeBin:
    t_start_s: float
    t_end_s: float
    bits_per_second: float = 0.0
    bytes: int = 0
    packets: int = 0
    lost_packets: int = 0
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None
    latency_mean_ms: Optional[float] = None
    latency_min_ms: Optional[float] = None
    latency_max_ms: Optional[float] = None
    send_gap_p50_us: Optional[float] = None
    recv_gap_p50_us: Optional[float] = None


@dataclass
class EventSpan:
    event_id: int
    start_s: float
    end_s: float
    tags: List[str] = field(default_factory=list)

    @property
    def primary_tag(self) -> Optional[str]:
        return self.tags[0] if self.tags else None


@dataclass
class EventStats:
    event_id: int
    packets: int
    bytes: int
    lost_packets: int = 0
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None
    latency_mean_ms: Optional[float] = None
    tags: List[str] = field(default_factory=list)
    expected_bytes: Optional[int] = None
    start_s: Optional[float] = None
    end_s: Optional[float] = None


@dataclass
class PointSeries:
    t_s: List[float]
    values: List[float]
    extra: List[int] = field(default_factory=list)


@dataclass
class Histogram:
    edges: List[float]
    counts: List[int]


@dataclass
class Ecdf:
    values: List[float]
    probabilities: List[float]


@dataclass
class SpacingPair:
    t_s: float
    sequence: int
    send_gap_us: float
    recv_gap_us: float


@dataclass
class LossRun:
    first_sequence: int
    count: int
    t_s: Optional[float] = None


@dataclass
class ReorderEvent:
    sequence: int
    t_s: float
    max_seen: int


@dataclass
class Summary:
    packets: int
    bytes: int
    duration_s: float
    bits_per_second: float
    unique_sequences: int
    first_sequence: int
    last_sequence: int
    lost_packets: int
    lost_percent: float
    duplicate_packets: int
    reordered_packets: int
    latency_min_ms: Optional[float] = None
    latency_max_ms: Optional[float] = None
    latency_mean_ms: Optional[float] = None
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None
    negative_latency_count: int = 0
    event_ids: int = 0
    bin_ms: float = 0.0
    source: str = ""
    pattern_source: Optional[str] = None
    pattern_type: Optional[str] = None
    pattern_name: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


@dataclass
class ReportData:
    """Analysis results ready for HTML, JSON, or static-image export."""

    version: int
    summary: Summary
    bins: List[TimeBin]
    event_spans: List[EventSpan]
    events: List[EventStats]
    sequences: PointSeries
    reordered: PointSeries
    spacing: List[SpacingPair]
    latency_histogram: Histogram
    latency_ecdf: Ecdf
    loss_runs: List[LossRun]
    reorder_events: List[ReorderEvent]

    def to_dict(self) -> Dict[str, Any]:
        return _sanitize(asdict(self))

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj
