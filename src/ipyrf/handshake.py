"""In-band first-packet actions.

The first TCP record or UDP datagram is either test payload (current
behavior) or a control message that tells the receiver what to do next.

Control values are reserved so a future reverse or config handshake can
reuse the send/receive steps without changing the data path again.

TCP uses the existing 1-byte ``latency_flag`` field as the discriminator:
values ``0`` and ``1`` remain data records (see ``LATENCY_DISABLED`` /
``LATENCY_ENABLED`` in :mod:`ipyrf.tcp`). Other values are control.

UDP sets :data:`UDP_CONTROL_FLAG` on a datagram that is not test data.
The sequence field then selects the action.
"""

from __future__ import annotations

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
