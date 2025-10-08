from __future__ import annotations
import argparse
import socket
import time
from typing import Optional

from .logger import Logger
from .controllers import (
    BasePacingController,
    StaticPacingController,
)


def set_tcp_mss(sock: socket.socket, mss: int):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, mss)
    except Exception as e:
        raise argparse.ArgumentTypeError(f"Failed to set TCP_MAXSEG: {e}")


def server(log: Logger, bind_addr: str, port: int, congestion_control: Optional[str]):
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
    stop_reason = "unknown"
    while True:
        try:
            n = conn.recv_into(data)
            if n == 0:
                stop_reason = "end-of-test"
                break
            bytes_recv += n
            now = time.time()
            if (now - last_ts) >= 1:
                log.update(
                    start_ts=last_ts,
                    end_ts=now,
                    bytes=bytes_recv - last_bytes,
                )
                last_ts = now
                last_bytes = bytes_recv
        except socket.timeout:
            continue
    end = time.time()
    dur = max(1e-9, end - start)
    log.summary(
        receiver=f"{bind_addr}:{port}",
        sender=f"{addr[0]}:{addr[1]}",
        seconds=dur,
        bytes=bytes_recv,
        bits_per_second=(bytes_recv * 8.0) / dur,
        stop_reason=stop_reason,
    )
    conn.close()


def client(
    log: Logger,
    host: str,
    port: int,
    congestion_control: Optional[str],
    target_duration: int | None,
    set_mss: Optional[int],
    bandwidth_bps: Optional[float] = None,
    controller: Optional[BasePacingController] = None,
):
    try:
        effective_controller = controller or StaticPacingController(
            bandwidth_bps, set_mss if set_mss else 1200
        )
        sock = prepare_client_socket(log, host, port, congestion_control, set_mss)
        if sock is None:
            return
        client_core(log, sock, host, port, target_duration, effective_controller)
    finally:
        effective_controller.request_stop()


def client_core(
    log: Logger,
    sock: socket.socket,
    host: str,
    port: int,
    target_duration: int | None,
    controller: BasePacingController,
):
    log.start(host, port)

    payload = b"\x00" * (64 * 1024)
    view = memoryview(payload)
    start = time.time()
    last_ts = start
    last_bytes = 0
    end_time = None if target_duration is None else start + target_duration
    bytes_sent = 0
    stop_reason = "unknown"

    while True:
        if (
            end_time is not None and time.time() >= end_time
        ) or controller.should_stop():
            stop_reason = (
                "duration" if controller.should_stop() is False else "user-stop"
            )
            break
        if bytes_sent == 0:
            log.test(host, port, start)

        to_send = 1200
        if controller.is_pacing():
            controller.maybe_sleep(to_send)

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
