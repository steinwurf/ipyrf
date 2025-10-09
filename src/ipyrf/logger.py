from __future__ import annotations
import json
import sys

from .utils import human_readable_bytes


log_types = ["start", "test", "update", "summary"]
log_directions = ["tx", "rx"]
log_modes = ["tcp", "udp"]


class Logger:
    def __init__(
        self, json_log: bool, mode: str, role: str, logfile: str | None = None
    ):
        self.json_log = json_log
        assert mode in log_modes
        self.mode = mode
        self.direction = "tx" if role == "client" else "rx"
        assert self.direction in log_directions
        self.test_start_time = None
        self.logfile = logfile

    def start(self, ip: str, port: int):
        self._log(
            log_type="start",
            mode=self.mode,
            direction=self.direction,
            address=f"{ip}:{port}",
        )

    def test(self, peer_ip: str, peer_port: int, start_ts: float):
        self._log(
            log_type="test",
            peer=f"{peer_ip}:{peer_port}",
            ts=start_ts,
        )
        self.test_start_time = start_ts

    def update(self, start_ts: float, end_ts: float, bytes: int, **obj):
        if self.test_start_time is None:
            raise RuntimeError("test() must be called before update()")
        end_ts -= self.test_start_time
        start_ts -= self.test_start_time
        delta_t = end_ts - start_ts

        bps = (bytes * 8.0) / delta_t if delta_t > 0 else 0.0
        self._log(
            log_type="update",
            start=start_ts,
            end=end_ts,
            bytes=bytes,
            bits_per_second=bps,
            **obj,
        )

    def summary(self, **obj):
        self._log(log_type="summary", **obj)

    def write(self, message: str):
        if self.logfile:
            with open(self.logfile, "a", buffering=1) as f:
                f.write(message + "\n")
        else:
            print(message)
            sys.stdout.flush()

    def _log(self, log_type, **obj):
        if self.json_log:
            obj["type"] = log_type
            obj["mode"] = self.mode
            obj["direction"] = self.direction
            self.write(json.dumps(obj, separators=(",", ":")) + "\n")
            return
        assert log_type in log_types
        if log_type == "start":
            self.write(
                f"▶ {self.mode.upper()} {self.direction.upper()} — {obj['address']}"
            )
        elif log_type == "test":
            self.write(f"▶ TEST peer={obj['peer']}  ts={obj['ts']}")
        elif log_type == "update":
            message = (
                f"⏱ {obj['start']:.2f}-{obj['end']:.2f} sec"
                f" | {human_readable_bytes(obj['bytes'])}"
            )
            if "target_bandwidth_bps" in obj:
                message += f" | {obj['bits_per_second'] / 1e6:.2f}/{obj['target_bandwidth_bps'] / 1e6:.2f} Mbps"
            else:
                message += f" | {obj['bits_per_second'] / 1e6:.2f} Mbps"
            if "lost_packets" in obj and "packets" in obj:
                message += f" | {obj['lost_packets']}/{obj['packets']} lost ({obj['lost_percent']:.1f}%)"
            elif "packets" in obj:
                message += f" | {obj['packets']} pkts"
            self.write(message)
        elif log_type == "summary":
            self.write(
                "\n━ SUMMARY ━\n"
                f"  {self.mode.upper()} {self.direction.upper()}\n"
                f"  {obj.get('sender', '')} → {obj.get('receiver', '')}\n"
                f"  duration : {obj['seconds']:.2f} sec\n"
                f"  data     : {human_readable_bytes(obj['bytes'])}\n"
                f"  rate     : {obj['bits_per_second'] / 1e6:.2f} Mbps\n"
                f"  reason   : {obj.get('stop_reason', '')}\n"
            )
