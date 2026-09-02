"""Measure clock offset between this host and a remote machine over SSH.

Each sample is a Cristian / NTP-style round trip: local send time ``t0``,
the remote's ``time.time_ns()``, then local receive time ``t1``. Offset is
``remote - (t0 + t1) // 2``; a positive value means the remote clock is
ahead of this host. The reported offset comes from the sample with the
smallest RTT, where the symmetric-delay assumption is strongest.
"""

from __future__ import annotations

import json
import shlex
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import List, Optional, Sequence, TextIO


class OffsetError(RuntimeError):
    """Raised when the remote clock probe fails."""


# Runs on the remote. Each stdin line is answered with time.time_ns().
REMOTE_CODE = """
import sys
import time

for line in sys.stdin:
    print(time.time_ns(), flush=True)
"""


@dataclass(frozen=True)
class OffsetSample:
    t0_ns: int
    t1_ns: int
    remote_ns: int
    offset_ns: int
    rtt_ns: int

    @classmethod
    def from_times(cls, t0_ns: int, t1_ns: int, remote_ns: int) -> "OffsetSample":
        return cls(
            t0_ns=t0_ns,
            t1_ns=t1_ns,
            remote_ns=remote_ns,
            offset_ns=remote_ns - (t0_ns + t1_ns) // 2,
            rtt_ns=t1_ns - t0_ns,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OffsetResult:
    host: str
    samples: List[OffsetSample]
    offset_ns: int
    median_offset_ns: int
    min_rtt_ns: int
    mean_rtt_ns: int

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "samples": [s.to_dict() for s in self.samples],
            "offset_ns": self.offset_ns,
            "median_offset_ns": self.median_offset_ns,
            "min_rtt_ns": self.min_rtt_ns,
            "mean_rtt_ns": self.mean_rtt_ns,
        }


def ssh_probe_command(
    host: str,
    *,
    ssh: str = "ssh",
    ssh_args: Sequence[str] = (),
    remote_python: str = "python3",
) -> List[str]:
    remote_command = f"{remote_python} -u -c " + shlex.quote(REMOTE_CODE)
    return [ssh, "-T", *ssh_args, host, remote_command]


def summarize(host: str, samples: Sequence[OffsetSample]) -> OffsetResult:
    if not samples:
        raise OffsetError("no samples collected")
    best = min(samples, key=lambda s: s.rtt_ns)
    offsets = [s.offset_ns for s in samples]
    rtts = [s.rtt_ns for s in samples]
    return OffsetResult(
        host=host,
        samples=list(samples),
        offset_ns=best.offset_ns,
        median_offset_ns=int(round(statistics.median(offsets))),
        min_rtt_ns=best.rtt_ns,
        mean_rtt_ns=int(round(statistics.mean(rtts))),
    )


def _exchange(
    proc: subprocess.Popen,
    *,
    time_ns=time.time_ns,
) -> OffsetSample:
    if proc.stdin is None or proc.stdout is None:
        raise OffsetError("remote process has no stdin/stdout")
    t0 = time_ns()
    try:
        proc.stdin.write("\n")
        proc.stdin.flush()
    except BrokenPipeError as e:
        raise OffsetError(_remote_exit_message(proc)) from e
    line = proc.stdout.readline()
    t1 = time_ns()
    if not line:
        raise OffsetError(_remote_exit_message(proc))
    try:
        remote_ns = int(line.strip())
    except ValueError as e:
        raise OffsetError(f"invalid remote timestamp: {line!r}") from e
    return OffsetSample.from_times(t0, t1, remote_ns)


def collect_samples(
    proc: subprocess.Popen,
    *,
    samples: int,
    interval_s: float,
    time_ns=time.time_ns,
    sleep=time.sleep,
    on_sample=None,
    warmup: bool = True,
) -> List[OffsetSample]:
    """Ping-pong ``samples`` times on an already-open remote Python.

    When ``warmup`` is true (the default), one untimed round trip runs first
    so SSH handshake, host-key prompts, and remote Python startup are not
    counted as a sample.
    """
    if warmup:
        _exchange(proc, time_ns=time_ns)
    collected: List[OffsetSample] = []
    for i in range(samples):
        sample = _exchange(proc, time_ns=time_ns)
        collected.append(sample)
        if on_sample is not None:
            on_sample(sample)
        if i + 1 < samples and interval_s > 0:
            sleep(interval_s)
    return collected


def measure_clock_offset(
    host: str,
    *,
    samples: int = 30,
    interval_s: float = 0.2,
    ssh: str = "ssh",
    ssh_args: Sequence[str] = (),
    remote_python: str = "python3",
    popen=subprocess.Popen,
    on_sample=None,
) -> OffsetResult:
    """SSH to ``host`` and collect clock-offset samples."""
    cmd = ssh_probe_command(
        host, ssh=ssh, ssh_args=ssh_args, remote_python=remote_python
    )
    try:
        proc = popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as e:
        raise OffsetError(f"{ssh!r} not found on PATH") from e
    try:
        collected = collect_samples(
            proc,
            samples=samples,
            interval_s=interval_s,
            on_sample=on_sample,
        )
        return summarize(host, collected)
    finally:
        _stop_proc(proc)


def format_sample(sample: OffsetSample) -> str:
    return (
        f"offset: {sample.offset_ns / 1e6:+9.3f} ms   "
        f"RTT: {sample.rtt_ns / 1e6:8.3f} ms"
    )


def format_summary(result: OffsetResult) -> str:
    return (
        f"\n"
        f"host:     {result.host}\n"
        f"offset:   {result.offset_ns / 1e6:+9.3f} ms  (min-RTT sample)\n"
        f"median:   {result.median_offset_ns / 1e6:+9.3f} ms\n"
        f"min RTT:  {result.min_rtt_ns / 1e6:9.3f} ms\n"
        f"mean RTT: {result.mean_rtt_ns / 1e6:9.3f} ms\n"
        f"\n"
        f"Positive offset means the remote clock is ahead of this host.\n"
    )


def run_offset(
    host: str,
    *,
    samples: int = 30,
    interval_s: float = 0.2,
    json_output: bool = False,
    ssh: str = "ssh",
    ssh_args: Sequence[str] = (),
    remote_python: str = "python3",
    file: Optional[TextIO] = None,
    popen=subprocess.Popen,
) -> OffsetResult:
    """Run the measurement and print human or JSON output."""
    if file is None:
        file = sys.stdout
    print(f"Connecting to {host}...", file=sys.stderr, flush=True)

    def on_sample(sample: OffsetSample) -> None:
        if not json_output:
            print(format_sample(sample), file=file, flush=True)

    result = measure_clock_offset(
        host,
        samples=samples,
        interval_s=interval_s,
        ssh=ssh,
        ssh_args=ssh_args,
        remote_python=remote_python,
        popen=popen,
        on_sample=on_sample,
    )
    if json_output:
        json.dump(result.to_dict(), file, separators=(",", ":"))
        file.write("\n")
        file.flush()
    else:
        print(format_summary(result), end="", file=file, flush=True)
    return result


def _remote_exit_message(proc: subprocess.Popen) -> str:
    code = proc.poll()
    error = ""
    if proc.stderr is not None and code is not None:
        try:
            error = proc.stderr.read()
        except OSError:
            error = ""
    if error.strip():
        detail = error.strip()
    elif code is not None:
        detail = f"exit {code}"
    else:
        detail = "stdout closed"
    return f"remote process exited: {detail}"


def _stop_proc(proc: subprocess.Popen) -> None:
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except OSError:
            pass
    if proc.poll() is not None:
        return
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
