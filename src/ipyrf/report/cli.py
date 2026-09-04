from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from .loader import ReportError
from .pipeline import generate_report


def main() -> None:
    try:
        _main()
    except ReportError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    except KeyboardInterrupt:
        return


def _main() -> None:
    parser = argparse.ArgumentParser(
        prog="ipyrf-report",
        description=(
            "Generate a self-contained interactive HTML report from an "
            "ipyrf --record CSV (optional traffic-pattern JSON)."
        ),
    )
    parser.add_argument(
        "record",
        metavar="RECORD",
        help="Record CSV written by ipyrf --record",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="HTML output path (default: RECORD with a .html suffix)",
    )
    parser.add_argument(
        "--traffic-pattern",
        metavar="FILE",
        help="Traffic-pattern JSON used during the test (adds tags and shading)",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        dest="json_path",
        help="Also write analysis results as JSON",
    )
    parser.add_argument(
        "--png",
        nargs="?",
        const="",
        metavar="DIR",
        help=(
            "Export PNG plots to DIR (default: a png/ folder next to the HTML). "
            "Requires kaleido"
        ),
    )
    parser.add_argument(
        "--svg",
        nargs="?",
        const="",
        metavar="DIR",
        help=(
            "Export SVG plots to DIR (default: an svg/ folder next to the HTML). "
            "Requires kaleido"
        ),
    )
    parser.add_argument(
        "--bin-ms",
        type=_parse_bin_ms,
        metavar="MS",
        help="Time-series bin size in milliseconds (default: auto from duration)",
    )
    parser.add_argument(
        "--title",
        help="Report title (default: derived from the record / pattern name)",
    )
    parser.add_argument(
        "--clock-offset",
        type=_parse_clock_offset,
        default=0.0,
        metavar="MS",
        help=(
            "Add this many milliseconds to one-way latency. Use the value "
            "printed by 'ipyrf offset' when run on the receiving host "
            "against the sender"
        ),
    )
    args = parser.parse_args()

    record = Path(args.record)
    output = Path(args.output) if args.output else record.with_suffix(".html")
    png_dir = _optional_dir(args.png, output.parent / "png")
    svg_dir = _optional_dir(args.svg, output.parent / "svg")

    generate_report(
        record,
        output,
        traffic_pattern_path=args.traffic_pattern,
        json_path=args.json_path,
        png_dir=png_dir,
        svg_dir=svg_dir,
        bin_ms=args.bin_ms,
        title=args.title,
        clock_offset_ms=args.clock_offset,
    )
    print(f"Wrote {output}")
    if args.json_path:
        print(f"Wrote {args.json_path}")
    if png_dir is not None:
        print(f"Wrote PNG plots in {png_dir}")
    if svg_dir is not None:
        print(f"Wrote SVG plots in {svg_dir}")


def _parse_clock_offset(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {value!r}") from None


def _parse_bin_ms(value: str) -> float:
    try:
        bin_ms = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {value!r}") from None
    if bin_ms <= 0:
        raise argparse.ArgumentTypeError("bin-ms must be > 0")
    return bin_ms


def _optional_dir(value: Optional[str], default: Path) -> Optional[Path]:
    if value is None:
        return None
    if value == "":
        return default
    return Path(value)
