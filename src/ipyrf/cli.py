#!/usr/bin/env python3

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .logger import Logger
from .utils import parse_bandwidth, parse_ip, tcp_congestion_control_info
from . import tcp, udp
from .interactive import InteractiveController
from .controllers import StaticPacingController, TrafficPatternController
from .handshake import ReverseConfig, reverse_trace_file_name
from .traffic_pattern import (
    LoopedTrafficPattern,
    TrafficPatternError,
    load_traffic_pattern,
)
from .video_trace import (
    VideoTraceError,
    generate_video_trace,
    generate_video_trace_from_ffprobe,
    load_ffprobe_json,
    run_ffprobe,
    write_trace_document,
)


def parse_existing_dir(path: str) -> str:
    directory = Path(path)
    if not directory.is_dir():
        raise argparse.ArgumentTypeError(f"not a directory: {path}")
    return str(directory)


def parse_loops(value: str) -> int:
    try:
        loops = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from None
    if loops < 1:
        raise argparse.ArgumentTypeError("loops must be >= 1")
    return loops


def _record_run_metadata(
    args,
    *,
    reverse: bool,
    reverse_trace_name,
    loops: int,
    pattern_path,
) -> dict:
    meta = {
        "protocol": args.protocol,
        "role": args.role,
        "reverse": bool(reverse),
        "sequence": "sender" if args.protocol == "udp" else "receive_order",
        "record_unit": "datagram" if args.protocol == "udp" else "tcp_record",
        "direction": "rx",
    }
    if reverse:
        if getattr(args, "bandwidth", None) is not None:
            meta["bandwidth_bps"] = args.bandwidth
        if pattern_path is None and getattr(args, "time", None) is not None:
            meta["duration_s"] = float(args.time)
        if loops != 1:
            meta["loops"] = loops
        if reverse_trace_name:
            meta["traffic_pattern_name"] = reverse_trace_name
        if pattern_path:
            try:
                with open(pattern_path, encoding="utf-8") as f:
                    meta["traffic_pattern"] = json.load(f)
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        if args.protocol == "udp" and getattr(args, "length", None) is not None:
            meta["payload_len"] = args.length
        if args.protocol == "tcp":
            if getattr(args, "congestion_control", None):
                meta["congestion_control"] = args.congestion_control
            if getattr(args, "set_mss", None):
                meta["mss"] = args.set_mss
    elif args.role == "server" and args.protocol == "tcp":
        if getattr(args, "congestion_control", None):
            meta["congestion_control"] = args.congestion_control
    return meta


def main():
    try:
        _main()
    except KeyboardInterrupt:
        return


def _main():
    p = argparse.ArgumentParser(description="Minimal iperf3-like tool (JSON output)")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", type=int, default=5201, help="Port number")
    common.add_argument(
        "--logfile", help="Write log messages to a file instead of stdout"
    )
    common.add_argument(
        "--json_log",
        help="Write the log messages as JSON.",
        action="store_true",
        default=False,
    )
    common.add_argument(
        "--record",
        metavar="PATH",
        dest="record_path",
        help="Write received records to a CSV file after the test",
    )
    common.add_argument(
        "--packet-record",
        dest="record_path",
        metavar="PATH",
        help=argparse.SUPPRESS,
    )

    subp = p.add_subparsers(dest="protocol", required=True)

    gen_parser = subp.add_parser(
        "generate", help="Generate traffic-pattern JSON files"
    )
    gen_sub = gen_parser.add_subparsers(dest="generate_kind", required=True)
    video_parser = gen_sub.add_parser(
        "video",
        help=(
            "Encoded-video-like trace (synthetic or from ffprobe); "
            "writes normal trace JSON"
        ),
    )
    video_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="FILE",
        help="Write the generated trace JSON to FILE",
    )
    video_source = video_parser.add_mutually_exclusive_group()
    video_source.add_argument(
        "--from",
        dest="from_media",
        metavar="MEDIA",
        help="Run ffprobe on MEDIA and convert video frames to a trace",
    )
    video_source.add_argument(
        "--ffprobe-json",
        metavar="FILE",
        help=(
            "Use a saved ffprobe -show_frames JSON dump instead of "
            "running ffprobe"
        ),
    )
    video_parser.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="Synthetic mode: frames per second (default: 30)",
    )
    video_parser.add_argument(
        "--gop",
        default="IPBB",
        help=(
            "Synthetic mode: GOP frame-type pattern using I/P/B "
            "characters (default: IPBB)"
        ),
    )
    video_parser.add_argument(
        "--i-size",
        type=int,
        default=40_000,
        metavar="BYTES",
        help="Synthetic mode: I-frame size in bytes (default: 40000)",
    )
    video_parser.add_argument(
        "--p-size",
        type=int,
        default=8_000,
        metavar="BYTES",
        help="Synthetic mode: P-frame size in bytes (default: 8000)",
    )
    video_parser.add_argument(
        "--b-size",
        type=int,
        default=2_000,
        metavar="BYTES",
        help="Synthetic mode: B-frame size in bytes (default: 2000)",
    )
    video_parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help=(
            "Synthetic mode: pattern length in seconds (default: 10). "
            "With --from/--ffprobe-json: optional truncate to this many "
            "seconds from the first frame"
        ),
    )

    tcp_parser = subp.add_parser("tcp", help="TCP mode")
    tcp_sub = tcp_parser.add_subparsers(dest="role", required=True)

    congestion_control = tcp_congestion_control_info()
    common_tcp = argparse.ArgumentParser(add_help=False)
    common_tcp.add_argument(
        "--congestion-control",
        choices=congestion_control.get("allowed", []),
        default=None,
        help=(
            argparse.SUPPRESS
            if congestion_control == {}
            else (
                "TCP: set congestion control algorithm "
                f"(default: system default '{congestion_control.get('current')}')"
            )
        ),
    )

    tcp_srv = tcp_sub.add_parser(
        "server", parents=[common, common_tcp], help="Run a TCP server"
    )
    tcp_srv.add_argument(
        "address", metavar="ADDRESS", type=parse_ip, help="Listen address"
    )
    tcp_srv.add_argument(
        "--trace-dir",
        metavar="DIR",
        type=parse_existing_dir,
        help=(
            "Directory containing traffic-pattern files for reverse tests "
            "(default: the server's working directory)"
        ),
    )

    tcp_cli = tcp_sub.add_parser(
        "client", parents=[common, common_tcp], help="Run a TCP client"
    )
    tcp_cli.add_argument(
        "--bandwidth",
        type=parse_bandwidth,
        help="Target bandwidth in bits per second, e.g., 50M",
    )
    tcp_cli.add_argument("address", metavar="ADDRESS", help="Server address to connect")
    tcp_cli.add_argument(
        "--set-mss", dest="set_mss", type=int, help="TCP: set TCP_MAXSEG (approx MSS)"
    )
    tcp_cli.add_argument(
        "--enable-latency",
        action="store_true",
        help="Enable latency tracking on the server",
    )
    tcp_cli.add_argument(
        "--bind-dev",
        metavar="DEVICE",
        help=(
            "Bind the client socket to DEVICE (Linux SO_BINDTODEVICE; "
            "typically requires CAP_NET_RAW or root)"
        ),
    )
    tcp_cli.add_argument(
        "-R",
        "--reverse",
        action="store_true",
        help=(
            "Server sends, client receives. Forwards --bandwidth and "
            "--time, or the name of --traffic-pattern, to the server. "
            "Cannot be combined with --interactive"
        ),
    )

    # Time, interactive mode, and traffic pattern are mutually exclusive
    tcp_time_group = tcp_cli.add_mutually_exclusive_group()
    tcp_time_group.add_argument(
        "--time", type=int, default=10, help="Test duration in seconds"
    )
    tcp_time_group.add_argument(
        "--interactive", action="store_true", help="Run client in interactive mode"
    )
    tcp_time_group.add_argument(
        "--traffic-pattern",
        metavar="FILE",
        help="JSON traffic pattern file (trace or piecewise_rate)",
    )
    tcp_cli.add_argument(
        "--loops",
        type=parse_loops,
        default=1,
        metavar="N",
        help=(
            "Repeat --traffic-pattern this many times (default: 1). "
            "Each repetition starts when the previous one ends"
        ),
    )

    udp_parser = subp.add_parser("udp", help="UDP mode")
    udp_sub = udp_parser.add_subparsers(dest="role", required=True)

    udp_srv = udp_sub.add_parser(
        "server", parents=[common], help="Run a UDP server"
    )
    udp_srv.add_argument(
        "address", metavar="ADDRESS", type=parse_ip, help="Listen address"
    )
    udp_srv.add_argument(
        "--inactivity-timeout",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Exit after this many seconds without receiving packets (default: 2.0)",
    )
    udp_srv.add_argument(
        "--trace-dir",
        metavar="DIR",
        type=parse_existing_dir,
        help=(
            "Directory containing traffic-pattern files for reverse tests "
            "(default: the server's working directory)"
        ),
    )

    udp_cli = udp_sub.add_parser(
        "client", parents=[common], help="Run a UDP client"
    )
    udp_cli.add_argument("address", metavar="ADDRESS", help="Server address to connect")
    udp_cli.add_argument(
        "--bandwidth", type=parse_bandwidth, help="Target bandwidth, e.g., 50M"
    )
    udp_cli.add_argument(
        "-l", dest="length", type=int, default=1200, help="UDP payload length"
    )
    udp_cli.add_argument(
        "--enable-latency",
        action="store_true",
        help="Enable latency tracking on the server",
    )
    udp_cli.add_argument(
        "--bind-dev",
        metavar="DEVICE",
        help=(
            "Bind the client socket to DEVICE (Linux SO_BINDTODEVICE; "
            "typically requires CAP_NET_RAW or root)"
        ),
    )
    udp_cli.add_argument(
        "-R",
        "--reverse",
        action="store_true",
        help=(
            "Server sends, client receives. Forwards --bandwidth, "
            "--time, and -l, or the name of --traffic-pattern, to the "
            "server. Cannot be combined with --interactive"
        ),
    )

    # Time, interactive mode, and traffic pattern are mutually exclusive
    udp_time_group = udp_cli.add_mutually_exclusive_group()
    udp_time_group.add_argument(
        "--time", type=int, default=10, help="Test duration in seconds"
    )
    udp_time_group.add_argument(
        "--interactive", action="store_true", help="Run client in interactive mode"
    )
    udp_time_group.add_argument(
        "--traffic-pattern",
        metavar="FILE",
        help="JSON traffic pattern file (trace or piecewise_rate)",
    )
    udp_cli.add_argument(
        "--loops",
        type=parse_loops,
        default=1,
        metavar="N",
        help=(
            "Repeat --traffic-pattern this many times (default: 1). "
            "Each repetition starts when the previous one ends"
        ),
    )

    args = p.parse_args()

    loops = getattr(args, "loops", 1)
    pattern_path = getattr(args, "traffic_pattern", None)
    reverse = getattr(args, "reverse", False)
    if loops != 1 and pattern_path is None:
        p.error("--loops requires --traffic-pattern")

    pattern = None
    if pattern_path is not None and not reverse:
        try:
            pattern = load_traffic_pattern(pattern_path)
        except TrafficPatternError as e:
            p.error(str(e))
        if loops != 1:
            pattern = LoopedTrafficPattern(pattern, loops)

    if args.protocol == "generate":
        if args.generate_kind == "video":
            try:
                if args.from_media is not None:
                    probe = run_ffprobe(args.from_media)
                    doc = generate_video_trace_from_ffprobe(
                        probe,
                        duration=args.duration,
                        source=str(args.from_media),
                    )
                elif args.ffprobe_json is not None:
                    probe = load_ffprobe_json(args.ffprobe_json)
                    doc = generate_video_trace_from_ffprobe(
                        probe,
                        duration=args.duration,
                        source=str(args.ffprobe_json),
                    )
                else:
                    doc = generate_video_trace(
                        fps=args.fps,
                        gop=args.gop,
                        i_size=args.i_size,
                        p_size=args.p_size,
                        b_size=args.b_size,
                        duration=(
                            10.0 if args.duration is None else args.duration
                        ),
                    )
                write_trace_document(args.output, doc)
            except VideoTraceError as e:
                print(f"error: {e}", file=sys.stderr)
                raise SystemExit(2) from e
            meta = doc["metadata"]
            detail = meta.get("gop") or meta.get("source") or meta.get(
                "generator"
            )
            print(
                f"Wrote {len(doc['events'])} frame events "
                f"({meta.get('frames')} frames, {detail}) to {args.output}"
            )
            return
        raise ValueError(f"Unknown generate kind: {args.generate_kind}")

    if args.role not in ("server", "client"):
        raise ValueError(f"Invalid role: {args.role}. Must be 'server' or 'client'.")

    if reverse and getattr(args, "interactive", False):
        p.error("--reverse cannot be combined with --interactive")

    record_path = getattr(args, "record_path", None)
    if record_path is not None and args.role == "client" and not reverse:
        p.error(
            "--record is only used when receiving "
            "(pass it to the server, or combine with --reverse)"
        )

    reverse_trace_name = None
    if reverse and pattern_path is not None:
        try:
            reverse_trace_name = reverse_trace_file_name(pattern_path)
        except ValueError as e:
            p.error(str(e))

    record_metadata = None
    if record_path is not None:
        record_metadata = _record_run_metadata(
            args,
            reverse=reverse,
            reverse_trace_name=reverse_trace_name,
            loops=loops,
            pattern_path=pattern_path,
        )

    log = Logger(
        args.json_log,
        args.protocol,
        args.role,
        args.logfile,
    )

    controller = None
    if args.protocol == "udp":
        if args.role == "server":
            udp.server(
                log,
                args.address,
                args.port,
                record_path=record_path,
                inactivity_timeout=args.inactivity_timeout,
                trace_dir=args.trace_dir,
                record_metadata=record_metadata,
            )
        else:
            if reverse:
                udp.client(
                    log,
                    args.address,
                    args.port,
                    args.length,
                    reverse_config=ReverseConfig(
                        duration_ms=(
                            0 if not args.time else int(args.time * 1000)
                        ),
                        bandwidth_bps=args.bandwidth,
                        payload_len=args.length,
                        traffic_pattern_name=reverse_trace_name,
                        loops=loops,
                    ),
                    bind_dev=args.bind_dev,
                    record_path=record_path,
                    record_metadata=record_metadata,
                )
            else:
                if pattern is not None:
                    controller = TrafficPatternController(
                        pattern,
                        bandwidth_bps=args.bandwidth,
                    )
                else:
                    bw = (
                        args.bandwidth or parse_bandwidth("50M")
                        if args.interactive
                        else args.bandwidth
                    )
                    if args.interactive:
                        controller = InteractiveController(bw)
                    else:
                        controller = StaticPacingController(bw, args.time)
                udp.client(
                    log,
                    args.address,
                    args.port,
                    args.length,
                    controller,
                    args.enable_latency,
                    bind_dev=args.bind_dev,
                )

    else:
        if args.role == "server":
            tcp.server(
                log,
                args.address,
                args.port,
                congestion_control=args.congestion_control,
                trace_dir=args.trace_dir,
                record_path=record_path,
                record_metadata=record_metadata,
            )
        else:
            if reverse:
                tcp.client(
                    log,
                    args.address,
                    args.port,
                    args.congestion_control,
                    args.set_mss,
                    reverse_config=ReverseConfig(
                        duration_ms=(
                            0 if not args.time else int(args.time) * 1000
                        ),
                        bandwidth_bps=args.bandwidth,
                        traffic_pattern_name=reverse_trace_name,
                        loops=loops,
                    ),
                    bind_dev=args.bind_dev,
                    record_path=record_path,
                    record_metadata=record_metadata,
                )
            else:
                # TCP record header is sent in addition to the payload returned by
                # next_send(); charge it so measured rates match --bandwidth /
                # traffic-pattern bitrates. UDP includes its header in the datagram.
                tcp_overhead = tcp.TCP_HDR_SIZE
                if pattern is not None:
                    controller = TrafficPatternController(
                        pattern,
                        bandwidth_bps=args.bandwidth,
                        header_overhead=tcp_overhead,
                    )
                elif args.interactive:
                    # If no bandwidth provided, controller will act as unlimited until adjusted
                    controller = InteractiveController(
                        args.bandwidth,
                        header_overhead=tcp_overhead,
                    )
                else:
                    controller = StaticPacingController(
                        args.bandwidth,
                        args.time,
                        header_overhead=tcp_overhead,
                    )
                tcp.client(
                    log,
                    args.address,
                    args.port,
                    args.congestion_control,
                    args.set_mss,
                    controller,
                    args.enable_latency,
                    bind_dev=args.bind_dev,
                )

    if controller is not None:
        controller.stop()
