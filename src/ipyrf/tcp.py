from __future__ import annotations
import argparse
import socket
import struct
import time
from dataclasses import dataclass
from typing import Optional

from .handshake import ACTION_DATA, classify_tcp_flag
from .latency import LatencyTracker
from .logger import Logger
from .controllers import BasePacingController
from .utils import bind_to_device

# TCP record header: latency flag, payload length, send timestamp (ns), event id
TCP_HDR = struct.Struct("!BIQI")
TCP_HDR_SIZE = TCP_HDR.size
MAX_TCP_PAYLOAD = 64 * 1024
DEFAULT_TCP_PAYLOAD = 1200
LATENCY_ENABLED = 1
LATENCY_DISABLED = 0


@dataclass(frozen=True)
class TcpRecord:
    latency_flag: int
    payload_len: int
    timestamp_ns: int
    event_id: int


def set_tcp_mss(sock: socket.socket, mss: int):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, mss)
    except Exception as e:
        raise argparse.ArgumentTypeError(f"Failed to set TCP_MAXSEG: {e}")


def parse_tcp_record(
    recv_buffer: bytearray,
) -> tuple[Optional[TcpRecord], Optional[str]]:
    """Consume one complete record from ``recv_buffer``.

    Returns ``(record, None)`` if a record was consumed, ``(None, None)``
    if more bytes are needed, or ``(None, stop_reason)`` on error.
    """
    if len(recv_buffer) < TCP_HDR_SIZE:
        return None, None
    latency_flag, payload_len, timestamp_ns, event_id = TCP_HDR.unpack_from(
        recv_buffer
    )
    if payload_len > MAX_TCP_PAYLOAD:
        return None, f"invalid payload length: {payload_len}"
    record_size = TCP_HDR_SIZE + payload_len
    if len(recv_buffer) < record_size:
        return None, None
    del recv_buffer[:record_size]
    return TcpRecord(latency_flag, payload_len, timestamp_ns, event_id), None


def classify_first_record(record: Optional[TcpRecord]) -> str:
    """Return the first-packet action for ``record``.

    ``None`` (peer closed before a complete record) is treated as data
    so the receive path can summarize an empty/aborted test. Non-data
    flags are reserved for reverse/config (see :mod:`ipyrf.handshake`).
    """
    if record is None:
        return ACTION_DATA
    return classify_tcp_flag(record.latency_flag)


class TcpReceiver:
    """Receive framed TCP records and log throughput / latency.

    Split out so a future first-packet control action can run a send
    step instead of this receive step on the same connection.
    """

    def __init__(
        self,
        conn: socket.socket,
        log: Logger,
        interval_seconds: float,
        bind_addr: str,
        port: int,
        peer: tuple[str, int],
    ):
        self.conn = conn
        self.log = log
        self.interval_seconds = interval_seconds
        self.bind_addr = bind_addr
        self.port = port
        self.peer = peer
        self.recv_buffer = bytearray()
        self._scratch = bytearray(MAX_TCP_PAYLOAD)
        self.bytes_recv = 0
        self.stop_reason = "unknown"
        self.start = 0.0
        self.last_ts = 0.0
        self.last_bytes = 0
        self.latency = LatencyTracker()
        self._started = False

    def begin(self, start: Optional[float] = None) -> None:
        self.start = time.time() if start is None else start
        self.last_ts = self.start
        self.log.test(self.peer[0], self.peer[1], self.start)
        self._started = True

    def read_first_record(self) -> Optional[TcpRecord]:
        """Block until one complete record, EOF, or a parse error."""
        return self._read_record()

    def run(self, first: Optional[TcpRecord] = None) -> None:
        """Receive test data. ``first`` is included when it is payload."""
        if first is not None:
            self._apply_record(first)
            if not self._drain_complete_records():
                return
            self._maybe_interval_log(time.time())

        while True:
            try:
                n = self.conn.recv_into(self._scratch)
            except socket.timeout:
                continue
            if n == 0:
                self.stop_reason = "end-of-test"
                break
            self.bytes_recv += n
            self.recv_buffer.extend(self._scratch[:n])
            if not self._drain_complete_records():
                break
            self._maybe_interval_log(time.time())

    def summarize(self) -> None:
        end = time.time()
        dur = max(1e-9, end - self.start) if self._started else 0.0
        summary_fields = {
            "receiver": f"{self.bind_addr}:{self.port}",
            "sender": f"{self.peer[0]}:{self.peer[1]}",
            "seconds": dur,
            "bytes": self.bytes_recv,
            "bits_per_second": (self.bytes_recv * 8.0) / dur if dur > 0 else 0.0,
            "stop_reason": self.stop_reason,
        }
        summary_fields.update(self.latency.summary_fields())
        self.log.summary(**summary_fields)

    def _read_record(self) -> Optional[TcpRecord]:
        while True:
            record, error = parse_tcp_record(self.recv_buffer)
            if error is not None:
                self.stop_reason = error
                return None
            if record is not None:
                return record
            try:
                n = self.conn.recv_into(self._scratch)
            except socket.timeout:
                continue
            if n == 0:
                self.stop_reason = "end-of-test"
                return None
            self.bytes_recv += n
            self.recv_buffer.extend(self._scratch[:n])

    def _drain_complete_records(self) -> bool:
        """Parse all complete records already in the buffer.

        Returns False when parsing should stop the test.
        """
        while True:
            record, error = parse_tcp_record(self.recv_buffer)
            if error is not None:
                self.stop_reason = error
                return False
            if record is None:
                return True
            self._apply_record(record)

    def _apply_record(self, record: TcpRecord) -> None:
        if record.latency_flag == LATENCY_ENABLED:
            self.latency.enable()
        if self.latency.enabled:
            self.latency.observe(record.timestamp_ns)

    def _maybe_interval_log(self, now: float) -> None:
        if (now - self.last_ts) < self.interval_seconds:
            return
        update_fields = {
            "start_ts": self.last_ts,
            "end_ts": now,
            "bytes": self.bytes_recv - self.last_bytes,
        }
        update_fields.update(self.latency.interval_fields())
        self.log.update(**update_fields)
        self.last_ts = now
        self.last_bytes = self.bytes_recv
        self.latency.reset_interval()


class TcpSender:
    """Send paced, framed TCP records.

    Split out so a future reverse test can run this step on the accepted
    server connection after a first-packet control record.
    """

    def __init__(
        self,
        sock: socket.socket,
        log: Logger,
        controller: BasePacingController,
        enable_latency: bool,
        host: str,
        port: int,
    ):
        self.sock = sock
        self.log = log
        self.controller = controller
        self.enable_latency = enable_latency
        self.host = host
        self.port = port
        self.bytes_sent = 0
        self.stop_reason = "unknown"
        self.start = 0.0
        self.last_ts = 0.0
        self.last_bytes = 0
        self._payload = b"\x00" * MAX_TCP_PAYLOAD

    def send_record(
        self,
        payload_len: int,
        latency_flag: Optional[int] = None,
        timestamp_ns: Optional[int] = None,
        event_id: Optional[int] = None,
    ) -> bool:
        """Send one framed record. Returns False if sending should stop.

        ``latency_flag`` defaults to the test latency setting. Passing a
        reserved handshake flag sends a control record instead of data.
        """
        if latency_flag is None:
            latency_flag = (
                LATENCY_ENABLED if self.enable_latency else LATENCY_DISABLED
            )
        if timestamp_ns is None:
            timestamp_ns = time.time_ns()
        if event_id is None:
            event_id = self.controller.current_event_id()

        header = TCP_HDR.pack(latency_flag, payload_len, timestamp_ns, event_id)
        if not self._send_all(header):
            return False
        if payload_len > 0:
            view = memoryview(self._payload)
            if not self._send_all(view[:payload_len]):
                return False
        return True

    def run(self) -> None:
        """Send test data. The first record is payload (current behavior)."""
        self.start = time.time()
        self.last_ts = self.start
        self.controller.start()

        while True:
            if self.controller.should_stop():
                self.stop_reason = self.controller.stop_reason()
                break

            to_send = self.controller.next_send(DEFAULT_TCP_PAYLOAD)
            if to_send <= 0:
                continue
            if to_send > MAX_TCP_PAYLOAD:
                to_send = MAX_TCP_PAYLOAD

            if self.bytes_sent == 0:
                self.log.test(self.host, self.port, self.start)

            sent = self.send_record(to_send)

            now = time.time()
            if (now - self.last_ts) >= self.controller.interval_seconds:
                self.log.update(
                    start_ts=self.last_ts,
                    end_ts=now,
                    bytes=self.bytes_sent - self.last_bytes,
                    **self.controller.get_update_fields(),
                )
                self.last_ts = now
                self.last_bytes = self.bytes_sent

            if not sent:
                break
            if self.stop_reason != "unknown" and self.stop_reason != "duration":
                break

    def finish(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_WR)
        except Exception:
            pass

        self.sock.settimeout(1.0)
        try:
            while self.sock.recv(4096):
                pass
        except Exception:
            pass
        self.sock.close()
        actual_duration = max(1e-9, time.time() - self.start)
        self.log.summary(
            receiver=f"{self.host}:{self.port}",
            seconds=actual_duration,
            bytes=self.bytes_sent,
            bits_per_second=(self.bytes_sent * 8.0) / actual_duration,
            stop_reason=self.stop_reason,
            **self.controller.get_update_fields(),
        )

    def _send_all(self, data) -> bool:
        offset = 0
        length = len(data)
        while offset < length:
            try:
                n = self.sock.send(data[offset:])
            except (BlockingIOError, InterruptedError):
                continue
            except Exception as e:
                self.stop_reason = f"error sending: {e}"
                return False
            if n <= 0:
                self.stop_reason = "send returned 0"
                return False
            offset += n
            self.bytes_sent += n
        return True


def server(
    log: Logger,
    bind_addr: str,
    port: int,
    interval_seconds: float,
    congestion_control: Optional[str],
):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind_addr, port))
    srv.listen(1)
    log.start(bind_addr, port)

    try:
        conn, addr = srv.accept()
        accept_ts = time.time()
    finally:
        srv.close()
    _prepare_server_connection(conn, congestion_control)

    receiver = TcpReceiver(
        conn, log, interval_seconds, bind_addr, port, addr
    )
    first = receiver.read_first_record()
    action = classify_first_record(first)

    if action == ACTION_DATA:
        # First record is test payload (or the peer closed before one).
        receiver.begin(accept_ts)
        if first is not None:
            receiver.run(first=first)
    else:
        # Future: ACTION_REVERSE -> TcpSender(conn, ...).run()
        # Future: ACTION_CONFIG -> read config, then send or receive.
        receiver.stop_reason = f"unsupported first-packet action: {action}"

    receiver.summarize()
    conn.close()


def client(
    log: Logger,
    host: str,
    port: int,
    congestion_control: Optional[str],
    set_mss: Optional[int],
    controller: BasePacingController,
    enable_latency: bool = False,
    bind_dev: Optional[str] = None,
):
    sock = prepare_client_socket(
        log, host, port, congestion_control, set_mss, bind_dev=bind_dev
    )
    if sock is None:
        return

    log.start(host, port)
    sender = TcpSender(sock, log, controller, enable_latency, host, port)
    # First record is test payload. Future reverse/config would send a
    # control record via sender.send_record(..., latency_flag=...) first,
    # then run TcpReceiver on this socket instead of sender.run().
    sender.run()
    sender.finish()


def _prepare_server_connection(
    conn: socket.socket, congestion_control: Optional[str]
) -> None:
    try:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass

    if congestion_control is not None:
        try:
            conn.setsockopt(
                socket.IPPROTO_TCP,
                socket.TCP_CONGESTION,
                congestion_control.encode("utf-8"),
            )
        except Exception as e:
            raise argparse.ArgumentTypeError(
                f"Failed to set TCP congestion control " f"'{congestion_control}': {e}"
            )

    conn.settimeout(1.0)


def prepare_client_socket(
    log: Logger,
    host: str,
    port: int,
    congestion_control: Optional[str],
    set_mss: Optional[int],
    bind_dev: Optional[str] = None,
) -> Optional[socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if congestion_control is not None:
        try:
            sock.setsockopt(
                socket.IPPROTO_TCP,
                socket.TCP_CONGESTION,
                congestion_control.encode("utf-8"),
            )
        except Exception as e:
            raise argparse.ArgumentTypeError(
                f"Failed to set TCP congestion control " f"'{congestion_control}': {e}"
            )

    if set_mss:
        set_tcp_mss(sock, set_mss)
    if bind_dev is not None:
        bind_to_device(sock, bind_dev)
    try:
        sock.connect((host, port))
    except Exception as e:
        log.summary(
            peer=f"{host}:{port}",
            stop_reason=f"connection failed: {e}",
            seconds=0,
            bytes=0,
            bits_per_second=0,
        )
        return None
    return sock
