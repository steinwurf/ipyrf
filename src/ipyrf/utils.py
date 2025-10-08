from __future__ import annotations
import argparse
import pathlib
import socket


def tcp_congestion_control_info():
    if not pathlib.Path("/proc/sys/net/ipv4/tcp_congestion_control").exists():
        return {}

    def read_proc(path):
        p = pathlib.Path(path)
        return p.read_text().strip().split() if p.exists() else []

    current = "".join(read_proc("/proc/sys/net/ipv4/tcp_congestion_control"))
    available = read_proc("/proc/sys/net/ipv4/tcp_available_congestion_control")
    allowed = read_proc("/proc/sys/net/ipv4/tcp_allowed_congestion_control")
    return {
        "current": current,
        "available": available,
        "allowed": allowed or available,
    }


def human_readable_bytes(n: int) -> str:
    if n < 1000:
        return f"{n} B"
    elif n < 1_000_000:
        return f"{n / 1_000:.1f} kB"
    elif n < 1_000_000_000:
        return f"{n / 1_000_000:.1f} MB"
    else:
        return f"{n / 1_000_000_000:.1f} GB"


def parse_bandwidth(s: str | None) -> float | None:
    if not s:
        return None
    multipliers = {"k": 1e3, "m": 1e6, "g": 1e9}
    try:
        suffix = s[-1].lower()
        if suffix in multipliers:
            return float(s[:-1]) * multipliers[suffix]
        return float(s)
    except Exception:
        raise argparse.ArgumentTypeError(f"Invalid bandwidth: {s}")


def parse_ip(s: str) -> str:
    try:
        socket.inet_pton(socket.AF_INET, s)
        return s
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, s)
        return s
    except OSError:
        pass
    raise argparse.ArgumentTypeError(f"Invalid IP address: {s}")


def human_bps(bps: float) -> str:
    if bps == float("inf"):
        return "unlimited"
    if bps < 1e3:
        return f"{bps:.0f} bps"
    if bps < 1e6:
        return f"{bps/1e3:.1f} Kbps"
    if bps < 1e9:
        return f"{bps/1e6:.1f} Mbps"
    return f"{bps/1e9:.2f} Gbps"
