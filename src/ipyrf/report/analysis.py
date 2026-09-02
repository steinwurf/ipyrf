from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .data import (
    REPORT_VERSION,
    Ecdf,
    EventSpan,
    EventStats,
    Histogram,
    LossRun,
    PacketTrace,
    PatternInfo,
    PointSeries,
    ReorderEvent,
    ReportData,
    SpacingPair,
    Summary,
    TimeBin,
)

MAX_BINS = 5000
MAX_SCATTER_POINTS = 20000
MAX_LISTED_GAPS = 10000
ECDF_POINTS = 2000
HISTOGRAM_BINS = 60
MIN_BIN_NS = 1_000_000  # 1 ms
MAX_BIN_NS = 1_000_000_000  # 1 s


def analyze(
    packets: PacketTrace,
    pattern: Optional[PatternInfo] = None,
    bin_ns: Optional[int] = None,
    clock_offset_ms: float = 0.0,
) -> ReportData:
    """Compute report series and summary statistics from a packet trace.

    ``clock_offset_ms`` is added to every one-way latency (received minus
    transmitted). Pass the ``ipyrf offset`` value measured from the
    receiving host to the sending host.
    """
    n = len(packets)
    sequence = packets.sequence
    size_bytes = packets.size_bytes
    transmitted_ns = packets.transmitted_ns
    received_ns = packets.received_ns
    event_id = packets.event_id

    t0 = min(received_ns)
    t1 = max(received_ns)
    duration_ns = t1 - t0
    if duration_ns <= 0:
        duration_ns = max(transmitted_ns) - min(transmitted_ns)
    if duration_ns <= 0:
        duration_ns = 1

    if bin_ns is None:
        bin_ns = _default_bin_ns(duration_ns)
    if bin_ns < 1:
        raise ValueError("bin_ns must be >= 1")

    n_bins = int((duration_ns + bin_ns - 1) // bin_ns) or 1
    if n_bins > MAX_BINS:
        bin_ns = duration_ns // MAX_BINS + 1
        n_bins = int((duration_ns + bin_ns - 1) // bin_ns) or 1

    bin_bytes = [0] * n_bins
    bin_packets = [0] * n_bins
    bin_lost = [0] * n_bins
    bin_latencies: List[List[float]] = [[] for _ in range(n_bins)]
    bin_send_gaps: List[List[float]] = [[] for _ in range(n_bins)]
    bin_recv_gaps: List[List[float]] = [[] for _ in range(n_bins)]

    latencies_ms: List[float] = []
    negative_latency = 0
    total_bytes = 0
    offset_ms = float(clock_offset_ms or 0.0)

    def bin_index(ts_ns: int) -> int:
        idx = (ts_ns - t0) // bin_ns
        if idx < 0:
            return 0
        if idx >= n_bins:
            return n_bins - 1
        return int(idx)

    for i in range(n):
        total_bytes += size_bytes[i]
        idx = bin_index(received_ns[i])
        bin_bytes[idx] += size_bytes[i]
        bin_packets[idx] += 1
        latency_ms = (received_ns[i] - transmitted_ns[i]) / 1e6 + offset_ms
        latencies_ms.append(latency_ms)
        bin_latencies[idx].append(latency_ms)
        if latency_ms < 0:
            negative_latency += 1

    unique_counts = Counter(sequence)
    unique_sequences = len(unique_counts)
    duplicate_packets = n - unique_sequences
    min_seq = min(sequence)
    max_seq = max(sequence)
    expected = max_seq - min_seq + 1
    lost_count = max(0, expected - unique_sequences)

    by_seq: Dict[int, int] = {}
    for i in range(n):
        seq = sequence[i]
        if seq not in by_seq:
            by_seq[seq] = i
    received_sorted = sorted(by_seq)

    lost_seqs: List[int] = []
    if lost_count:
        received_set = set(received_sorted)
        for seq in range(min_seq, max_seq + 1):
            if seq not in received_set:
                lost_seqs.append(seq)

    loss_runs: List[LossRun] = []
    for first, count, t_ns in _collapse_runs(lost_seqs, by_seq, received_ns, received_sorted):
        if len(loss_runs) < MAX_LISTED_GAPS:
            t_s = None if t_ns is None else (t_ns - t0) / 1e9
            loss_runs.append(LossRun(first_sequence=first, count=count, t_s=t_s))
        if t_ns is not None:
            bin_lost[bin_index(t_ns)] += count

    rx_order = sorted(range(n), key=lambda i: (received_ns[i], sequence[i]))
    max_seen = -1
    reordered_indices: List[int] = []
    reorder_events: List[ReorderEvent] = []
    for i in rx_order:
        seq = sequence[i]
        if max_seen >= 0 and seq < max_seen:
            reordered_indices.append(i)
            if len(reorder_events) < MAX_LISTED_GAPS:
                reorder_events.append(
                    ReorderEvent(
                        sequence=seq,
                        t_s=(received_ns[i] - t0) / 1e9,
                        max_seen=max_seen,
                    )
                )
        if seq > max_seen:
            max_seen = seq

    seq_order = sorted(range(n), key=lambda i: (sequence[i], transmitted_ns[i]))
    spacing: List[SpacingPair] = []
    for prev, cur in zip(seq_order, seq_order[1:]):
        if sequence[cur] != sequence[prev] + 1:
            continue
        send_gap_us = (transmitted_ns[cur] - transmitted_ns[prev]) / 1e3
        recv_gap_us = (received_ns[cur] - received_ns[prev]) / 1e3
        t_s = (received_ns[cur] - t0) / 1e9
        if len(spacing) < MAX_SCATTER_POINTS * 4:
            spacing.append(
                SpacingPair(
                    t_s=t_s,
                    sequence=sequence[cur],
                    send_gap_us=send_gap_us,
                    recv_gap_us=recv_gap_us,
                )
            )
        idx = bin_index(received_ns[cur])
        bin_send_gaps[idx].append(send_gap_us)
        bin_recv_gaps[idx].append(recv_gap_us)

    if len(spacing) > MAX_SCATTER_POINTS:
        spacing = _downsample_list(spacing, MAX_SCATTER_POINTS)

    bins: List[TimeBin] = []
    for i in range(n_bins):
        start_ns = t0 + i * bin_ns
        end_ns = min(t0 + (i + 1) * bin_ns, t0 + duration_ns)
        width_s = max((end_ns - start_ns) / 1e9, bin_ns / 1e9)
        lat = bin_latencies[i]
        sorted_lat = sorted(lat) if lat else []
        bins.append(
            TimeBin(
                t_start_s=(start_ns - t0) / 1e9,
                t_end_s=(end_ns - t0) / 1e9,
                bits_per_second=(bin_bytes[i] * 8.0) / width_s,
                bytes=bin_bytes[i],
                packets=bin_packets[i],
                lost_packets=bin_lost[i],
                latency_p50_ms=_percentile(sorted_lat, 50),
                latency_p95_ms=_percentile(sorted_lat, 95),
                latency_p99_ms=_percentile(sorted_lat, 99),
                latency_mean_ms=(sum(lat) / len(lat)) if lat else None,
                latency_min_ms=sorted_lat[0] if sorted_lat else None,
                latency_max_ms=sorted_lat[-1] if sorted_lat else None,
                send_gap_p50_us=_percentile(sorted(bin_send_gaps[i]), 50)
                if bin_send_gaps[i]
                else None,
                recv_gap_p50_us=_percentile(sorted(bin_recv_gaps[i]), 50)
                if bin_recv_gaps[i]
                else None,
            )
        )

    seq_keep = _downsample_indices(n, MAX_SCATTER_POINTS, extra=reordered_indices)
    sequences = PointSeries(
        t_s=[(received_ns[i] - t0) / 1e9 for i in seq_keep],
        values=[float(sequence[i]) for i in seq_keep],
        extra=[sequence[i] for i in seq_keep],
    )
    reordered = PointSeries(
        t_s=[(received_ns[i] - t0) / 1e9 for i in reordered_indices],
        values=[float(sequence[i]) for i in reordered_indices],
        extra=[sequence[i] for i in reordered_indices],
    )

    sorted_lat_all = sorted(latencies_ms)
    duration_s = duration_ns / 1e9
    bits_per_second = (total_bytes * 8.0) / duration_s
    lost_percent = 100.0 * lost_count / expected if expected else 0.0

    warnings: List[str] = []
    if negative_latency:
        warnings.append(
            f"{negative_latency} packet(s) have negative latency; "
            "sender and receiver clocks may be unsynchronized"
        )
    if duplicate_packets:
        warnings.append(f"{duplicate_packets} duplicate sequence number(s)")

    pattern_by_id = {event.event_id: event for event in (pattern.events if pattern else [])}
    event_spans = _event_spans(packets, t0, pattern_by_id)
    events = _event_stats(
        packets,
        t0,
        pattern_by_id,
        lost_seqs,
        latencies_ms,
        event_spans,
    )
    unique_event_ids = {eid for eid in event_id if eid != 0}
    meta = packets.metadata or {}
    protocol = _optional_str(meta.get("protocol"))
    sequence_kind = _optional_str(meta.get("sequence"))
    show_loss = protocol != "tcp" and sequence_kind != "receive_order"

    pattern_name = None
    pattern_type = None
    pattern_source = None
    if pattern is not None:
        pattern_source = pattern.source
        pattern_type = pattern.pattern_type
        name = pattern.metadata.get("name")
        if name is not None:
            pattern_name = str(name)
        elif pattern.metadata.get("generator"):
            pattern_name = str(pattern.metadata.get("generator"))
        elif meta.get("traffic_pattern_name"):
            pattern_name = str(meta["traffic_pattern_name"])

    if not show_loss:
        warnings = [
            w
            for w in warnings
            if "duplicate sequence" not in w
        ]

    summary = Summary(
        packets=n,
        bytes=total_bytes,
        duration_s=duration_s,
        bits_per_second=bits_per_second,
        unique_sequences=unique_sequences,
        first_sequence=min_seq,
        last_sequence=max_seq,
        lost_packets=lost_count if show_loss else 0,
        lost_percent=lost_percent if show_loss else 0.0,
        duplicate_packets=duplicate_packets if show_loss else 0,
        reordered_packets=len(reordered_indices) if show_loss else 0,
        latency_min_ms=sorted_lat_all[0] if sorted_lat_all else None,
        latency_max_ms=sorted_lat_all[-1] if sorted_lat_all else None,
        latency_mean_ms=(sum(sorted_lat_all) / len(sorted_lat_all))
        if sorted_lat_all
        else None,
        latency_p50_ms=_percentile(sorted_lat_all, 50),
        latency_p95_ms=_percentile(sorted_lat_all, 95),
        latency_p99_ms=_percentile(sorted_lat_all, 99),
        negative_latency_count=negative_latency,
        event_ids=len(unique_event_ids),
        bin_ms=bin_ns / 1e6,
        source=packets.source,
        pattern_source=pattern_source,
        pattern_type=pattern_type,
        pattern_name=pattern_name,
        protocol=protocol,
        sequence_kind=sequence_kind,
        record_unit=_optional_str(meta.get("record_unit")),
        receiver=_optional_str(meta.get("receiver")),
        sender=_optional_str(meta.get("sender")),
        reverse=bool(meta.get("reverse")),
        role=_optional_str(meta.get("role")),
        bandwidth_bps=_optional_float(meta.get("bandwidth_bps")),
        configured_duration_s=_optional_float(meta.get("duration_s")),
        payload_len=_optional_int(meta.get("payload_len")),
        stop_reason=_optional_str(meta.get("stop_reason")),
        congestion_control=_optional_str(meta.get("congestion_control")),
        show_loss=show_loss,
        clock_offset_ms=offset_ms,
        warnings=warnings,
    )

    return ReportData(
        version=REPORT_VERSION,
        summary=summary,
        bins=bins,
        event_spans=event_spans,
        events=events,
        sequences=sequences,
        reordered=reordered,
        spacing=spacing,
        latency_histogram=_histogram(sorted_lat_all),
        latency_ecdf=_ecdf(sorted_lat_all),
        loss_runs=loss_runs if show_loss else [],
        reorder_events=reorder_events if show_loss else [],
    )


def _default_bin_ns(duration_ns: int) -> int:
    target = duration_ns / 500.0
    bin_ns = int(max(MIN_BIN_NS, min(MAX_BIN_NS, target)))
    return max(bin_ns, 1)


def _percentile(sorted_values: Sequence[float], p: float) -> Optional[float]:
    if not sorted_values:
        return None
    if p <= 0:
        return float(sorted_values[0])
    if p >= 100:
        return float(sorted_values[-1])
    rank = (len(sorted_values) - 1) * (p / 100.0)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    if low == high:
        return float(sorted_values[low])
    frac = rank - low
    return float(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac)


def _downsample_indices(
    n: int, max_points: int, extra: Optional[Sequence[int]] = None
) -> List[int]:
    if n <= max_points:
        return list(range(n))
    step = n / float(max_points)
    chosen = {int(i * step) for i in range(max_points)}
    chosen.add(n - 1)
    if extra:
        chosen.update(extra)
    return sorted(i for i in chosen if 0 <= i < n)


def _downsample_list(items: List, max_points: int) -> List:
    if len(items) <= max_points:
        return items
    step = len(items) / float(max_points)
    indices = sorted({int(i * step) for i in range(max_points)} | {len(items) - 1})
    return [items[i] for i in indices]


def _collapse_runs(
    lost_seqs: Sequence[int],
    by_seq: Dict[int, int],
    received_ns: Sequence[int],
    received_sorted: Sequence[int],
) -> List[Tuple[int, int, Optional[int]]]:
    runs: List[Tuple[int, int, Optional[int]]] = []
    if not lost_seqs:
        return runs
    start = lost_seqs[0]
    prev = start
    for seq in lost_seqs[1:]:
        if seq == prev + 1:
            prev = seq
            continue
        t_ns = _interpolate_time(start, by_seq, received_ns, received_sorted)
        runs.append((start, prev - start + 1, t_ns))
        start = seq
        prev = seq
    t_ns = _interpolate_time(start, by_seq, received_ns, received_sorted)
    runs.append((start, prev - start + 1, t_ns))
    return runs


def _interpolate_time(
    seq: int,
    by_seq: Dict[int, int],
    received_ns: Sequence[int],
    received_sorted: Sequence[int],
) -> Optional[int]:
    pos = bisect.bisect_left(received_sorted, seq)
    prev_s = received_sorted[pos - 1] if pos > 0 else None
    next_s = received_sorted[pos] if pos < len(received_sorted) else None
    if prev_s is not None and next_s is not None:
        prev_rx = received_ns[by_seq[prev_s]]
        next_rx = received_ns[by_seq[next_s]]
        span = next_s - prev_s
        if span <= 0:
            return prev_rx
        frac = (seq - prev_s) / span
        return int(prev_rx + frac * (next_rx - prev_rx))
    if prev_s is not None:
        return received_ns[by_seq[prev_s]]
    if next_s is not None:
        return received_ns[by_seq[next_s]]
    return None


def _event_spans(
    packets: PacketTrace,
    t0: int,
    pattern_by_id: Dict[int, object],
) -> List[EventSpan]:
    n = len(packets)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: (packets.sequence[i], packets.received_ns[i]))
    spans: List[EventSpan] = []
    current_id = packets.event_id[order[0]]
    run = [order[0]]

    def flush() -> None:
        if current_id == 0 or not run:
            return
        rx_times = [packets.received_ns[i] for i in run]
        start_s = (min(rx_times) - t0) / 1e9
        end_s = (max(rx_times) - t0) / 1e9
        if end_s <= start_s:
            end_s = start_s + 0.0005
        pattern_event = pattern_by_id.get(current_id)
        tags = list(getattr(pattern_event, "tags", []) or [])
        spans.append(
            EventSpan(
                event_id=current_id,
                start_s=start_s,
                end_s=end_s,
                tags=tags,
            )
        )

    for i in order[1:]:
        eid = packets.event_id[i]
        if eid != current_id:
            flush()
            current_id = eid
            run = [i]
        else:
            run.append(i)
    flush()
    return spans


def _event_stats(
    packets: PacketTrace,
    t0: int,
    pattern_by_id: Dict[int, object],
    lost_seqs: Sequence[int],
    latencies_ms: Sequence[float],
    spans: Sequence[EventSpan],
) -> List[EventStats]:
    seq_event: Dict[int, int] = {}
    grouped: Dict[int, List[int]] = defaultdict(list)
    for i in range(len(packets)):
        eid = packets.event_id[i]
        if eid == 0:
            continue
        grouped[eid].append(i)
        seq_event.setdefault(packets.sequence[i], eid)

    event_loss: Dict[int, int] = defaultdict(int)
    for seq in lost_seqs:
        prev_e = seq_event.get(seq - 1)
        next_e = seq_event.get(seq + 1)
        if prev_e is not None and prev_e == next_e:
            event_loss[prev_e] += 1

    span_range: Dict[int, Tuple[float, float]] = {}
    for span in spans:
        lo, hi = span_range.get(span.event_id, (span.start_s, span.end_s))
        span_range[span.event_id] = (min(lo, span.start_s), max(hi, span.end_s))

    stats: List[EventStats] = []
    for eid in sorted(grouped):
        indices = grouped[eid]
        lats = sorted(latencies_ms[i] for i in indices)
        pattern_event = pattern_by_id.get(eid)
        expected_bytes = getattr(pattern_event, "nbytes", None)
        tags = list(getattr(pattern_event, "tags", []) or [])
        start_end = span_range.get(eid)
        stats.append(
            EventStats(
                event_id=eid,
                packets=len(indices),
                bytes=sum(packets.size_bytes[i] for i in indices),
                lost_packets=event_loss.get(eid, 0),
                latency_p50_ms=_percentile(lats, 50),
                latency_p95_ms=_percentile(lats, 95),
                latency_p99_ms=_percentile(lats, 99),
                latency_mean_ms=(sum(lats) / len(lats)) if lats else None,
                tags=tags,
                expected_bytes=expected_bytes,
                start_s=None if start_end is None else start_end[0],
                end_s=None if start_end is None else start_end[1],
            )
        )
    return stats


def _histogram(sorted_values: Sequence[float]) -> Histogram:
    if not sorted_values:
        return Histogram(edges=[], counts=[])
    lo = float(sorted_values[0])
    hi = float(sorted_values[-1])
    if hi <= lo:
        return Histogram(edges=[lo, lo], counts=[len(sorted_values)])
    n_bins = min(HISTOGRAM_BINS, max(1, len(sorted_values)))
    width = (hi - lo) / n_bins
    counts = [0] * n_bins
    for value in sorted_values:
        idx = int((value - lo) / width)
        if idx >= n_bins:
            idx = n_bins - 1
        elif idx < 0:
            idx = 0
        counts[idx] += 1
    edges = [lo + i * width for i in range(n_bins + 1)]
    return Histogram(edges=edges, counts=counts)


def _ecdf(sorted_values: Sequence[float]) -> Ecdf:
    n = len(sorted_values)
    if n == 0:
        return Ecdf(values=[], probabilities=[])
    if n <= ECDF_POINTS:
        return Ecdf(
            values=[float(v) for v in sorted_values],
            probabilities=[(i + 1) / n for i in range(n)],
        )
    values: List[float] = []
    probabilities: List[float] = []
    for k in range(ECDF_POINTS):
        i = int(k * (n - 1) / (ECDF_POINTS - 1))
        values.append(float(sorted_values[i]))
        probabilities.append((i + 1) / n)
    return Ecdf(values=values, probabilities=probabilities)


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
