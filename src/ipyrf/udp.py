from __future__ import annotations
import socket
import struct
import time
from typing import Optional, Tuple

from .logger import Logger
from .controllers import BasePacingController


UDP_HDR = struct.Struct("!I Q I")
FIN_FLAG = 0x1


def server(log: Logger, bind_addr: str, port: int, interval_seconds: float):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_addr, port))
    sock.settimeout(2.0)

    active = False
    start = 0.0
    last_bytes = 0
    last_pkts = 0
    last_seq_seen_last = -1
    last_seq_seen = -1
    bytes_received = 0
    total_pkts = 0
    src_peer: Optional[Tuple[str, int]] = None

    log.start(bind_addr, port)

    inactivity_deadline = None

    stop_reason = "unknown"
    while True:
        try:
            data, peer = sock.recvfrom(65535)
        except socket.timeout:
            if active and inactivity_deadline and time.time() > inactivity_deadline:
                stop_reason = "inactivity"
                break
            continue

        now = time.time()
        if not active:
            log.test(peer[0], peer[1], now)
            active = True
            start = now
            last_ts = now
            src_peer = peer
        inactivity_deadline = now + 2.0

        if len(data) >= UDP_HDR.size:
            seq, _, flags = UDP_HDR.unpack_from(data)
        else:
            seq, _, flags = (0, 0, 0)

        if (flags & FIN_FLAG) != 0:
            stop_reason = "end-of-test"
            break

        total_pkts += 1
        bytes_received += len(data)

        last_seq_seen = max(last_seq_seen, seq)
        if last_seq_seen_last == -1:
            last_seq_seen_last = last_seq_seen

        if (now - last_ts) >= interval_seconds:
            sent_packets = last_seq_seen - last_seq_seen_last
            received = total_pkts - last_pkts
            lost = max(0, sent_packets - received)
            percentage_lost = (100.0 * lost / sent_packets) if sent_packets > 0 else 0.0
            log.update(
                start_ts=last_ts,
                end_ts=now,
                bytes=bytes_received - last_bytes,
                packets=sent_packets,
                lost_packets=lost,
                lost_percent=percentage_lost,
            )
            last_ts = now
            last_bytes = bytes_received
            last_pkts = total_pkts
            last_seq_seen_last = last_seq_seen

    end = time.time()
    dur = max(1e-9, end - start) if active else 0.0
    lost = max(0, last_seq_seen + 1 - total_pkts) if last_seq_seen >= 0 else 0
    loss_pct = (100.0 * lost / max(1, last_seq_seen + 1)) if last_seq_seen >= 0 else 0.0
    log.summary(
        receiver=f"{bind_addr}:{port}",
        sender=None if not src_peer else f"{src_peer[0]}:{src_peer[1]}",
        seconds=dur,
        bytes=bytes_received,
        packets=total_pkts,
        bits_per_second=((bytes_received * 8.0) / max(1e-9, dur) if dur > 0 else 0.0),
        lost_packets=lost,
        lost_percent=loss_pct,
        stop_reason=stop_reason,
    )


def client(
    log: Logger,
    host: str,
    port: int,
    payload_len: int,
    controller: BasePacingController,
):
    if payload_len < UDP_HDR.size:
        payload_len = UDP_HDR.size

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.connect((host, port))

    log.start(host, port)

    start = time.time()
    last_ts = start
    last_bytes = 0
    last_pkts = 0
    seq = 0
    bytes_sent = 0
    pkts_sent = 0

    payload = bytearray(payload_len)

    stop_reason = controller.stop_reason()

    # Start timing if the controller has a duration
    controller.start()

    while not controller.should_stop():
        UDP_HDR.pack_into(payload, 0, seq, time.time_ns(), 0)

        if controller.is_pacing():
            controller.maybe_sleep(len(payload))
        try:
            if bytes_sent == 0:
                log.test(host, port, start)
            n = sock.send(payload)
        except Exception as e:
            stop_reason = f"error sending packet: {e}"
            break
        if n <= 0:
            stop_reason = "send returned 0"
            break
        bytes_sent += n
        pkts_sent += 1
        seq += 1
        now = time.time()
        if (now - last_ts) >= controller.interval_seconds:
            extra = controller.get_update_fields()
            log.update(
                start_ts=last_ts,
                end_ts=now,
                bytes=bytes_sent - last_bytes,
                packets=pkts_sent - last_pkts,
                **extra,
            )
            last_ts = now
            last_bytes = bytes_sent
            last_pkts = pkts_sent

    for _ in range(3):
        UDP_HDR.pack_into(payload, 0, seq, time.time_ns(), 0x1)
        try:
            sock.send(payload)
        except Exception:
            pass
        time.sleep(0.01)

    dur = max(1e-9, time.time() - start)
    log.summary(
        receiver=f"{host}:{port}",
        seconds=dur,
        bytes=bytes_sent,
        packets=pkts_sent,
        bits_per_second=(bytes_sent * 8.0) / dur,
        **controller.get_update_fields(),
        payload_len=payload_len,
        stop_reason=stop_reason,
    )
