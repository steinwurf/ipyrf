from __future__ import annotations

import bisect
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .utils import parse_bandwidth


class TrafficPatternError(ValueError):
    """Raised when a traffic pattern file or document is invalid."""


class TrafficPattern:
    """Application traffic becoming available over time.

    Relative times are seconds from the start of the pattern. Subclasses
    describe *when* bytes become available; they do not pace the network.
    """

    def cumulative_bytes(self, elapsed: float) -> int:
        """Return how many bytes have become available by ``elapsed``."""
        raise NotImplementedError

    def duration(self) -> float:
        """Return the relative time of the final availability event.

        Returns ``0.0`` if the pattern has no events.
        """
        raise NotImplementedError

    def total_bytes(self) -> int:
        """Return the total number of bytes the pattern will make available."""
        raise NotImplementedError

    def next_event_time(
        self, elapsed: float, min_bytes: int = 1
    ) -> Optional[float]:
        """Return the next relative time when more bytes become available.

        Args:
            elapsed: Current relative time in seconds.
            min_bytes: Prefer waiting until at least this many additional
                bytes are available (used by continuous-rate patterns).
                Discrete patterns may ignore this and return the next
                event that increases availability.

        Returns ``None`` if no later events remain.
        """
        return None


class TraceTrafficPattern(TrafficPattern):
    """Traffic pattern from discrete ``(timestamp, nbytes)`` events.

    Each event makes ``nbytes`` available at relative time ``timestamp``
    (seconds from start). Timestamps must be non-decreasing and ``>= 0``;
    ``nbytes`` must be ``>= 0``.
    """

    def __init__(
        self,
        events: Sequence[Tuple[float, int]],
        metadata: Optional[Dict[str, Any]] = None,
    ):
        times: List[float] = []
        cumulative: List[int] = []
        total = 0
        prev_ts = 0.0
        for i, (ts, nbytes) in enumerate(events):
            if ts < 0:
                raise TrafficPatternError(
                    f"event {i}: timestamp must be >= 0, got {ts}"
                )
            if nbytes < 0:
                raise TrafficPatternError(
                    f"event {i}: nbytes must be >= 0, got {nbytes}"
                )
            if times and ts < prev_ts:
                raise TrafficPatternError(
                    f"event {i}: timestamps must be non-decreasing "
                    f"({prev_ts} -> {ts})"
                )
            total += int(nbytes)
            times.append(float(ts))
            cumulative.append(total)
            prev_ts = ts

        self._times = times
        self._cumulative = cumulative
        self.metadata: Dict[str, Any] = dict(metadata) if metadata else {}

    def cumulative_bytes(self, elapsed: float) -> int:
        if not self._times or elapsed < self._times[0]:
            return 0
        index = bisect.bisect_right(self._times, elapsed) - 1
        return self._cumulative[index]

    def duration(self) -> float:
        if not self._times:
            return 0.0
        return self._times[-1]

    def total_bytes(self) -> int:
        if not self._cumulative:
            return 0
        return self._cumulative[-1]

    def next_event_time(
        self, elapsed: float, min_bytes: int = 1
    ) -> Optional[float]:
        if not self._times:
            return None
        index = bisect.bisect_right(self._times, elapsed)
        while index < len(self._times):
            # Skip events that do not increase available bytes.
            prev = self._cumulative[index - 1] if index > 0 else 0
            if self._cumulative[index] > prev:
                return self._times[index]
            index += 1
        return None


class PiecewiseRateTrafficPattern(TrafficPattern):
    """Traffic pattern from consecutive constant-bitrate periods.

    Each period has a ``duration`` (seconds) and ``bitrate`` (bits/s).
    Bytes become available continuously at ``bitrate / 8`` bytes/s.
    A bitrate of ``0`` is idle (no bytes). Durations must be ``> 0``;
    bitrates must be ``>= 0``.
    """

    def __init__(
        self,
        periods: Sequence[Tuple[float, float]],
        metadata: Optional[Dict[str, Any]] = None,
    ):
        starts: List[float] = []
        rates_Bps: List[float] = []
        cum_at_start: List[int] = []
        t = 0.0
        total = 0
        for i, (duration, bitrate_bps) in enumerate(periods):
            if duration <= 0:
                raise TrafficPatternError(
                    f"period {i}: duration must be > 0, got {duration}"
                )
            if bitrate_bps < 0:
                raise TrafficPatternError(
                    f"period {i}: bitrate must be >= 0, got {bitrate_bps}"
                )
            starts.append(t)
            rates_Bps.append(float(bitrate_bps) / 8.0)
            cum_at_start.append(total)
            period_bytes = int(float(bitrate_bps) * float(duration) / 8.0)
            total += period_bytes
            t += float(duration)

        self._starts = starts
        self._rates_Bps = rates_Bps
        self._cum_at_start = cum_at_start
        self._duration = t
        self._total_bytes = total
        self.metadata: Dict[str, Any] = dict(metadata) if metadata else {}

    def cumulative_bytes(self, elapsed: float) -> int:
        if not self._starts or elapsed <= 0:
            return 0
        if elapsed >= self._duration:
            return self._total_bytes

        index = bisect.bisect_right(self._starts, elapsed) - 1
        into = elapsed - self._starts[index]
        return self._cum_at_start[index] + int(self._rates_Bps[index] * into)

    def duration(self) -> float:
        return self._duration

    def total_bytes(self) -> int:
        return self._total_bytes

    def next_event_time(
        self, elapsed: float, min_bytes: int = 1
    ) -> Optional[float]:
        if not self._starts or min_bytes <= 0:
            return None
        if elapsed < 0:
            elapsed = 0.0

        current = self.cumulative_bytes(elapsed)
        if current >= self._total_bytes:
            return None

        target = current + min_bytes
        if target > self._total_bytes:
            target = self._total_bytes

        index = 0
        if elapsed > 0:
            index = bisect.bisect_right(self._starts, elapsed) - 1

        for i in range(max(index, 0), len(self._starts)):
            rate = self._rates_Bps[i]
            if rate <= 0:
                continue
            cum_start = self._cum_at_start[i]
            if target <= cum_start:
                return self._starts[i]
            needed = target - cum_start
            t = self._starts[i] + needed / rate
            # Stay within this period: period end is start of next or duration.
            period_end = (
                self._starts[i + 1]
                if i + 1 < len(self._starts)
                else self._duration
            )
            if t <= period_end + 1e-12:
                return max(t, elapsed)
        return None


SUPPORTED_TRACE_VERSION = 1
SUPPORTED_TYPES = ("trace", "piecewise_rate")


def load_traffic_pattern(path: Union[str, Path]) -> TrafficPattern:
    """Load a versioned JSON traffic-pattern file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise TrafficPatternError(f"cannot read {path}: {e}") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise TrafficPatternError(
            f"{path}: invalid JSON: {e.msg} "
            f"(line {e.lineno}, column {e.colno})"
        ) from e

    return parse_traffic_pattern(data, source=str(path))


def parse_traffic_pattern(
    data: Any, *, source: str = "<input>"
) -> TrafficPattern:
    """Parse a versioned traffic-pattern JSON document."""
    if not isinstance(data, dict):
        raise TrafficPatternError(f"{source}: top level must be a JSON object")

    if "version" not in data:
        raise TrafficPatternError(f"{source}: missing required field 'version'")
    version = data["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise TrafficPatternError(
            f"{source}: 'version' must be an integer, got {version!r}"
        )
    if version != SUPPORTED_TRACE_VERSION:
        raise TrafficPatternError(
            f"{source}: unsupported version {version}; "
            f"supported version is {SUPPORTED_TRACE_VERSION}"
        )

    if "type" not in data:
        raise TrafficPatternError(f"{source}: missing required field 'type'")
    pattern_type = data["type"]
    if pattern_type not in SUPPORTED_TYPES:
        supported = ", ".join(repr(t) for t in SUPPORTED_TYPES)
        raise TrafficPatternError(
            f"{source}: unsupported type {pattern_type!r}; "
            f"supported types are {supported}"
        )

    metadata = data.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise TrafficPatternError(
            f"{source}: 'metadata' must be a JSON object when present"
        )

    if pattern_type == "trace":
        return _parse_trace(data, metadata=metadata, source=source)
    return _parse_piecewise_rate(data, metadata=metadata, source=source)


def _parse_trace(
    data: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]],
    source: str,
) -> TraceTrafficPattern:
    if "events" not in data:
        raise TrafficPatternError(f"{source}: missing required field 'events'")
    events_data = data["events"]
    if not isinstance(events_data, list):
        raise TrafficPatternError(f"{source}: 'events' must be a JSON array")

    events: List[Tuple[float, int]] = []
    for i, raw in enumerate(events_data):
        events.append(_parse_event(raw, source=source, index=i))

    try:
        return TraceTrafficPattern(events, metadata=metadata)
    except TrafficPatternError as e:
        raise TrafficPatternError(f"{source}: {e}") from e


def _parse_piecewise_rate(
    data: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]],
    source: str,
) -> PiecewiseRateTrafficPattern:
    if "periods" not in data:
        raise TrafficPatternError(f"{source}: missing required field 'periods'")
    periods_data = data["periods"]
    if not isinstance(periods_data, list):
        raise TrafficPatternError(f"{source}: 'periods' must be a JSON array")

    periods: List[Tuple[float, float]] = []
    for i, raw in enumerate(periods_data):
        periods.append(_parse_period(raw, source=source, index=i))

    try:
        return PiecewiseRateTrafficPattern(periods, metadata=metadata)
    except TrafficPatternError as e:
        raise TrafficPatternError(f"{source}: {e}") from e


def _parse_event(raw: Any, *, source: str, index: int) -> Tuple[float, int]:
    loc = f"{source}: event {index}"
    if not isinstance(raw, dict):
        raise TrafficPatternError(f"{loc}: must be a JSON object")

    if "timestamp" not in raw:
        raise TrafficPatternError(f"{loc}: missing required field 'timestamp'")
    if "nbytes" not in raw:
        raise TrafficPatternError(f"{loc}: missing required field 'nbytes'")

    timestamp = _parse_timestamp(raw["timestamp"], loc=loc)
    nbytes = _parse_nbytes(raw["nbytes"], loc=loc)
    _parse_optional_tags(raw, loc=loc)

    return timestamp, nbytes


def _parse_period(raw: Any, *, source: str, index: int) -> Tuple[float, float]:
    loc = f"{source}: period {index}"
    if not isinstance(raw, dict):
        raise TrafficPatternError(f"{loc}: must be a JSON object")

    if "duration" not in raw:
        raise TrafficPatternError(f"{loc}: missing required field 'duration'")
    if "bitrate" not in raw:
        raise TrafficPatternError(f"{loc}: missing required field 'bitrate'")

    duration = _parse_duration(raw["duration"], loc=loc)
    bitrate = _parse_bitrate(raw["bitrate"], loc=loc)
    _parse_optional_tags(raw, loc=loc)

    return duration, bitrate


def _parse_optional_tags(raw: Dict[str, Any], *, loc: str) -> None:
    if "tags" not in raw:
        return
    tags = raw["tags"]
    if not isinstance(tags, list):
        raise TrafficPatternError(f"{loc}: 'tags' must be a JSON array")
    for j, tag in enumerate(tags):
        if not isinstance(tag, str):
            raise TrafficPatternError(
                f"{loc}: tags[{j}] must be a string, got {tag!r}"
            )


def _parse_timestamp(value: Any, *, loc: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrafficPatternError(
            f"{loc}: 'timestamp' must be a number, got {value!r}"
        )
    timestamp = float(value)
    if timestamp < 0:
        raise TrafficPatternError(
            f"{loc}: 'timestamp' must be >= 0, got {timestamp}"
        )
    return timestamp


def _parse_duration(value: Any, *, loc: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrafficPatternError(
            f"{loc}: 'duration' must be a number, got {value!r}"
        )
    duration = float(value)
    if duration <= 0:
        raise TrafficPatternError(
            f"{loc}: 'duration' must be > 0, got {duration}"
        )
    return duration


def _parse_bitrate(value: Any, *, loc: str) -> float:
    if isinstance(value, bool):
        raise TrafficPatternError(
            f"{loc}: 'bitrate' must be a number or string, got {value!r}"
        )
    if isinstance(value, (int, float)):
        bitrate = float(value)
        if bitrate < 0:
            raise TrafficPatternError(
                f"{loc}: 'bitrate' must be >= 0, got {bitrate}"
            )
        return bitrate
    if isinstance(value, str):
        try:
            parsed = parse_bandwidth(value)
        except Exception as e:
            raise TrafficPatternError(
                f"{loc}: invalid bitrate {value!r}"
            ) from e
        if parsed is None:
            raise TrafficPatternError(
                f"{loc}: 'bitrate' must be a number or string, got {value!r}"
            )
        if parsed < 0:
            raise TrafficPatternError(
                f"{loc}: 'bitrate' must be >= 0, got {parsed}"
            )
        return float(parsed)
    raise TrafficPatternError(
        f"{loc}: 'bitrate' must be a number or string, got {value!r}"
    )


def _parse_nbytes(value: Any, *, loc: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrafficPatternError(
            f"{loc}: 'nbytes' must be an integer, got {value!r}"
        )
    if isinstance(value, float) and not value.is_integer():
        raise TrafficPatternError(
            f"{loc}: 'nbytes' must be an integer, got {value!r}"
        )
    nbytes = int(value)
    if nbytes < 0:
        raise TrafficPatternError(
            f"{loc}: 'nbytes' must be >= 0, got {nbytes}"
        )
    return nbytes
