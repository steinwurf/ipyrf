#!/usr/bin/env python3

from __future__ import annotations
import argparse
import sys

from .logger import Logger
from .utils import parse_bandwidth, parse_ip, tcp_congestion_control_info
from . import tcp, udp
from .interactive import InteractiveController
from .controllers import StaticPacingController


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
        "--bandwidth", type=parse_bandwidth, help="Target bandwidth, e.g., 50M"
    )
    tcp_cli.add_argument("address", metavar="ADDRESS", help="Server address to connect")
    tcp_cli.add_argument(
        "--set-mss", dest="set_mss", type=int, help="TCP: set TCP_MAXSEG (approx MSS)"
    )

    # Time and interactive mode are mutually exclusive
    tcp_time_group = tcp_cli.add_mutually_exclusive_group()
    tcp_time_group.add_argument(
        "--time", type=int, default=10, help="Test duration in seconds"
    )
    tcp_time_group.add_argument(
        "--interactive", action="store_true", help="Run client in interactive mode"
    )

    udp_parser = subp.add_parser("udp", help="UDP mode")
    udp_sub = udp_parser.add_subparsers(dest="role", required=True)

    udp_srv = udp_sub.add_parser("server", parents=[common], help="Run a UDP server")
    udp_srv.add_argument(
        "address", metavar="ADDRESS", type=parse_ip, help="Listen address"
    )

    udp_cli = udp_sub.add_parser("client", parents=[common], help="Run a UDP client")
    udp_cli.add_argument("address", metavar="ADDRESS", help="Server address to connect")
    udp_cli.add_argument(
        "--bandwidth", type=parse_bandwidth, help="Target bandwidth, e.g., 50M"
    )
    udp_cli.add_argument(
        "-l", dest="length", type=int, default=1200, help="UDP payload length"
    )

    # Time and interactive mode are mutually exclusive
    udp_time_group = udp_cli.add_mutually_exclusive_group()
    udp_time_group.add_argument(
        "--time", type=int, default=10, help="Test duration in seconds"
    )
    udp_time_group.add_argument(
        "--interactive", action="store_true", help="Run client in interactive mode"
    )

    args = p.parse_args()

    if args.role not in ("server", "client"):
        raise ValueError(f"Invalid role: {args.role}. Must be 'server' or 'client'.")

    log = Logger(args.json_log, args.protocol, args.role, args.logfile)

    controller = None
    if args.protocol == "udp":
        if args.role == "server":
            udp.server(log, args.address, args.port, args.interval)
        else:
            bw = (
                args.bandwidth or parse_bandwidth("50M")
                if args.interactive
                else (args.bandwidth or 1e9)
            )
            if args.interactive:
                controller = InteractiveController(bw, args.length, args.interval)
            else:
                controller = StaticPacingController(
                    bw, max(args.length, udp.UDP_HDR.size), args.time, args.interval
                )
            udp.client(
                log,
                args.address,
                args.port,
                args.length,
                controller,
            )

    else:
        if args.role == "server":
            tcp.server(
                log, args.address, args.port, args.interval, args.congestion_control
            )
        else:
            if args.interactive:
                quantum = args.set_mss if args.set_mss else 1200
                # If no bandwidth provided, controller will act as unlimited until adjusted
                controller = InteractiveController(
                    args.bandwidth, quantum, args.interval
                )
            else:
                controller = StaticPacingController(
                    args.bandwidth,
                    args.set_mss if args.set_mss else 1200,
                    args.time,
                    args.interval,
                )
            tcp.client(
                log,
                args.address,
                args.port,
                args.congestion_control,
                args.set_mss,
                controller,
            )

    if controller is not None:
        controller.stop()
