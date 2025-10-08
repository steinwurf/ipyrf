#!/usr/bin/env python3

from __future__ import annotations
import argparse
import sys

from .logger import Logger
from .utils import parse_bandwidth, parse_ip, tcp_congestion_control_info
from . import tcp, udp


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
    tcp_cli.add_argument(
        "--time", type=int, default=10, help="Test duration in seconds"
    )
    tcp_cli.add_argument("address", metavar="ADDRESS", help="Server address to connect")
    tcp_cli.add_argument(
        "--set-mss", dest="set_mss", type=int, help="TCP: set TCP_MAXSEG (approx MSS)"
    )

    udp_parser = subp.add_parser("udp", help="UDP mode")
    udp_sub = udp_parser.add_subparsers(dest="role", required=True)

    udp_srv = udp_sub.add_parser("server", parents=[common], help="Run a UDP server")
    udp_srv.add_argument(
        "address", metavar="ADDRESS", type=parse_ip, help="Listen address"
    )

    udp_cli = udp_sub.add_parser("client", parents=[common], help="Run a UDP client")
    udp_cli.add_argument(
        "--time", type=int, default=10, help="Test duration in seconds"
    )
    udp_cli.add_argument("address", metavar="ADDRESS", help="Server address to connect")
    udp_cli.add_argument(
        "--bandwidth", type=parse_bandwidth, help="Target bandwidth, e.g., 50M"
    )
    udp_cli.add_argument(
        "-l", dest="length", type=int, default=1200, help="UDP payload length"
    )

    args = p.parse_args()

    if args.role not in ("server", "client"):
        raise ValueError(f"Invalid role: {args.role}. Must be 'server' or 'client'.")

    if args.logfile:
        sys.stdout = open(args.logfile, "w", buffering=1)

    log = Logger(args.json_log, args.protocol, args.role)

    if args.protocol == "udp":
        if args.role == "server":
            udp.server(log, args.address, args.port)
        else:
            bw = args.bandwidth or 1e9
            udp.client(log, args.address, args.port, args.time, bw, args.length)
    else:
        if args.role == "server":
            tcp.server(log, args.address, args.port, args.congestion_control)
        else:
            tcp.client(
                log,
                args.address,
                args.port,
                args.congestion_control,
                args.time,
                args.set_mss,
                args.bandwidth,
            )
