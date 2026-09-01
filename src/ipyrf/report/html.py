from __future__ import annotations

import html
from pathlib import Path
from typing import Optional, Union

from ..utils import human_bps, human_readable_bytes
from .data import ReportData, Summary
from .plots import PLOT_CONFIG, build_html_figures, require_plotly

# Hover / glossary copy. Keep sentences short; they appear in tooltips.
TIPS = {
    "duration": (
        "Time from the first received packet to the last. "
        "This is the receiver's timeline, not the configured --time."
    ),
    "throughput": (
        "Average receive rate for the whole test: total bits divided by duration."
    ),
    "bytes": "Total UDP datagram size recorded (payload including ipyrf headers).",
    "packets": "Number of datagrams written to the packet-record CSV.",
    "loss": (
        "Sequence numbers between the first and last received packet that never "
        "arrived. Loss % is lost / (last_seq - first_seq + 1)."
    ),
    "reordered": (
        "Packets that arrived after a higher sequence number had already been "
        "received. The late packet is counted, not the early one."
    ),
    "p50": (
        "Median one-way latency: half of the packets were this fast or faster. "
        "Latency is received_ns minus transmitted_ns; clocks must be in sync."
    ),
    "p95": (
        "95th percentile latency: 95% of packets were at or below this value; "
        "the slowest 5% were worse."
    ),
    "p99": (
        "99th percentile latency: 99% of packets were at or below this value; "
        "the slowest 1% were worse."
    ),
    "ecdf": (
        "Empirical Cumulative Distribution Function. At latency x, the curve "
        "is the fraction of packets whose latency was x or less. It crosses "
        "0.50 / 0.95 / 0.99 at p50 / p95 / p99."
    ),
    "histogram": (
        "How many packets fell into each latency bucket. A tall bar is a common "
        "latency; a long right tail is occasional delay."
    ),
    "event_id": (
        "1-based index of the traffic-pattern event or piecewise-rate period "
        "that produced this packet. 0 means no pattern was used."
    ),
    "tags": "Optional labels from the traffic-pattern JSON (for example I, P, or B frames).",
    "expected": (
        "Bytes this event should have produced according to the traffic-pattern "
        "file (one loop). Compare with the received bytes column."
    ),
    "bin": (
        "Packets are summed into time buckets of this width to build the "
        "throughput, latency, and loss series. Override with --bin-ms."
    ),
}

FIGURE_BLURBS = {
    "overview": (
        "Shared time axis from the first received packet. Drag to pan, scroll "
        "or use the toolbar to zoom; double-click a plot to reset. Shaded "
        "bands mark traffic-pattern events when event_id is set."
    ),
    "latency_distribution": (
        "Left: how often each latency occurred. Right: the "
        '{ecdf} — read a latency on the x-axis and the y-axis says what '
        "share of packets were that fast or faster. Vertical dotted lines "
        "are {p50}, {p95}, and {p99}."
    ),
    "send_vs_receive_spacing": (
        "Each point is a consecutive sequence pair: time between those two "
        "packets at the sender (x) versus at the receiver (y). Points on the "
        "dashed line kept their spacing. Points above the line were stretched "
        "by the path (jitter or buffering); points below were compressed."
    ),
}

OVERVIEW_ROWS = (
    ("Traffic events", "event_id or pattern tag active at that time."),
    ("Throughput", "Received bits per second in each time bin."),
    ("Latency", "Per-bin {p50} / {p95} / {p99} one-way delay."),
    ("Packet loss", "Missing sequence numbers mapped onto the receive timeline."),
    (
        "Sequence / reordering",
        "Sequence number versus receive time. A smooth rise is in-order "
        "delivery; red marks are {reordered} packets.",
    ),
    (
        "Send vs receive spacing",
        "Median gap between consecutive packets at send versus receive. "
        "When the receive line fans out, the network is adding jitter.",
    ),
)


def write_html(
    data: ReportData,
    path: Union[str, Path],
    *,
    title: Optional[str] = None,
) -> Path:
    """Write a self-contained interactive HTML report (Plotly.js inlined)."""
    require_plotly()
    path = Path(path)
    figures = build_html_figures(data)
    report_title = title or _default_title(data)
    sections = []
    include_js: Union[bool, str] = True
    for name, fig in figures.items():
        heading = _figure_heading(name)
        plot_html = fig.to_html(
            include_plotlyjs=include_js,
            full_html=False,
            config=PLOT_CONFIG,
        )
        include_js = False
        extra = _overview_legend() if name == "overview" else ""
        sections.append(
            f'<section class="plot" id="{html.escape(name)}">'
            f"<h2>{html.escape(heading)}</h2>"
            f"{_blurb_html(name)}"
            f"{extra}"
            f"{plot_html}</section>"
        )

    document = _DOCUMENT.format(
        title=html.escape(report_title),
        subtitle=_subtitle(data.summary),
        kpis=_kpi_cards(data.summary),
        intro=_intro_html(),
        warnings=_warnings(data.summary.warnings),
        events=_events_table(data.events),
        glossary=_glossary_html(),
        footer=_footer(data.summary),
    )
    document = document.replace("<!--PLOTS-->", "\n".join(sections), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path


def _default_title(data: ReportData) -> str:
    name = data.summary.pattern_name
    if name:
        return f"ipyrf report — {name}"
    source = Path(data.summary.source).name if data.summary.source else "packet record"
    return f"ipyrf report — {source}"


def _figure_heading(name: str) -> str:
    return {
        "overview": "Overview",
        "latency_distribution": "Latency distribution",
        "send_vs_receive_spacing": "Send spacing vs receive spacing",
    }.get(name, name.replace("_", " ").title())


def _subtitle(summary: Summary) -> str:
    parts = [html.escape(Path(summary.source).name or "packet record")]
    if summary.pattern_source:
        label = summary.pattern_type or "traffic pattern"
        name = Path(summary.pattern_source).name
        parts.append(f"{html.escape(label)}: {html.escape(name)}")
    parts.append(_term(f"bin {summary.bin_ms:g} ms", TIPS["bin"]))
    return " · ".join(parts)


def _kpi_cards(summary: Summary) -> str:
    cards = [
        ("Duration", f"{summary.duration_s:.3f} s", TIPS["duration"]),
        ("Throughput", human_bps(summary.bits_per_second), TIPS["throughput"]),
        ("Bytes", human_readable_bytes(summary.bytes), TIPS["bytes"]),
        ("Packets", f"{summary.packets:,}", TIPS["packets"]),
        (
            "Loss",
            f"{summary.lost_packets:,} ({summary.lost_percent:.2f}%)",
            TIPS["loss"],
        ),
        ("Reordered", f"{summary.reordered_packets:,}", TIPS["reordered"]),
        ("Latency p50", _fmt_ms(summary.latency_p50_ms), TIPS["p50"]),
        ("Latency p95", _fmt_ms(summary.latency_p95_ms), TIPS["p95"]),
        ("Latency p99", _fmt_ms(summary.latency_p99_ms), TIPS["p99"]),
    ]
    items = []
    for label, value, tip in cards:
        items.append(
            f'<div class="kpi tip" tabindex="0" data-tip="{_attr(tip)}">'
            f'<div class="label">{html.escape(label)}</div>'
            f'<div class="value">{html.escape(value)}</div>'
            "</div>"
        )
    return "".join(items)


def _intro_html() -> str:
    return (
        '<section class="intro">'
        "<h2>About this report</h2>"
        "<p>Each chart is built from the UDP packet-record CSV: sequence "
        "number, size, send and receive timestamps, and optional "
        f"{_term('event_id', TIPS['event_id'])}. One-way latency is "
        "receive time minus send time.</p>"
        "<p>Hover a highlighted term or a KPI tile for a definition. "
        "Pan and zoom the plots; double-click to reset. A fuller "
        '<a href="#glossary">glossary</a> is at the bottom.</p>'
        "</section>"
    )


def _blurb_html(name: str) -> str:
    text = FIGURE_BLURBS.get(name)
    if not text:
        return ""
    return f'<p class="blurb">{_expand_terms(text)}</p>'


def _overview_legend() -> str:
    items = []
    for title, detail in OVERVIEW_ROWS:
        items.append(
            "<li><strong>"
            f"{html.escape(title)}.</strong> {_expand_terms(detail)}</li>"
        )
    return f'<ul class="row-help">{"".join(items)}</ul>'


def _warnings(warnings: list) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{html.escape(w)}</li>" for w in warnings)
    return f'<section class="warnings"><h2>Warnings</h2><ul>{items}</ul></section>'


def _events_table(events: list) -> str:
    if not events:
        return ""
    max_rows = 200
    rows = events[:max_rows]
    body = []
    for event in rows:
        tags = ", ".join(event.tags) if event.tags else "—"
        expected = (
            human_readable_bytes(event.expected_bytes)
            if event.expected_bytes is not None
            else "—"
        )
        body.append(
            "<tr>"
            f"<td>{event.event_id}</td>"
            f"<td>{html.escape(tags)}</td>"
            f"<td>{event.packets:,}</td>"
            f"<td>{human_readable_bytes(event.bytes)}</td>"
            f"<td>{html.escape(expected)}</td>"
            f"<td>{event.lost_packets:,}</td>"
            f"<td>{_fmt_ms(event.latency_p50_ms)}</td>"
            f"<td>{_fmt_ms(event.latency_p95_ms)}</td>"
            "</tr>"
        )
    note = ""
    if len(events) > max_rows:
        note = (
            f'<p class="note">Showing {max_rows} of {len(events)} events.</p>'
        )
    blurb = (
        '<p class="blurb">One row per '
        f"{_term('event_id', TIPS['event_id'])} seen in the recording. "
        "Lost packets are attributed to an event only when both neighbouring "
        "sequence numbers belong to it.</p>"
    )
    headers = "".join(
        _th(label, tip)
        for label, tip in (
            ("event_id", TIPS["event_id"]),
            ("tags", TIPS["tags"]),
            ("packets", TIPS["packets"]),
            ("bytes", TIPS["bytes"]),
            ("expected", TIPS["expected"]),
            ("lost", TIPS["loss"]),
            ("p50", TIPS["p50"]),
            ("p95", TIPS["p95"]),
        )
    )
    return (
        '<section class="events"><h2>Per-event stats</h2>'
        + blurb
        + "<table><thead><tr>"
        + headers
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
        + note
        + "</section>"
    )


def _glossary_html() -> str:
    entries = [
        ("ECDF", TIPS["ecdf"]),
        (
            "p50 / p95 / p99",
            "Latency percentiles. p50 is the median (half of packets were this "
            "fast or faster). p95 and p99: that fraction of packets were at or "
            "below this latency; the rest were slower.",
        ),
        (
            "Latency",
            "One-way delay: received timestamp minus transmitted timestamp. "
            "Sender and receiver clocks must be synchronized or values will "
            "be shifted (and can go negative).",
        ),
        ("Loss", TIPS["loss"]),
        ("Reordered", TIPS["reordered"]),
        ("event_id", TIPS["event_id"]),
        ("Time bin", TIPS["bin"]),
        ("Send vs receive spacing", FIGURE_BLURBS["send_vs_receive_spacing"]),
    ]
    items = []
    for term, definition in entries:
        items.append(
            f"<dt>{html.escape(term)}</dt>"
            f"<dd>{html.escape(definition)}</dd>"
        )
    return (
        f'<section class="glossary" id="glossary">'
        f"<h2>Glossary</h2>"
        f"<p class=\"blurb\">Short definitions for the metrics used above. "
        f"The same text appears if you hover a highlighted term.</p>"
        f"<dl>{''.join(items)}</dl>"
        f"</section>"
    )


def _footer(summary: Summary) -> str:
    extra = []
    if summary.pattern_name:
        extra.append(html.escape(summary.pattern_name))
    extra.append(f"{summary.packets:,} packets")
    extra.append(f"{summary.lost_packets:,} lost")
    return "Generated by ipyrf-report · " + " · ".join(extra)


def _expand_terms(text: str) -> str:
    """Replace ``{p50}``-style placeholders with hoverable terms."""
    mapping = {
        "ecdf": _term("ECDF", TIPS["ecdf"]),
        "p50": _term("p50", TIPS["p50"]),
        "p95": _term("p95", TIPS["p95"]),
        "p99": _term("p99", TIPS["p99"]),
        "reordered": _term("reordered", TIPS["reordered"]),
    }
    return text.format(**mapping)


def _term(label: str, tip: str) -> str:
    return (
        f'<span class="tip" tabindex="0" data-tip="{_attr(tip)}">'
        f"{html.escape(label)}</span>"
    )


def _th(label: str, tip: str) -> str:
    return (
        f'<th class="tip" tabindex="0" data-tip="{_attr(tip)}">'
        f"{html.escape(label)}</th>"
    )


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


def _fmt_ms(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if abs(value) < 0.01:
        return f"{value * 1000:.2f} µs"
    return f"{value:.3f} ms"


_DOCUMENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title}</title>
  <style>
    :root {{
      --bg: #f4f5f7;
      --card: #ffffff;
      --ink: #1b1f24;
      --muted: #5c6773;
      --accent: #1f4b99;
      --line: #d8dee6;
      --warn-bg: #fff6e5;
      --warn-ink: #8a5a00;
      --tip-bg: #152238;
      --tip-ink: #f4f7fb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      color: var(--ink);
      background: var(--bg);
    }}
    header {{
      background: #152238;
      color: #fff;
      padding: 1.5rem 2rem 1.75rem;
    }}
    header h1 {{
      margin: 0 0 0.35rem;
      font-size: 1.45rem;
      font-weight: 650;
    }}
    header .subtitle {{
      color: #c5d0e0;
      font-size: 0.92rem;
    }}
    .kpis {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 0.75rem;
      margin: 1.1rem 0 0;
    }}
    .kpi {{
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.12);
      border-radius: 8px;
      padding: 0.7rem 0.8rem;
    }}
    .kpi .label {{
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #b7c4d6;
    }}
    .kpi .value {{
      margin-top: 0.2rem;
      font-size: 1.05rem;
      font-variant-numeric: tabular-nums;
    }}
    main {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 1.25rem 1.25rem 2.5rem;
    }}
    section {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.75rem 1rem 1rem;
      margin: 1rem 0;
    }}
    h2 {{
      margin: 0.2rem 0 0.45rem;
      font-size: 1.05rem;
    }}
    .blurb, .row-help {{
      color: var(--muted);
      font-size: 0.9rem;
      line-height: 1.45;
      margin: 0 0 0.8rem;
    }}
    .row-help {{
      padding-left: 1.1rem;
    }}
    .row-help li {{
      margin: 0.2rem 0;
    }}
    .warnings {{
      background: var(--warn-bg);
      border-color: #f0d090;
      color: var(--warn-ink);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-variant-numeric: tabular-nums;
      font-size: 0.9rem;
    }}
    th, td {{
      text-align: left;
      padding: 0.4rem 0.5rem;
      border-bottom: 1px solid var(--line);
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .note, footer {{
      color: var(--muted);
      font-size: 0.85rem;
    }}
    footer {{
      text-align: center;
      padding: 0.5rem 0 1.5rem;
    }}
    dl {{
      margin: 0;
      display: grid;
      grid-template-columns: minmax(8rem, 14rem) 1fr;
      gap: 0.45rem 1rem;
      font-size: 0.9rem;
    }}
    dt {{
      font-weight: 650;
      color: var(--ink);
    }}
    dd {{
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }}
    a {{ color: var(--accent); }}
    .tip {{
      position: relative;
      cursor: help;
      border-bottom: 1px dotted currentColor;
    }}
    .kpi.tip {{
      border-bottom: 1px solid rgba(255,255,255,0.12);
    }}
    th.tip {{
      border-bottom: 1px solid var(--line);
    }}
    .tip:hover,
    .tip:focus {{
      outline: none;
    }}
    .tip:hover::after,
    .tip:focus::after {{
      content: attr(data-tip);
      position: absolute;
      left: 0;
      top: calc(100% + 0.4rem);
      z-index: 30;
      width: max-content;
      max-width: min(22rem, 70vw);
      padding: 0.55rem 0.7rem;
      border-radius: 6px;
      background: var(--tip-bg);
      color: var(--tip-ink);
      font-size: 0.78rem;
      font-weight: 400;
      letter-spacing: 0;
      text-transform: none;
      line-height: 1.4;
      white-space: normal;
      box-shadow: 0 6px 18px rgba(21, 34, 56, 0.28);
      pointer-events: none;
    }}
    header .tip:hover::after,
    header .tip:focus::after {{
      background: #fff;
      color: var(--ink);
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="subtitle">{subtitle}</div>
    <div class="kpis">{kpis}</div>
  </header>
  <main>
    {intro}
    {warnings}
    <!--PLOTS-->
    {events}
    {glossary}
  </main>
  <footer>{footer}</footer>
</body>
</html>
"""
