"""In-band first-packet actions.

The first TCP record or UDP datagram is either test payload (current
behavior) or a control message that tells the receiver what to do next.

TCP uses the existing 1-byte ``latency_flag`` field as the discriminator:
values ``0`` and ``1`` remain data records (see ``LATENCY_DISABLED`` /
``LATENCY_ENABLED`` in :mod:`ipyrf.tcp`). Other values are control.

UDP sets :data:`UDP_CONTROL_FLAG` on a datagram that is not test data.
The sequence field then selects the action.

A reverse first-packet carries a :class:`ReverseConfig` payload so the
listening side can become the sender. The config supports bandwidth-paced
tests (target rate, duration, UDP payload length) and optional traffic
pattern files identified by file name only, so the whole config always
fits in a single packet.
"""

from __future__ import annotations
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

ACTION_DATA = "data"
ACTION_REVERSE = "reverse"
ACTION_CONFIG = "config"
ACTION_ERROR = "error"
ACTION_UNKNOWN = "unknown"

# Reserved TCP latency_flag values. 0 and 1 are data (with/without latency).
TCP_FLAG_REVERSE = 2
TCP_FLAG_CONFIG = 3
TCP_FLAG_ERROR = 4

# UDP header flags. FIN and latency live in udp.py; this bit marks control.
UDP_CONTROL_FLAG = 0x4
UDP_CONTROL_SEQ_REVERSE = 1
UDP_CONTROL_SEQ_CONFIG = 2
UDP_CONTROL_SEQ_ERROR = 3

_TCP_FLAG_ACTIONS = {
    TCP_FLAG_REVERSE: ACTION_REVERSE,
    TCP_FLAG_CONFIG: ACTION_CONFIG,
    TCP_FLAG_ERROR: ACTION_ERROR,
}

_UDP_CONTROL_SEQ_ACTIONS = {
    UDP_CONTROL_SEQ_REVERSE: ACTION_REVERSE,
    UDP_CONTROL_SEQ_CONFIG: ACTION_CONFIG,
    UDP_CONTROL_SEQ_ERROR: ACTION_ERROR,
}

# version, bandwidth_bps (0 = unlimited), duration_ms, payload_len
# (0 = default), loops, name_len. File name bytes follow the header.
REVERSE_CONFIG = struct.Struct("!BQIIIB")
REVERSE_CONFIG_VERSION = 1
REVERSE_CONFIG_SIZE = REVERSE_CONFIG.size
DEFAULT_REVERSE_PAYLOAD_LEN = 1200
MAX_TRACE_FILE_NAME_LEN = 255
MAX_ERROR_MESSAGE_LEN = 1024


@dataclass(frozen=True)
class ReverseConfig:
    """Reverse-test parameters sent in the first packet.

    When ``traffic_pattern_name`` is set, the server loads that file by
    name (not path) from its working directory or ``--trace-dir``.
    ``duration_ms`` is then ignored; ``bandwidth_bps`` remains an
    optional egress-rate cap and ``loops`` replays the pattern.
    """

    duration_ms: int
    bandwidth_bps: Optional[float] = None
    payload_len: int = 0
    traffic_pattern_name: Optional[str] = None
    loops: int = 1


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


def validate_trace_file_name(name: str) -> str:
    """Return ``name`` if it is a file name with no path components.

    Raises:
        ValueError: If ``name`` is empty, too long, or contains a path.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("trace file name must be a non-empty string")
    encoded_len = len(name.encode("utf-8"))
    if encoded_len > MAX_TRACE_FILE_NAME_LEN:
        raise ValueError(
            f"trace file name too long ({encoded_len} > "
            f"{MAX_TRACE_FILE_NAME_LEN})"
        )
    if name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(
            f"trace file name must be a file name, not a path: {name!r}"
        )
    if Path(name).name != name:
        raise ValueError(
            f"trace file name must be a file name, not a path: {name!r}"
        )
    return name


def reverse_trace_file_name(path: Union[str, Path]) -> str:
    """Return the file name to send for a client ``--traffic-pattern`` path."""
    return validate_trace_file_name(Path(path).name)


def resolve_trace_file(
    name: str, trace_dir: Optional[Union[str, Path]] = None
) -> Path:
    """Locate a reverse traffic-pattern file by name.

    Looks in ``trace_dir`` when given, otherwise the process working
    directory. ``name`` must be a file name, not a path.
    """
    name = validate_trace_file_name(name)
    directory = Path(trace_dir) if trace_dir is not None else Path.cwd()
    if not directory.is_dir():
        raise ValueError(f"trace directory not found: {directory}")
    path = directory / name
    if not path.is_file():
        raise ValueError(f"trace file not found: {path}")
    return path


def pack_reverse_config(config: ReverseConfig) -> bytes:
    """Serialize a reverse config for a TCP record or UDP datagram payload."""
    bw = 0 if config.bandwidth_bps is None else int(round(config.bandwidth_bps))
    if bw < 0:
        bw = 0
    payload_len = max(0, int(config.payload_len))
    duration_ms = int(config.duration_ms)
    if duration_ms < 0 or duration_ms > 0xFFFFFFFF:
        raise ValueError(f"invalid reverse duration: {duration_ms}")
    loops = int(config.loops)
    if loops < 1:
        raise ValueError(f"invalid reverse loops: {loops}")
    name = config.traffic_pattern_name or ""
    if name:
        name = validate_trace_file_name(name)
    name_bytes = name.encode("utf-8")
    return (
        REVERSE_CONFIG.pack(
            REVERSE_CONFIG_VERSION,
            bw,
            duration_ms,
            payload_len,
            loops,
            len(name_bytes),
        )
        + name_bytes
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
    version, bw, duration_ms, payload_len, loops, name_len = (
        REVERSE_CONFIG.unpack_from(payload)
    )
    if version != REVERSE_CONFIG_VERSION:
        raise ValueError(f"unsupported reverse config version: {version}")
    if loops < 1:
        raise ValueError(f"invalid reverse loops: {loops}")
    name_end = REVERSE_CONFIG_SIZE + name_len
    if len(payload) < name_end:
        raise ValueError(
            f"truncated reverse config name ({len(payload)} < {name_end})"
        )
    name = None
    if name_len:
        try:
            name = payload[REVERSE_CONFIG_SIZE:name_end].decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError("invalid reverse trace file name encoding") from e
        name = validate_trace_file_name(name)
    return ReverseConfig(
        duration_ms=int(duration_ms),
        bandwidth_bps=None if bw == 0 else float(bw),
        payload_len=int(payload_len),
        traffic_pattern_name=name,
        loops=int(loops),
    )


def reverse_udp_payload_len(config: ReverseConfig) -> int:
    """UDP datagram size for a reverse send (header is included)."""
    if config.payload_len > 0:
        return config.payload_len
    return DEFAULT_REVERSE_PAYLOAD_LEN


def pack_error_message(message: str) -> bytes:
    """Serialize a reverse-setup error for a control record or datagram."""
    encoded = str(message).encode("utf-8")
    if len(encoded) > MAX_ERROR_MESSAGE_LEN:
        encoded = encoded[:MAX_ERROR_MESSAGE_LEN]
    return encoded


def unpack_error_message(payload: bytes) -> str:
    """Parse a reverse-setup error payload."""
    if not payload:
        return "reverse error"
    return payload.decode("utf-8", errors="replace")
