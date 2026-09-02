from __future__ import annotations
import socket
import struct
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from .handshake import (
    ACTION_DATA,
    ACTION_ERROR,
    ACTION_REVERSE,
    UDP_CONTROL_FLAG,
    UDP_CONTROL_SEQ_ERROR,
    UDP_CONTROL_SEQ_REVERSE,
    ReverseConfig,
    classify_udp_first,
    pack_error_message,
    pack_reverse_config,
    reverse_udp_payload_len,
    unpack_error_message,
    unpack_reverse_config,
)
from .latency import LatencyTracker
from .logger import Logger
from .controllers import (
    BasePacingController,
    controller_from_reverse_config,
)
from .recorder import Recorder
from .utils import bind_to_device


UDP_HDR = struct.Struct("!I Q I I")
FIN_FLAG = 0x1
LATENCY_FLAG = 0x2
# UDP_CONTROL_FLAG (0x4) lives in handshake.py: first datagram is not test data.


@dataclass(frozen=True)
class UdpPacket:
    seq: int
    timestamp_ns: int
    flags: int
    event_id: int
    size: int
    peer: Tuple[str, int]
    received_ns: int

    @property
    def is_fin(self) -> bool:
        return (self.flags & FIN_FLAG) != 0

    @property
    def latency_enabled(self) -> bool:
        return (self.flags & LATENCY_FLAG) != 0

    @property
    def is_control(self) -> bool:
        return (self.flags & UDP_CONTROL_FLAG) != 0


def parse_udp_packet(
    buffer, n: int, peer: Tuple[str, int], received_ns: int
) -> UdpPacket:
    if n >= UDP_HDR.size:
        seq, timestamp_ns, flags, event_id = UDP_HDR.unpack_from(buffer)
    else:
        seq, timestamp_ns, flags, event_id = (0, 0, 0, 0)
    return UdpPacket(
        seq, timestamp_ns, flags, event_id, n, peer, received_ns
    )


def classify_first_packet(packet: UdpPacket) -> str:
    """Return the first-packet action for ``packet``.

    Datagrams without :data:`UDP_CONTROL_FLAG` are test payload (current
    behavior), including FIN. Reverse control datagrams carry a
    :class:`ReverseConfig` after the UDP header.
    """
    return classify_udp_first(packet.flags, packet.seq)


def write_udp_control(
    sock: socket.socket, seq: int, payload: bytes, flags: int = UDP_CONTROL_FLAG
) -> Optional[str]:
    """Send one control datagram. Returns a stop reason, or None on success."""
    buf = bytearray(UDP_HDR.size + len(payload))
    UDP_HDR.pack_into(buf, 0, seq, time.time_ns(), flags, 0)
    buf[UDP_HDR.size :] = payload
    try:
        n = sock.send(buf)
    except Exception as e:
        return f"error sending packet: {e}"
    if n <= 0:
        return "send returned 0"
    return None


class UdpReceiver:
    """Receive UDP datagrams and log throughput / loss / latency.

    Split out so a future first-packet control action can run a send
    step instead of this receive step toward the same peer.
    """

    def __init__(
        self,
        sock: socket.socket,
        log: Logger,
        bind_addr: str,
        port: int,
        record_path: Optional[str] = None,
        inactivity_timeout: float = 2.0,
        record_metadata: Optional[dict] = None,
    ):
        self.sock = sock
        self.log = log
        self.bind_addr = bind_addr
        self.port = port
        self.inactivity_timeout = inactivity_timeout
        self.recv_buffer = bytearray(65535)
        self.recorder = (
            Recorder(record_path, metadata=record_metadata)
            if record_path is not None
            else None
        )
        self.active = False
        self.start = 0.0
        self.last_ts = 0.0
        self.last_bytes = 0
        self.last_pkts = 0
        self.last_seq_seen_last = 0
        self.last_seq_seen = -1
        self.bytes_received = 0
        self.total_pkts = 0
        self.src_peer: Optional[Tuple[str, int]] = None
        self.inactivity_deadline: Optional[float] = None
        self.stop_reason = "unknown"
        self.latency = LatencyTracker()

    def wait_first(self) -> UdpPacket:
        """Wait until the first datagram arrives (no inactivity stop)."""
        while True:
            try:
                n, peer = self.sock.recvfrom_into(self.recv_buffer)
            except socket.timeout:
                continue
            return parse_udp_packet(
                self.recv_buffer, n, peer, time.time_ns()
            )

    def run(self, first: UdpPacket) -> None:
        """Receive test data. ``first`` is included when it is payload."""
        self._begin(first)
        if not self._accept(first):
            return
        while True:
            try:
                n, peer = self.sock.recvfrom_into(self.recv_buffer)
            except socket.timeout:
                if (
                    self.active
                    and self.inactivity_deadline
                    and time.time() > self.inactivity_deadline
                ):
                    self.stop_reason = "inactivity"
                    break
                continue
            except KeyboardInterrupt:
                self.stop_reason = "interrupted"
                break
            packet = parse_udp_packet(
                self.recv_buffer, n, peer, time.time_ns()
            )
            if not self._accept(packet):
                break

    def close_recorder(self) -> None:
        if self.recorder is None:
            return
        dur = (
            max(1e-9, time.time() - self.start) if self.active else 0.0
        )
        self.recorder.update_metadata(
            protocol="udp",
            sequence="sender",
            record_unit="datagram",
            direction="rx",
            receiver=f"{self.bind_addr}:{self.port}",
            sender=(
                None
                if not self.src_peer
                else f"{self.src_peer[0]}:{self.src_peer[1]}"
            ),
            stop_reason=self.stop_reason,
            seconds=dur,
            bytes=self.bytes_received,
            latency_enabled=self.latency.enabled,
        )
        self.recorder.close()

    def summarize(self) -> None:
        end = time.time()
        dur = max(1e-9, end - self.start) if self.active else 0.0
        lost = self.last_seq_seen - self.total_pkts if self.last_seq_seen > 0 else 0
        loss_pct = (
            100.0 * lost / self.last_seq_seen if self.last_seq_seen > 0 else 0.0
        )

        summary_fields = {
            "direction": "rx",
            "receiver": f"{self.bind_addr}:{self.port}",
            "sender": (
                None
                if not self.src_peer
                else f"{self.src_peer[0]}:{self.src_peer[1]}"
            ),
            "seconds": dur,
            "bytes": self.bytes_received,
            "packets": self.total_pkts,
            "bits_per_second": (
                (self.bytes_received * 8.0) / max(1e-9, dur) if dur > 0 else 0.0
            ),
            "lost_packets": lost,
            "lost_percent": loss_pct,
            "stop_reason": self.stop_reason,
        }
        summary_fields.update(self.latency.summary_fields())
        if self.recorder is not None:
            summary_fields["record_count"] = self.recorder.recorded
            summary_fields["record_dropped"] = self.recorder.dropped
        self.log.summary(**summary_fields)

    def _begin(self, packet: UdpPacket) -> None:
        now = packet.received_ns / 1e9
        self.log.test("rx", packet.peer[0], packet.peer[1], now)
        self.active = True
        self.start = now
        self.last_ts = now
        self.src_peer = packet.peer

    def _accept(self, packet: UdpPacket) -> bool:
        """Handle one datagram. Returns False when the test should stop."""
        now_ns = packet.received_ns
        now = now_ns / 1e9
        self.inactivity_deadline = now + self.inactivity_timeout

        if packet.latency_enabled:
            self.latency.enable()

        if packet.is_fin:
            self.stop_reason = "end-of-test"
            return False

        self.total_pkts += 1
        self.bytes_received += packet.size
        self.last_seq_seen = max(self.last_seq_seen, packet.seq)
        if self.recorder is not None:
            self.recorder.add(
                packet.timestamp_ns,
                now_ns,
                packet.size,
                packet.event_id,
                seq=packet.seq,
            )

        if self.latency.enabled:
            self.latency.observe(packet.timestamp_ns, now_ns)

        if (now - self.last_ts) >= 1.0:
            sent_packets = self.last_seq_seen - self.last_seq_seen_last
            received = self.total_pkts - self.last_pkts
            lost = max(0, sent_packets - received)
            percentage_lost = (
                100.0 * lost / sent_packets if sent_packets > 0 else 0.0
            )
            update_fields = {
                "start_ts": self.last_ts,
                "end_ts": now,
                "bytes": self.bytes_received - self.last_bytes,
                "packets": sent_packets,
                "lost_packets": lost,
                "lost_percent": percentage_lost,
            }
            update_fields.update(self.latency.interval_fields())
            self.log.update(**update_fields)
            self.last_ts = now
            self.last_bytes = self.bytes_received
            self.last_pkts = self.total_pkts
            self.last_seq_seen_last = self.last_seq_seen
            self.latency.reset_interval()
        return True


class UdpSender:
    """Send paced UDP datagrams and a FIN marker.

    Split out so a future reverse test can run this step from the
    server after a first-packet control datagram.
    """

    def __init__(
        self,
        sock: socket.socket,
        log: Logger,
        host: str,
        port: int,
        payload_len: int,
        controller: BasePacingController,
        enable_latency: bool,
    ):
        self.sock = sock
        self.log = log
        self.host = host
        self.port = port
        self.payload_len = payload_len
        self.controller = controller
        self.enable_latency = enable_latency
        self.payload = bytearray(payload_len)
        self.payload_view = memoryview(self.payload)
        self.seq = 1
        self.bytes_sent = 0
        self.pkts_sent = 0
        self.stop_reason = controller.stop_reason()
        self.start = 0.0
        self.last_ts = 0.0
        self.last_bytes = 0
        self.last_pkts = 0

    def send_datagram(
        self,
        send_len: int,
        flags: int = 0,
        seq: Optional[int] = None,
        event_id: Optional[int] = None,
        timestamp_ns: Optional[int] = None,
    ) -> int:
        """Pack a header and send one datagram. Returns bytes sent, or 0."""
        if seq is None:
            seq = self.seq
        if event_id is None:
            event_id = self.controller.current_event_id()
        if timestamp_ns is None:
            timestamp_ns = time.time_ns()
        UDP_HDR.pack_into(
            self.payload, 0, seq, timestamp_ns, flags, event_id
        )
        try:
            n = self.sock.send(self.payload_view[:send_len])
        except Exception as e:
            self.stop_reason = f"error sending packet: {e}"
            return 0
        if n <= 0:
            self.stop_reason = "send returned 0"
            return 0
        return n

    def run(self) -> None:
        """Send test data. The first datagram is payload (current behavior)."""
        self.sock.settimeout(None)
        self.start = time.time()
        self.last_ts = self.start
        self.controller.start()

        try:
            while not self.controller.should_stop():
                flags = LATENCY_FLAG if self.enable_latency else 0
                send_len = self.controller.next_send(self.payload_len)
                if send_len <= 0:
                    continue
                if send_len < UDP_HDR.size:
                    send_len = UDP_HDR.size
                elif send_len > self.payload_len:
                    send_len = self.payload_len

                try:
                    if self.bytes_sent == 0:
                        self.log.test("tx", self.host, self.port, self.start)
                    n = self.send_datagram(send_len, flags=flags)
                except Exception as e:
                    self.stop_reason = f"error sending packet: {e}"
                    break
                if n <= 0:
                    break
                self.bytes_sent += n
                self.pkts_sent += 1
                self.seq += 1
                now = time.time()
                if (now - self.last_ts) >= 1.0:
                    extra = self.controller.get_update_fields()
                    self.log.update(
                        start_ts=self.last_ts,
                        end_ts=now,
                        bytes=self.bytes_sent - self.last_bytes,
                        packets=self.pkts_sent - self.last_pkts,
                        **extra,
                    )
                    self.last_ts = now
                    self.last_bytes = self.bytes_sent
                    self.last_pkts = self.pkts_sent
        except KeyboardInterrupt:
            self.stop_reason = "interrupted"

    def send_fin(self) -> None:
        fin_flags = FIN_FLAG | (LATENCY_FLAG if self.enable_latency else 0)
        for _ in range(3):
            UDP_HDR.pack_into(
                self.payload, 0, self.seq, time.time_ns(), fin_flags, 0
            )
            try:
                self.sock.send(self.payload)
            except (Exception, KeyboardInterrupt):
                pass
            try:
                time.sleep(0.01)
            except KeyboardInterrupt:
                break

    def summarize(self) -> None:
        dur = max(1e-9, time.time() - self.start)
        self.log.summary(
            direction="tx",
            receiver=f"{self.host}:{self.port}",
            seconds=dur,
            bytes=self.bytes_sent,
            packets=self.pkts_sent,
            bits_per_second=(self.bytes_sent * 8.0) / dur,
            **self.controller.get_update_fields(),
            payload_len=self.payload_len,
            stop_reason=self.stop_reason,
        )


def server(
    log: Logger,
    bind_addr: str,
    port: int,
    record_path: Optional[str] = None,
    inactivity_timeout: float = 2.0,
    trace_dir: Optional[str] = None,
    record_metadata: Optional[dict] = None,
):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_addr, port))
    sock.settimeout(inactivity_timeout)
    log.start(bind_addr, port)

    receiver = UdpReceiver(
        sock,
        log,
        bind_addr,
        port,
        record_path=record_path,
        inactivity_timeout=inactivity_timeout,
        record_metadata=record_metadata,
    )
    try:
        first = receiver.wait_first()
        action = classify_first_packet(first)
        if action == ACTION_DATA:
            receiver.run(first)
        elif action == ACTION_REVERSE:
            _run_udp_reverse_send(
                sock,
                log,
                receiver,
                first,
                trace_dir=trace_dir,
            )
            return
        else:
            receiver.src_peer = first.peer
            receiver.stop_reason = f"unsupported first-packet action: {action}"
    except KeyboardInterrupt:
        receiver.stop_reason = "interrupted"
    finally:
        receiver.close_recorder()

    receiver.summarize()


def _run_udp_reverse_send(
    sock: socket.socket,
    log: Logger,
    receiver: UdpReceiver,
    first: UdpPacket,
    trace_dir: Optional[str] = None,
) -> None:
    payload = bytes(receiver.recv_buffer[UDP_HDR.size : first.size])
    try:
        sock.connect(first.peer)
    except Exception as e:
        receiver.src_peer = first.peer
        receiver.stop_reason = f"connect to peer failed: {e}"
        receiver.close_recorder()
        receiver.summarize()
        return

    try:
        config = unpack_reverse_config(payload)
        controller = controller_from_reverse_config(
            config, trace_dir=trace_dir
        )
    except ValueError as e:
        _send_udp_reverse_error(sock, str(e))
        receiver.src_peer = first.peer
        receiver.stop_reason = str(e)
        receiver.close_recorder()
        receiver.summarize()
        return

    payload_len = reverse_udp_payload_len(config)
    sender = UdpSender(
        sock, log, first.peer[0], first.peer[1], payload_len, controller, False
    )
    sender.run()
    sender.send_fin()
    sender.summarize()


def _send_udp_reverse_error(sock: socket.socket, message: str) -> None:
    packed = pack_error_message(message)
    for _ in range(3):
        write_udp_control(sock, UDP_CONTROL_SEQ_ERROR, packed)
        time.sleep(0.01)


def client(
    log: Logger,
    host: str,
    port: int,
    payload_len: int,
    controller: Optional[BasePacingController] = None,
    enable_latency: bool = False,
    bind_dev: Optional[str] = None,
    reverse_config: Optional[ReverseConfig] = None,
    inactivity_timeout: float = 2.0,
    record_path: Optional[str] = None,
    record_metadata: Optional[dict] = None,
):
    if payload_len < UDP_HDR.size:
        payload_len = UDP_HDR.size

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if bind_dev is not None:
        bind_to_device(sock, bind_dev)
    sock.connect((host, port))

    log.start(host, port)
    if reverse_config is not None:
        _run_udp_reverse_receive(
            sock,
            log,
            host,
            port,
            reverse_config,
            inactivity_timeout,
            record_path=record_path,
            record_metadata=record_metadata,
        )
        return

    if controller is None:
        raise ValueError("controller is required unless reverse_config is set")
    sender = UdpSender(
        sock, log, host, port, payload_len, controller, enable_latency
    )
    sender.run()
    sender.send_fin()
    sender.summarize()


def _run_udp_reverse_receive(
    sock: socket.socket,
    log: Logger,
    host: str,
    port: int,
    reverse_config: ReverseConfig,
    inactivity_timeout: float,
    record_path: Optional[str] = None,
    record_metadata: Optional[dict] = None,
) -> None:
    packed = pack_reverse_config(reverse_config)
    last_err = None
    sent = False
    for _ in range(3):
        last_err = write_udp_control(sock, UDP_CONTROL_SEQ_REVERSE, packed)
        if last_err is None:
            sent = True
        time.sleep(0.01)
    if not sent:
        log.summary(
            direction="rx",
            receiver=f"{host}:{port}",
            seconds=0,
            bytes=0,
            packets=0,
            bits_per_second=0,
            stop_reason=last_err or "error sending packet",
        )
        return

    sock.settimeout(inactivity_timeout)
    local = sock.getsockname()
    receiver = UdpReceiver(
        sock,
        log,
        local[0],
        local[1],
        record_path=record_path,
        inactivity_timeout=inactivity_timeout,
        record_metadata=record_metadata,
    )
    try:
        first = receiver.wait_first()
        action = classify_first_packet(first)
        if action == ACTION_ERROR:
            payload = bytes(receiver.recv_buffer[UDP_HDR.size : first.size])
            receiver.src_peer = first.peer
            receiver.stop_reason = unpack_error_message(payload)
        elif action != ACTION_DATA:
            receiver.src_peer = first.peer
            receiver.stop_reason = f"unexpected first-packet action: {action}"
        else:
            receiver.run(first)
    except KeyboardInterrupt:
        receiver.stop_reason = "interrupted"
    finally:
        receiver.close_recorder()
    receiver.summarize()
