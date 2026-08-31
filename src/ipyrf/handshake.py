"""In-band first-packet actions.

The first TCP record or UDP datagram is either test payload (current
behavior) or a control message that tells the receiver what to do next.

TCP uses the existing 1-byte ``latency_flag`` field as the discriminator:
values ``0`` and ``1`` remain data records (see ``LATENCY_DISABLED`` /
``LATENCY_ENABLED`` in :mod:`ipyrf.tcp`). Other values are control.

UDP sets :data:`UDP_CONTROL_FLAG` on a datagram that is not test data.
The sequence field then selects the action.

A reverse first-packet carries a :class:`ReverseConfig` payload so the
listening side can become the sender. The config currently supports
bandwidth-paced tests (target rate, duration, UDP payload length).
"""

from __future__ import annotations
import struct
from dataclasses import dataclass
from typing import Optional

ACTION_DATA = "data"
ACTION_REVERSE = "reverse"
ACTION_CONFIG = "config"
ACTION_UNKNOWN = "unknown"

# Reserved TCP latency_flag values. 0 and 1 are data (with/without latency).
TCP_FLAG_REVERSE = 2
TCP_FLAG_CONFIG = 3

# UDP header flags. FIN and latency live in udp.py; this bit marks control.
UDP_CONTROL_FLAG = 0x4
UDP_CONTROL_SEQ_REVERSE = 1
UDP_CONTROL_SEQ_CONFIG = 2

_TCP_FLAG_ACTIONS = {
    TCP_FLAG_REVERSE: ACTION_REVERSE,
    TCP_FLAG_CONFIG: ACTION_CONFIG,
}

_UDP_CONTROL_SEQ_ACTIONS = {
    UDP_CONTROL_SEQ_REVERSE: ACTION_REVERSE,
    UDP_CONTROL_SEQ_CONFIG: ACTION_CONFIG,
}

# version, bandwidth_bps (0 = unlimited), duration_seconds, payload_len (0 = default)
REVERSE_CONFIG = struct.Struct("!BQdI")
REVERSE_CONFIG_VERSION = 1
REVERSE_CONFIG_SIZE = REVERSE_CONFIG.size
DEFAULT_REVERSE_PAYLOAD_LEN = 1200


@dataclass(frozen=True)
class ReverseConfig:
    """Bandwidth-paced reverse-test parameters sent in the first packet."""

    duration_seconds: float
    bandwidth_bps: Optional[float] = None
    payload_len: int = 0


def classify_tcp_flag(latency_flag: int) -> str:
    """Return the first-packet action encoded in a TCP record flag."""
    if latency_flag in (0, 1):
        return ACTION_DATA
    return _TCP_FLAG_ACTIONS.get(latency_flag, ACTION_UNKNOWN)


def classify_udp_first(flags: int, seq: int = 0) -> str:
    """Return the first-packet action encoded in a UDP datagram."""
    if (flags & UDP_CONTROL_FLAG) == 0:
        return ACTION_DATA
    return _UDP_CONTROL_SEQ_ACTIONS.get(seq, ACTION_UNKNOWN)


def pack_reverse_config(config: ReverseConfig) -> bytes:
    """Serialize a reverse config for a TCP record or UDP datagram payload."""
    bw = 0 if config.bandwidth_bps is None else int(round(config.bandwidth_bps))
    if bw < 0:
        bw = 0
    payload_len = max(0, int(config.payload_len))
    return REVERSE_CONFIG.pack(
        REVERSE_CONFIG_VERSION, bw, float(config.duration_seconds), payload_len
    )


def unpack_reverse_config(payload: bytes) -> ReverseConfig:
    """Parse a reverse config payload.

    Raises:
        ValueError: If the payload is truncated or the version is unknown.
    """
    if len(payload) < REVERSE_CONFIG_SIZE:
        raise ValueError(
            f"truncated reverse config ({len(payload)} < {REVERSE_CONFIG_SIZE})"
        )
    version, bw, duration, payload_len = REVERSE_CONFIG.unpack_from(payload)
    if version != REVERSE_CONFIG_VERSION:
        raise ValueError(f"unsupported reverse config version: {version}")
    if duration < 0:
        raise ValueError(f"invalid reverse duration: {duration}")
    return ReverseConfig(
        duration_seconds=float(duration),
        bandwidth_bps=None if bw == 0 else float(bw),
        payload_len=int(payload_len),
    )


def reverse_udp_payload_len(config: ReverseConfig) -> int:
    """UDP datagram size for a reverse send (header is included)."""
    if config.payload_len > 0:
        return config.payload_len
    return DEFAULT_REVERSE_PAYLOAD_LEN
