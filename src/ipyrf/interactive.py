from __future__ import annotations

import os
import sys
import time
import threading
import termios
import fcntl
import select

from .utils import human_bps
from .token_bucket import TokenBucket
from .controllers import BasePacingController


class DynamicTokenBucket(TokenBucket):
    """Token bucket that allows updating the target rate at runtime."""

    def set_rate_bps(self, new_rate_bps: float, quantum_bytes: int):
        # Convert bits/sec -> bytes/sec as used internally by TokenBucket
        self.rate_bps = max(1e-6, new_rate_bps / 8.0)
        self.capacity = max(quantum_bytes * 2, int(self.rate_bps))
        self.tokens = min(self.tokens, self.capacity)


class KeyReader:
    """Non-blocking key reader that recognizes arrow keys and a few chars.

    Reads from /dev/tty (the controlling terminal), so stdout can be redirected.
    Prevents terminal echo so arrow keys won't shift the cursor (no stray spaces).
    """

    def __init__(self):
        self.tty_path = "/dev/tty"
        self.fd = None
        self.orig_attrs = None
        self.orig_flags = None

    def __enter__(self):
        try:
            self.fd = os.open(self.tty_path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            self.fd = sys.stdin.fileno()

        self.orig_attrs = termios.tcgetattr(self.fd)
        attrs = termios.tcgetattr(self.fd)
        lflag = attrs[3]
        attrs[3] = lflag & ~(termios.ECHO | termios.ICANON | termios.IEXTEN)
        cc = list(attrs[6])
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        attrs[6] = cc
        termios.tcsetattr(self.fd, termios.TCSADRAIN, attrs)

        self.orig_flags = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        fcntl.fcntl(self.fd, fcntl.F_SETFL, self.orig_flags | os.O_NONBLOCK)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.orig_attrs is not None:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.orig_attrs)
            if self.orig_flags is not None:
                fcntl.fcntl(self.fd, fcntl.F_SETFL, self.orig_flags)
            if self.fd not in (None, sys.stdin.fileno()):
                os.close(self.fd)
        except Exception:
            pass

    def read_keys(self, stop_event: threading.Event, on_key):
        buf = bytearray()
        allowed_chars = {"q", "Q", "0", "u", "U"}
        while not stop_event.is_set():
            try:
                rlist, _, _ = select.select([self.fd], [], [], 0.05)
            except Exception:
                rlist = []
            if not rlist:
                continue
            try:
                chunk = os.read(self.fd, 16)
            except BlockingIOError:
                continue
            except Exception:
                break
            if not chunk:
                continue
            buf.extend(chunk)
            while True:
                if not buf:
                    break
                c = buf[0]
                if c == 0x1B:  # ESC
                    if len(buf) >= 3 and buf[1] == 0x5B:
                        code = buf[2]
                        if code in (0x41, 0x42, 0x43, 0x44):  # A,B,C,D
                            del buf[:3]
                            mapping = {
                                0x41: "UP",
                                0x42: "DOWN",
                                0x43: "RIGHT",
                                0x44: "LEFT",
                            }
                            on_key(mapping[code])
                            continue
                    if len(buf) < 3:
                        break
                    del buf[0]
                    continue
                else:
                    ch = chr(c)
                    del buf[0]
                    if ch in allowed_chars:
                        on_key(ch)
                    continue


class InteractiveController(BasePacingController):
    def __init__(
        self, initial_bps: float | None, quantum_bytes: int, interval: float = 1.0
    ):
        super().__init__(interval_seconds=interval)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.quantum = quantum_bytes
        self.pacing = initial_bps is not None
        self.target_bps = (
            float(initial_bps) if initial_bps is not None else float("inf")
        )
        self.tb: DynamicTokenBucket | None = (
            DynamicTokenBucket(self.target_bps, self.quantum) if self.pacing else None
        )
        self.keyloop_thread = start_interactive_keys(self, initial_bps)

    def is_pacing(self) -> bool:
        with self.lock:
            return self.pacing and self.target_bps != float("inf")

    def maybe_sleep(self, n_bytes: int):
        tb = None
        with self.lock:
            tb = self.tb
        if tb is None:
            return
        while True:
            sleep_time = tb.take(n_bytes)
            if sleep_time <= 0:
                break
            time.sleep(sleep_time)

    def get_update_fields(self):
        with self.lock:
            if self.pacing and self.target_bps != float("inf"):
                return {"target_bandwidth_bps": self.target_bps}
            return {}

    def should_stop(self) -> bool:
        return self.stop_event.is_set()

    def stop_reason(self) -> str:
        return "user-stop"

    def reset(self, initial_bps: float | None):
        with self.lock:
            if initial_bps is None:
                self.target_bps = float("inf")
                self.pacing = False
                self.tb = None
            else:
                self.target_bps = float(initial_bps)
                self.pacing = True
                if self.tb is None:
                    self.tb = DynamicTokenBucket(self.target_bps, self.quantum)
                else:
                    self.tb.set_rate_bps(self.target_bps, self.quantum)

    def unlimited(self):
        with self.lock:
            self.pacing = False
            self.target_bps = float("inf")
            self.tb = None

    def bump(self, delta_bps: float = 0.0, scale: float = 1.0):
        with self.lock:
            if not self.pacing or self.target_bps == float("inf"):
                self.pacing = True
                if not (self.target_bps != float("inf")):
                    self.target_bps = 50e6
                if self.tb is None:
                    self.tb = DynamicTokenBucket(self.target_bps, self.quantum)
            if scale != 1.0:
                self.target_bps = max(1e3, self.target_bps * scale)
            else:
                self.target_bps = max(1e3, self.target_bps + delta_bps)
            self.tb.set_rate_bps(self.target_bps, self.quantum)
            return self.target_bps

    def request_stop(self):
        self.stop_event.set()

    def stop(self):
        if self.keyloop_thread is not None:
            self.keyloop_thread.join()
            self.keyloop_thread = None


def start_interactive_keys(
    controller: InteractiveController, initial_bps: float | None
):
    def on_key(k: str):
        if k == "RIGHT":
            new = controller.bump(delta_bps=1e6)
            print(f"[interactive] target = {human_bps(new)}", file=sys.stderr)
        elif k == "LEFT":
            new = controller.bump(delta_bps=-1e6)
            print(f"[interactive] target = {human_bps(new)}", file=sys.stderr)
        elif k == "UP":
            new = controller.bump(scale=1.10)
            print(f"[interactive] target = {human_bps(new)}", file=sys.stderr)
        elif k == "DOWN":
            new = controller.bump(scale=0.90)
            print(f"[interactive] target = {human_bps(new)}", file=sys.stderr)
        elif k == "0":
            controller.reset(initial_bps)
            print(
                f"[interactive] reset -> {human_bps(initial_bps if initial_bps is not None else float('inf'))}",
                file=sys.stderr,
            )
        elif k in ("u", "U"):
            controller.unlimited()
            print("[interactive] pacing: unlimited", file=sys.stderr)
        elif k in ("q", "Q"):
            controller.request_stop()

    print(
        "[interactive] Controls: ← -1 Mbps, -> +1 Mbps, ↓ -10%, ↑ +10%, 0 reset, u unlimited, q quit, Ctrl+C exit",
        file=sys.stderr,
    )
    print(
        f"[interactive] starting at {human_bps(initial_bps if initial_bps is not None else float('inf'))}",
        file=sys.stderr,
    )

    def _keyloop():
        with KeyReader() as kr:
            kr.read_keys(controller.stop_event, on_key)

    keyloop_thread = threading.Thread(target=_keyloop, daemon=True)
    keyloop_thread.start()
    return keyloop_thread
