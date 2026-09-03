from __future__ import annotations

from collections import OrderedDict
from typing import Iterable, List, Optional, Sequence, Tuple

from .data import EventSpan, ReportData, TimeBin

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError as e:  # pragma: no cover - installed via package deps
    go = None
    make_subplots = None
    _PLOTLY_IMPORT_ERROR = e
else:
    _PLOTLY_IMPORT_ERROR = None

PLOTLY_COLORS = [
    "#636EFA",
    "#EF553B",
    "#00CC96",
    "#AB63FA",
    "#FFA15A",
    "#19D3F3",
    "#FF6692",
    "#B6E880",
    "#FF97FF",
    "#FECB52",
]
TAG_COLORS = {
    "I": "#e74c3c",
    "P": "#3498db",
    "B": "#27ae60",
}
MAX_VRECTS = 200
HOVER = "x unified"
TEMPLATE = "plotly_white"

PLOT_CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
}


def require_plotly() -> None:
    if go is None:
        raise ImportError(
            "ipyrf-report requires plotly. Install it with: "
            "python3 -m pip install plotly"
        ) from _PLOTLY_IMPORT_ERROR


def build_html_figures(data: ReportData) -> "OrderedDict[str, go.Figure]":
    """Figures for the interactive HTML report (overview then details)."""
    require_plotly()
    figures: "OrderedDict[str, go.Figure]" = OrderedDict()
    figures["overview"] = _overview_figure(data)
    figures["latency_distribution"] = _distribution_figure(data)
    figures["send_vs_receive_spacing"] = _spacing_scatter_figure(data)
    return figures


def build_export_figures(data: ReportData) -> "OrderedDict[str, go.Figure]":
    """Individual figures for PNG/SVG export."""
    require_plotly()
    figures: "OrderedDict[str, go.Figure]" = OrderedDict()
    figures["throughput"] = _single_axis_figure(
        data,
        title="Throughput",
        ylabel="Throughput (Mbps)",
        traces=_throughput_traces(data.bins),
    )
    figures["latency"] = _single_axis_figure(
        data,
        title="Latency",
        ylabel="Latency (ms)",
        traces=_latency_traces(data.bins),
    )
    if data.summary.show_loss:
        figures["loss"] = _single_axis_figure(
            data,
            title="Packet loss",
            ylabel="Lost packets",
            traces=_loss_traces(data.bins),
            bar=True,
        )
        figures["sequence"] = _sequence_figure(data)
    figures["spacing"] = _single_axis_figure(
        data,
        title="Send spacing vs receive spacing",
        ylabel="Interarrival (µs)",
        traces=_spacing_traces(data.bins),
    )
    figures["latency_distribution"] = _distribution_figure(data)
    figures["send_vs_receive_spacing"] = _spacing_scatter_figure(data)
    return figures


def _overview_figure(data: ReportData) -> "go.Figure":
    has_events = any(span.event_id != 0 for span in data.event_spans)
    rows: List[Tuple[str, List, str, bool]] = []
    if has_events:
        rows.append(
            ("Traffic events", _event_lane_traces(data), "Event", False)
        )
    rows.extend(
        [
            ("Throughput", _throughput_traces(data.bins), "Mbps", False),
            ("Latency", _latency_traces(data.bins), "ms", False),
        ]
    )
    if data.summary.show_loss:
        rows.append(("Packet loss", _loss_traces(data.bins), "lost pkts", True))
        rows.append(
            ("Sequence / reordering", _sequence_traces(data), "sequence", False)
        )
    rows.append(
        (
            "Send vs receive spacing",
            _spacing_traces(data.bins),
            "µs",
            False,
        )
    )

    n_rows = len(rows)
    first_time_row = 2 if has_events else 1
    fig = make_subplots(
        rows=n_rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=min(0.04, 0.12 / n_rows),
        subplot_titles=[title for title, _, _, _ in rows],
        row_heights=_row_heights(n_rows, event_row=has_events),
    )

    for row_i, (_, traces, ylabel, is_bar) in enumerate(rows, start=1):
        for trace in traces:
            fig.add_trace(trace, row=row_i, col=1)
        fig.update_yaxes(title_text=ylabel, row=row_i, col=1)
        if is_bar:
            fig.update_layout(barmode="overlay")

    _add_event_shading(
        fig,
        data,
        rows=range(first_time_row, n_rows + 1),
        legend_row=first_time_row,
    )
    fig.update_xaxes(title_text="Time (s)", row=n_rows, col=1)
    fig.update_layout(
        template=TEMPLATE,
        hovermode=HOVER,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=70, r=30, t=80, b=50),
        height=240 * n_rows,
        showlegend=True,
    )
    return fig


def _row_heights(n_rows: int, event_row: bool) -> List[float]:
    weights = []
    remaining = n_rows
    if event_row:
        weights.append(0.7)
        remaining -= 1
    # throughput, latency, loss, sequence, spacing
    typical = [1.2, 1.1, 0.8, 1.3, 1.0]
    weights.extend(typical[:remaining])
    while len(weights) < n_rows:
        weights.append(1.0)
    total = sum(weights)
    return [w / total for w in weights]


def _single_axis_figure(
    data: ReportData,
    *,
    title: str,
    ylabel: str,
    traces: List,
    bar: bool = False,
) -> "go.Figure":
    fig = go.Figure(traces)
    _add_event_shading(fig, data)
    fig.update_layout(
        template=TEMPLATE,
        title=title,
        hovermode=HOVER,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=70, r=30, t=80, b=50),
        height=420,
        barmode="overlay" if bar else None,
    )
    fig.update_xaxes(title_text="Time (s)")
    fig.update_yaxes(title_text=ylabel)
    return fig


def _throughput_traces(bins: Sequence[TimeBin]) -> List:
    x = [_bin_center(b) for b in bins]
    y = [b.bits_per_second / 1e6 for b in bins]
    return [
        go.Scatter(
            x=x,
            y=y,
            name="Throughput",
            mode="lines",
            line=dict(color="#1f77b4", width=2),
            fill="tozeroy",
            fillcolor="rgba(31, 119, 180, 0.18)",
            hovertemplate="t=%{x:.4f}s<br>%{y:.3f} Mbps<extra>Throughput</extra>",
        )
    ]


def _latency_traces(bins: Sequence[TimeBin]) -> List:
    x = [_bin_center(b) for b in bins]
    traces = []
    for name, attr, color in (
        ("p50", "latency_p50_ms", "#2ca02c"),
        ("p95", "latency_p95_ms", "#ff7f0e"),
        ("p99", "latency_p99_ms", "#d62728"),
    ):
        traces.append(
            go.Scatter(
                x=x,
                y=[getattr(b, attr) for b in bins],
                name=f"Latency {name}",
                mode="lines",
                line=dict(color=color, width=2),
                connectgaps=True,
                hovertemplate="t=%{x:.4f}s<br>%{y:.3f} ms<extra>"
                + f"Latency {name}</extra>",
            )
        )
    return traces


def _loss_traces(bins: Sequence[TimeBin]) -> List:
    x = [_bin_center(b) for b in bins]
    y = [b.lost_packets for b in bins]
    return [
        go.Bar(
            x=x,
            y=y,
            name="Lost packets",
            marker_color="#c0392b",
            hovertemplate="t=%{x:.4f}s<br>%{y} lost<extra>Loss</extra>",
        )
    ]


def _spacing_traces(bins: Sequence[TimeBin]) -> List:
    x = [_bin_center(b) for b in bins]
    return [
        go.Scatter(
            x=x,
            y=[b.send_gap_p50_us for b in bins],
            name="Send spacing p50",
            mode="lines",
            line=dict(color="#8e44ad", width=2),
            connectgaps=True,
            hovertemplate="t=%{x:.4f}s<br>%{y:.1f} µs<extra>Send p50</extra>",
        ),
        go.Scatter(
            x=x,
            y=[b.recv_gap_p50_us for b in bins],
            name="Receive spacing p50",
            mode="lines",
            line=dict(color="#16a085", width=2, dash="dash"),
            connectgaps=True,
            hovertemplate="t=%{x:.4f}s<br>%{y:.1f} µs<extra>Receive p50</extra>",
        ),
    ]


def _sequence_traces(data: ReportData) -> List:
    traces = [
        go.Scatter(
            x=data.sequences.t_s,
            y=data.sequences.values,
            name="Sequence",
            mode="markers",
            marker=dict(size=4, color="#34495e", opacity=0.7),
            hovertemplate="t=%{x:.4f}s<br>seq=%{y}<extra>Sequence</extra>",
        )
    ]
    if data.reordered.t_s:
        traces.append(
            go.Scatter(
                x=data.reordered.t_s,
                y=data.reordered.values,
                name="Reordered",
                mode="markers",
                marker=dict(size=8, color="#e74c3c", symbol="x"),
                hovertemplate="t=%{x:.4f}s<br>seq=%{y}<extra>Reordered</extra>",
            )
        )
    return traces


def _sequence_figure(data: ReportData) -> "go.Figure":
    fig = go.Figure(_sequence_traces(data))
    _add_event_shading(fig, data)
    fig.update_layout(
        template=TEMPLATE,
        title="Sequence / reordering",
        hovermode=HOVER,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=70, r=30, t=80, b=50),
        height=420,
    )
    fig.update_xaxes(title_text="Time (s)")
    fig.update_yaxes(title_text="Sequence")
    return fig


def _event_lane_traces(data: ReportData) -> List:
    if _prefer_tag_lane(data.event_spans):
        tags = []
        times = []
        colors = []
        for span in data.event_spans:
            tag = span.primary_tag or f"event {span.event_id}"
            mid = (span.start_s + span.end_s) / 2.0
            times.append(mid)
            tags.append(tag)
            colors.append(_color_for_key(tag))
        return [
            go.Scatter(
                x=times,
                y=tags,
                name="Event tag",
                mode="markers",
                marker=dict(size=8, color=colors),
                hovertemplate="t=%{x:.4f}s<br>%{y}<extra>Event</extra>",
            )
        ]

    times = []
    ids = []
    for span in data.event_spans:
        times.append((span.start_s + span.end_s) / 2.0)
        ids.append(span.event_id)
    return [
        go.Scatter(
            x=times,
            y=ids,
            name="Event ID",
            mode="markers",
            marker=dict(size=7, color="#8e44ad"),
            hovertemplate="t=%{x:.4f}s<br>event_id=%{y}<extra>Event</extra>",
        )
    ]


def _distribution_figure(data: ReportData) -> "go.Figure":
    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=(
            "Latency histogram",
            "ECDF (share of packets at or below this latency)",
        ),
        horizontal_spacing=0.12,
    )
    hist = data.latency_histogram
    if hist.counts:
        centers = []
        widths = []
        for i, count in enumerate(hist.counts):
            left = hist.edges[i]
            right = hist.edges[i + 1] if i + 1 < len(hist.edges) else hist.edges[i]
            centers.append((left + right) / 2.0)
            widths.append(max(right - left, 1e-9))
        fig.add_trace(
            go.Bar(
                x=centers,
                y=hist.counts,
                width=widths,
                name="Count",
                marker_color="#2980b9",
                hovertemplate=(
                    "%{x:.3f} ms<br>%{y} packets in this bucket"
                    "<extra>Histogram</extra>"
                ),
            ),
            row=1,
            col=1,
        )
    ecdf = data.latency_ecdf
    if ecdf.values:
        fig.add_trace(
            go.Scatter(
                x=ecdf.values,
                y=ecdf.probabilities,
                name="ECDF",
                mode="lines",
                line=dict(color="#8e44ad", width=2),
                hovertemplate=(
                    "%{x:.3f} ms<br>"
                    "%{y:.1%} of packets had this latency or less"
                    "<extra>ECDF</extra>"
                ),
            ),
            row=1,
            col=2,
        )
        for p, label, color in (
            (data.summary.latency_p50_ms, "p50", "#2ca02c"),
            (data.summary.latency_p95_ms, "p95", "#ff7f0e"),
            (data.summary.latency_p99_ms, "p99", "#d62728"),
        ):
            if p is None:
                continue
            fig.add_vline(
                x=p,
                line_dash="dot",
                line_color=color,
                annotation_text=label,
                annotation_position="top",
                row=1,
                col=2,
            )
    fig.update_xaxes(title_text="Latency (ms)", row=1, col=1)
    fig.update_xaxes(title_text="Latency (ms)", row=1, col=2)
    fig.update_yaxes(title_text="Packets", row=1, col=1)
    fig.update_yaxes(
        title_text="Fraction of packets ≤ latency",
        range=[0, 1],
        row=1,
        col=2,
    )
    fig.update_layout(
        template=TEMPLATE,
        showlegend=False,
        height=420,
        margin=dict(l=60, r=30, t=60, b=50),
    )
    return fig


def _spacing_scatter_figure(data: ReportData) -> "go.Figure":
    send = [p.send_gap_us for p in data.spacing]
    recv = [p.recv_gap_us for p in data.spacing]
    fig = go.Figure(
        go.Scatter(
            x=send,
            y=recv,
            mode="markers",
            name="Packet pair",
            marker=dict(size=5, color="#34495e", opacity=0.45),
            hovertemplate=(
                "send=%{x:.1f} µs<br>recv=%{y:.1f} µs<br>"
                "above dashed line = extra delay between packets"
                "<extra>Packet pair</extra>"
            ),
        )
    )
    if send and recv:
        lo = min(min(send), min(recv))
        hi = max(max(send), max(recv))
        fig.add_trace(
            go.Scatter(
                x=[lo, hi],
                y=[lo, hi],
                mode="lines",
                name="equal spacing",
                line=dict(color="#e74c3c", width=1, dash="dash"),
                hoverinfo="skip",
            )
        )
    fig.update_layout(
        template=TEMPLATE,
        title="Send spacing vs receive spacing",
        height=480,
        margin=dict(l=70, r=30, t=60, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(title_text="Send spacing (µs)")
    fig.update_yaxes(title_text="Receive spacing (µs)")
    return fig


def _add_event_shading(
    fig: "go.Figure",
    data: ReportData,
    rows: Optional[Iterable[int]] = None,
    legend_row: Optional[int] = None,
) -> None:
    merged = _merged_shading_spans(data.event_spans)
    if not merged or len(merged) > MAX_VRECTS:
        return
    row_list = list(rows) if rows is not None else []
    axis_refs = (
        [_xy_refs_for_row(row) for row in row_list]
        if row_list
        else [("x", "y")]
    )
    shapes = list(fig.layout.shapes) if fig.layout.shapes else []
    for start, end, _key, color in merged:
        for xref, yref in axis_refs:
            shapes.append(
                dict(
                    type="rect",
                    xref=xref,
                    yref=f"{yref} domain",
                    x0=start,
                    x1=end,
                    y0=0,
                    y1=1,
                    fillcolor=color,
                    opacity=0.12,
                    line=dict(width=0),
                    layer="below",
                )
            )
    fig.update_layout(shapes=shapes)

    seen_keys = set()
    legend_traces = []
    for _start, _end, key, color in merged:
        if key in seen_keys:
            continue
        seen_keys.add(key)
        legend_traces.append(
            go.Scatter(
                x=[None],
                y=[None],
                mode="markers",
                marker=dict(size=10, color=color),
                name=key,
                hoverinfo="skip",
                showlegend=True,
            )
        )
    target_row = legend_row if legend_row is not None else (
        row_list[0] if row_list else None
    )
    for legend in legend_traces:
        if target_row is not None:
            fig.add_trace(legend, row=target_row, col=1)
        else:
            fig.add_trace(legend)


def _xy_refs_for_row(row: int) -> Tuple[str, str]:
    """Plotly subplot axis ids for a 1-indexed row in a single-column figure."""
    if row <= 1:
        return "x", "y"
    return f"x{row}", f"y{row}"


def _merged_shading_spans(
    spans: Sequence[EventSpan],
) -> List[Tuple[float, float, str, str]]:
    usable = [s for s in spans if s.event_id != 0]
    if not usable:
        return []
    use_tags = _prefer_tag_lane(usable)
    keyed: List[Tuple[float, float, str, str]] = []
    for span in sorted(usable, key=lambda s: (s.start_s, s.end_s)):
        key = span.primary_tag if use_tags and span.primary_tag else f"event {span.event_id}"
        keyed.append((span.start_s, span.end_s, key, _color_for_key(key)))
    if not keyed:
        return []
    merged = [keyed[0]]
    for start, end, key, color in keyed[1:]:
        prev_start, prev_end, prev_key, prev_color = merged[-1]
        if key == prev_key:
            merged[-1] = (prev_start, max(prev_end, end), prev_key, prev_color)
        else:
            merged.append((start, end, key, color))
    return merged


def _prefer_tag_lane(spans: Sequence[EventSpan]) -> bool:
    tags = {span.primary_tag for span in spans if span.primary_tag}
    return bool(tags) and len(tags) <= 8


def _color_for_key(key: str) -> str:
    if key in TAG_COLORS:
        return TAG_COLORS[key]
    idx = sum(ord(c) for c in key) % len(PLOTLY_COLORS)
    return PLOTLY_COLORS[idx]


def _bin_center(bin_: TimeBin) -> float:
    return (bin_.t_start_s + bin_.t_end_s) / 2.0
