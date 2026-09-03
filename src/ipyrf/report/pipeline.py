from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .analysis import analyze
from .export import write_images
from .html import write_html
from .loader import load_pattern_info, load_record, pattern_from_record_metadata
from .data import ReportData


def generate_report(
    record_path: Union[str, Path],
    output_path: Union[str, Path],
    *,
    traffic_pattern_path: Optional[Union[str, Path]] = None,
    json_path: Optional[Union[str, Path]] = None,
    png_dir: Optional[Union[str, Path]] = None,
    svg_dir: Optional[Union[str, Path]] = None,
    bin_ms: Optional[float] = None,
    title: Optional[str] = None,
    clock_offset_ms: float = 0.0,
) -> ReportData:
    """Load a ``--record`` CSV, analyze it, and write HTML (and optional exports)."""
    packets = load_record(record_path)
    if traffic_pattern_path is not None:
        pattern = load_pattern_info(traffic_pattern_path)
    else:
        pattern = pattern_from_record_metadata(packets.metadata)
    bin_ns = None
    if bin_ms is not None:
        bin_ns = max(1, int(float(bin_ms) * 1e6))
    data = analyze(
        packets,
        pattern,
        bin_ns=bin_ns,
        clock_offset_ms=clock_offset_ms,
    )
    write_html(data, output_path, title=title)
    if json_path is not None:
        Path(json_path).write_text(data.to_json(), encoding="utf-8")
    if png_dir is not None:
        write_images(data, png_dir, "png")
    if svg_dir is not None:
        write_images(data, svg_dir, "svg")
    return data
