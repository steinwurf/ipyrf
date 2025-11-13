from __future__ import annotations
import argparse
import socket
import struct
import time
from typing import Optional

from .logger import Logger
from .controllers import BasePacingController

# TCP header: latency flag (B = 1 byte) + timestamp (Q = 8 bytes, nanoseconds)
TCP_LATENCY_FLAG = struct.Struct("!B")
TCP_TIMESTAMP = struct.Struct("!Q")
TCP_HDR_SIZE = TCP_LATENCY_FLAG.size + TCP_TIMESTAMP.size
LATENCY_ENABLED = 1
LATENCY_DISABLED = 0


def set_tcp_mss(sock: socket.socket, mss: int):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, mss)
    except Exception as e:
        raise argparse.ArgumentTypeError(f"Failed to set TCP_MAXSEG: {e}")


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

    conn, addr = srv.accept()
    start = time.time()
    last_ts = start
    bytes_recv = 0
    last_bytes = 0

    # Latency tracking (enabled if client sets flag)
    latency_enabled = False
    latency_sum = 0.0
    latency_count = 0
    latency_min = float("inf")
    latency_max = 0.0
    interval_latency_sum = 0.0
    interval_latency_count = 0
    interval_latency_min = float("inf")
    interval_latency_max = 0.0
    # Max latency cap: 1 second (reasonable for same-machine scenarios)
    max_latency_cap = 1.0

    log.test(addr[0], addr[1], start)

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
    data = bytearray(64 * 1024)
    recv_buffer = bytearray()
    stop_reason = "unknown"
    while True:
        try:
            n = conn.recv_into(data)
            if n == 0:
                stop_reason = "end-of-test"
                break
            bytes_recv += n

            # Try to extract timestamp from data
            # Add received data to buffer
            recv_buffer.extend(data[:n])

            # Try to extract headers from buffer
            while len(recv_buffer) >= TCP_HDR_SIZE:
                try:
                    latency_flag = TCP_LATENCY_FLAG.unpack_from(recv_buffer)[0]
                    timestamp_ns = TCP_TIMESTAMP.unpack_from(
                        recv_buffer, TCP_LATENCY_FLAG.size
                    )[0]

                    # Check if client wants latency tracking enabled
                    if latency_flag == LATENCY_ENABLED:
                        latency_enabled = True

                    # Calculate latency if enabled and timestamp valid
                    if latency_enabled and timestamp_ns > 0:
                        now = time.time()
                        timestamp_s = timestamp_ns / 1e9
                        latency = now - timestamp_s
                        # Only include if latency is within the max cap
                        if latency >= 0 and latency <= max_latency_cap:
                            latency_sum += latency
                            latency_count += 1
                            latency_min = min(latency_min, latency)
                            latency_max = max(latency_max, latency)
                            interval_latency_sum += latency
                            interval_latency_count += 1
                            interval_latency_min = min(interval_latency_min, latency)
                            interval_latency_max = max(interval_latency_max, latency)
                    # Remove header from buffer (header + payload chunk)
                    # We remove header + 1200 bytes (typical chunk size)
                    chunk_size = TCP_HDR_SIZE + 1200
                    if len(recv_buffer) >= chunk_size:
                        recv_buffer = recv_buffer[chunk_size:]
                    else:
                        # Not enough data for full chunk, wait for more
                        break
                except (struct.error, IndexError):
                    # Header not available or invalid, skip latency calc
                    # Try to recover by removing one byte and trying again
                    if len(recv_buffer) > 0:
                        recv_buffer = recv_buffer[1:]
                    else:
                        break

            now = time.time()
            if (now - last_ts) >= interval_seconds:
                update_fields = {
                    "start_ts": last_ts,
                    "end_ts": now,
                    "bytes": bytes_recv - last_bytes,
                }

                # Add latency statistics if available
                if interval_latency_count > 0:
                    update_fields["latency_avg"] = (
                        interval_latency_sum / interval_latency_count
                    )
                    update_fields["latency_min"] = interval_latency_min
                    update_fields["latency_max"] = interval_latency_max
                    update_fields["latency_count"] = interval_latency_count

                log.update(**update_fields)

                last_ts = now
                last_bytes = bytes_recv
                # Reset interval latency tracking
                interval_latency_sum = 0.0
                interval_latency_count = 0
                interval_latency_min = float("inf")
                interval_latency_max = 0.0
        except socket.timeout:
            continue
    end = time.time()
    dur = max(1e-9, end - start)

    summary_fields = {
        "receiver": f"{bind_addr}:{port}",
        "sender": f"{addr[0]}:{addr[1]}",
        "seconds": dur,
        "bytes": bytes_recv,
        "bits_per_second": (bytes_recv * 8.0) / dur,
        "stop_reason": stop_reason,
    }

    # Add overall latency statistics if available
    if latency_count > 0:
        summary_fields["latency_avg"] = latency_sum / latency_count
        summary_fields["latency_min"] = latency_min
        summary_fields["latency_max"] = latency_max
        summary_fields["latency_count"] = latency_count

    log.summary(**summary_fields)
    conn.close()


def client(
    log: Logger,
    host: str,
    port: int,
    congestion_control: Optional[str],
    set_mss: Optional[int],
    controller: BasePacingController,
    enable_latency: bool = False,
):
    sock = prepare_client_socket(log, host, port, congestion_control, set_mss)
    if sock is None:
        return

    log.start(host, port)

    payload = b"\x00" * (64 * 1024)
    view = memoryview(payload)
    start = time.time()
    last_ts = start
    last_bytes = 0
    bytes_sent = 0
    stop_reason = "unknown"

    # Start timing if the controller has a duration
    controller.start()

    while True:
        if controller.should_stop():
            stop_reason = controller.stop_reason()
            break
        if bytes_sent == 0:
            log.test(host, port, start)

        to_send = 1200
        if controller.is_pacing():
            controller.maybe_sleep(to_send)

        # Prepend latency flag + timestamp header to the chunk
        latency_flag = LATENCY_ENABLED if enable_latency else LATENCY_DISABLED
        timestamp_ns = time.time_ns()
        header = TCP_LATENCY_FLAG.pack(latency_flag) + TCP_TIMESTAMP.pack(timestamp_ns)

        # Send header first
        header_offset = 0
        while header_offset < TCP_HDR_SIZE:
            try:
                n = sock.send(header[header_offset:])
            except (BlockingIOError, InterruptedError):
                continue
            except Exception as e:
                stop_reason = f"error sending: {e}"
                break
            if n <= 0:
                stop_reason = "send returned 0"
                break
            header_offset += n
            bytes_sent += n

        if stop_reason != "unknown":
            break

        # Then send payload
        offset = 0
        while offset < to_send:
            try:
                n = sock.send(view[offset:to_send])
            except (BlockingIOError, InterruptedError):
                continue
            except Exception as e:
                stop_reason = f"error sending: {e}"
                offset = to_send
                break
            if n <= 0:
                stop_reason = "send returned 0"
                offset = to_send
                break
            offset += n
            bytes_sent += n

        now = time.time()
        if (now - last_ts) >= controller.interval_seconds:
            log.update(
                start_ts=last_ts,
                end_ts=now,
                bytes=bytes_sent - last_bytes,
                **controller.get_update_fields(),
            )
            last_ts = now
            last_bytes = bytes_sent

        if stop_reason != "unknown" and stop_reason != "duration":
            break

    try:
        sock.shutdown(socket.SHUT_WR)
    except Exception:
        pass

    sock.settimeout(1.0)
    try:
        while sock.recv(4096):
            pass
    except Exception:
        pass
    sock.close()
    actual_duration = max(1e-9, time.time() - start)
    log.summary(
        receiver=f"{host}:{port}",
        seconds=actual_duration,
        bytes=bytes_sent,
        bits_per_second=(bytes_sent * 8.0) / actual_duration,
        stop_reason=stop_reason,
        **controller.get_update_fields(),
    )


def prepare_client_socket(
    log: Logger,
    host: str,
    port: int,
    congestion_control: Optional[str],
    set_mss: Optional[int],
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
