#!/usr/bin/env python3

from __future__ import annotations
import argparse
import sys

from .logger import Logger
from .utils import parse_bandwidth, parse_ip, tcp_congestion_control_info
from . import tcp, udp
from .interactive import InteractiveController
from .controllers import StaticPacingController, TrafficPatternController
from .traffic_pattern import TrafficPatternError, load_traffic_pattern
from .video_trace import (
    VideoTraceError,
    generate_video_trace,
    generate_video_trace_from_ffprobe,
    load_ffprobe_json,
    run_ffprobe,
    write_trace_document,
)


def parse_traffic_pattern_arg(path: str):
    try:
        return load_traffic_pattern(path)
    except TrafficPatternError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def main():
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
    common.add_argument("--interval", type=float, default=1.0, help="Stats interval")

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
        type=parse_traffic_pattern_arg,
        metavar="FILE",
        help="JSON traffic pattern file (trace or piecewise_rate)",
    )

    udp_parser = subp.add_parser("udp", help="UDP mode")
    udp_sub = udp_parser.add_subparsers(dest="role", required=True)

    udp_srv = udp_sub.add_parser("server", parents=[common], help="Run a UDP server")
    udp_srv.add_argument(
        "address", metavar="ADDRESS", type=parse_ip, help="Listen address"
    )
    udp_srv.add_argument(
        "--packet-record",
        metavar="PATH",
        help="Write received UDP packet traces to a CSV file after the test",
    )
    udp_srv.add_argument(
        "--inactivity-timeout",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Exit after this many seconds without receiving packets (default: 2.0)",
    )

    udp_cli = udp_sub.add_parser("client", parents=[common], help="Run a UDP client")
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
        type=parse_traffic_pattern_arg,
        metavar="FILE",
        help="JSON traffic pattern file (trace or piecewise_rate)",
    )

    args = p.parse_args()

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

    log = Logger(args.json_log, args.protocol, args.role, args.logfile)

    controller = None
    if args.protocol == "udp":
        if args.role == "server":
            udp.server(
                log,
                args.address,
                args.port,
                args.interval,
                packet_record_path=args.packet_record,
                inactivity_timeout=args.inactivity_timeout,
            )
        else:
            if getattr(args, "traffic_pattern", None) is not None:
                controller = TrafficPatternController(
                    args.traffic_pattern,
                    args.interval,
                    bandwidth_bps=args.bandwidth,
                )
            else:
                bw = (
                    args.bandwidth or parse_bandwidth("50M")
                    if args.interactive
                    else args.bandwidth
                )
                if args.interactive:
                    controller = InteractiveController(bw, args.interval)
                else:
                    controller = StaticPacingController(
                        bw, args.time, args.interval
                    )
            udp.client(
                log,
                args.address,
                args.port,
                args.length,
                controller,
                args.enable_latency,
            )

    else:
        if args.role == "server":
            tcp.server(
                log,
                args.address,
                args.port,
                args.interval,
                args.congestion_control,
            )
        else:
            # TCP record header is sent in addition to the payload returned by
            # next_send(); charge it so measured rates match --bandwidth /
            # traffic-pattern bitrates. UDP includes its header in the datagram.
            tcp_overhead = tcp.TCP_HDR_SIZE
            if getattr(args, "traffic_pattern", None) is not None:
                controller = TrafficPatternController(
                    args.traffic_pattern,
                    args.interval,
                    bandwidth_bps=args.bandwidth,
                    header_overhead=tcp_overhead,
                )
            elif args.interactive:
                # If no bandwidth provided, controller will act as unlimited until adjusted
                controller = InteractiveController(
                    args.bandwidth,
                    args.interval,
                    header_overhead=tcp_overhead,
                )
            else:
                controller = StaticPacingController(
                    args.bandwidth,
                    args.time,
                    args.interval,
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
            )

    if controller is not None:
        controller.stop()
