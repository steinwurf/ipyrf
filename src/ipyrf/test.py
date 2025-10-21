"""
Test utilities for ipyrf.

This module provides test helpers for ipyrf including:
- IPyrfClient: A client for running and monitoring ipyrf instances
- CheckCriteria: Criteria for evaluating test results
- IPyrfBuilder: Builder pattern for creating test instances

Note: This module requires pytest and dummynet to be installed.
"""

import os
import sys
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any, Set, List

try:
    import dummynet
except ImportError:
    raise ImportError(
        "dummynet is required to use ipyrf.test. "
        "Install it with: pip install dummynet"
    )

log = logging.getLogger(__name__)


def wait_for_condition(condition_func, timeout=2, interval=0.1):
    """
    Wait for a condition to become true.

    Args:
        condition_func: A callable that returns True when the condition
                        is met.
        timeout: Maximum time to wait in seconds (default: 2).
        interval: Time between checks in seconds (default: 0.1).

    Returns:
        bool: True if the condition was met, False if timeout occurred.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        if condition_func():
            return True
        time.sleep(interval)
    return False


class IPyrfClient:
    """
    A client for running and monitoring ipyrf instances.

    This class manages an ipyrf process, allowing you to run UDP/TCP
    servers and clients, and monitor their output.
    """

    log_file_name = "ipyrf{id}.log"
    ID = 0

    def __init__(self, testdirectory, shell=None):
        """
        Initialize an IPyrf client.

        Args:
            testdirectory: A test directory object (from
                           pytest-testdirectory).
            shell: Optional dummynet shell to run the process in.
                   Defaults to a HostShell if not provided.
        """
        self.shell = shell or dummynet.HostShell(
            log=log, process_monitor=dummynet.ProcessMonitor(log=log), sudo=False
        )
        self.process = None
        self.id = IPyrfClient.ID
        IPyrfClient.ID += 1
        log_file = self.log_file_name.format(id=self.id)
        self.log_file_path = os.path.join(testdirectory.path(), log_file)
        # make sure the log file does not exist
        assert not os.path.exists(
            self.log_file_path
        ), f"log file {self.log_file_path} already exists"

    def run_udp_server(self, address, port):
        """
        Run ipyrf as a UDP server.

        Args:
            address: IP address to bind to.
            port: Port number to listen on.
        """
        args = ["udp", "server", address, "--port", str(port)]
        self.__run(args)

    def run_tcp_server(self, address, port):
        """
        Run ipyrf as a TCP server.

        Args:
            address: IP address to bind to.
            port: Port number to listen on.
        """
        args = ["tcp", "server", address, "--port", str(port)]
        self.__run(args)

    def run_udp_client(self, address, port, duration, bandwidth, length=None):
        """
        Run ipyrf as a UDP client.

        Args:
            address: Server IP address.
            port: Server port number.
            duration: Test duration in seconds.
            bandwidth: Target bandwidth (e.g., "10M" for 10 Mbps).
            length: Optional packet length.
        """
        args = [
            "udp",
            "client",
            address,
            "--port",
            str(port),
            "--time",
            str(duration),
            "--bandwidth",
            str(bandwidth),
        ]
        if length is not None:
            args += ["-l", str(length)]
        self.__run(args)

    def run_tcp_client(self, address, port, duration, bandwidth=None, TCP_MAXSEG=None):
        """
        Run ipyrf as a TCP client.

        Args:
            address: Server IP address.
            port: Server port number.
            duration: Test duration in seconds.
            bandwidth: Optional target bandwidth (e.g., "10M" for
                       10 Mbps).
            TCP_MAXSEG: Optional TCP maximum segment size.
        """
        args = [
            "tcp",
            "client",
            address,
            "--port",
            str(port),
            "--time",
            str(duration),
        ]
        if TCP_MAXSEG is not None:
            args += ["--set-mss", str(TCP_MAXSEG)]
        if bandwidth is not None:
            args += ["--bandwidth", str(bandwidth)]
        self.__run(args)

    def __run(self, args):
        """Internal method to run ipyrf with the given arguments."""
        assert self.process is None, "ipyrf is already running"

        # add the log file argument
        args += ["--logfile", self.log_file_path]
        args += ["--json_log"]  # log in json format

        cmd = f"{sys.executable} -m ipyrf {' '.join(args)}"
        log.info(f"Running: {cmd}")
        self.process = self.shell.run_async(cmd)

        def check_for_start_log_msg():
            outputs = self.output()
            return len(outputs) != 0 and outputs[0].get("type") == "start"

        if not wait_for_condition(check_for_start_log_msg, timeout=5):
            raise RuntimeError(
                f"ipyrf did not start properly within 5 second, "
                f"output: {self.output()}"
            )

    def output(self):
        """
        Get the JSON log output from the ipyrf process.

        Returns:
            list: A list of dictionaries, each representing a JSON log
                  entry.
        """
        if self.process is None or not os.path.exists(self.log_file_path):
            return []

        # read new lines from the log file
        result = []
        with open(self.log_file_path, "r") as f:
            lines = f.readlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    result.append(data)
                except Exception as e:
                    log.warning(f"Failed to parse ipyrf output line: {line}: {e}")
        return result

    def wait_for_summary(self, timeout):
        """
        Wait for the summary output from ipyrf.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            dict: The summary dictionary.

        Raises:
            TimeoutError: If the summary is not received within the
                          timeout.
        """
        if self.process is None:
            raise Exception("ipyrf is not running")

        wait_for_condition(
            lambda: len(self.output()) > 0
            and self.output()[-1].get("type") == "summary",
            timeout=timeout,
        )
        outputs = self.output()
        if len(outputs) == 0:
            raise TimeoutError("no output from ipyrf")
        summary = outputs[-1]
        if summary.get("type") != "summary":
            raise TimeoutError("no summary output from ipyrf")
        return summary


@dataclass
class CheckCriteria:
    """
    Tunable rules for deciding whether a run was successful.

    If mode is None, the checker infers it from summaries ("tcp"/"udp").
    """

    mode: Optional[str] = None
    min_seconds: float = 0.5  # require test to actually run
    min_bps: Optional[float] = None  # absolute minimum bits/s
    min_bytes: Optional[int] = None  # absolute minimum bytes received

    # UDP-specific
    max_loss_pct: Optional[float] = 0.0  # allow some loss (% of sent)
    max_lost_packets: Optional[int] = None
    min_packets: Optional[int] = None  # absolute minimum packets
    server_bps_ratio_of_target: Optional[float] = None
    # Allowed stop reasons
    allow_client_stop_reasons: Set[str] = field(default_factory=lambda: {"duration"})
    allow_server_stop_reasons: Set[str] = field(
        default_factory=lambda: {"end-of-test", "inactivity"}
    )

    def evaluate(
        self, server: Dict[str, Any], client: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """
        Evaluate server and client summaries against the criteria.

        Args:
            server: Server summary dictionary.
            client: Client summary dictionary.

        Returns:
            tuple: (success: bool, reasons: list of str)
                   success is True if all criteria are met, False
                   otherwise.
                   reasons contains a list of failure reasons (empty if
                   success).
        """
        reasons: List[str] = []
        mode = self.mode or client.get("mode") or server.get("mode")

        # Basic sanity
        if not mode:
            reasons.append("Could not determine mode (tcp/udp) from summaries.")
            return False, reasons

        if client.get("direction") != "tx":
            reasons.append(
                f"Client direction is not 'tx': " f"{client.get('direction')}"
            )
            return False, reasons

        if server.get("direction") != "rx":
            reasons.append(
                f"Server direction is not 'rx': " f"{server.get('direction')}"
            )
            return False, reasons

        if server.get("type") != "summary" or client.get("type") != "summary":
            reasons.append("Missing summary object(s).")
            return False, reasons

        # Duration
        s_sec = float(server.get("seconds", 0.0) or 0.0)
        c_sec = float(client.get("seconds", 0.0) or 0.0)
        if s_sec < self.min_seconds or c_sec < self.min_seconds:
            reasons.append(
                f"Duration too short: server={s_sec:.3f}s "
                f"client={c_sec:.3f}s (min={self.min_seconds}s)"
            )

        # Stop reasons
        if (
            "stop_reason" in client
            and client["stop_reason"] not in self.allow_client_stop_reasons
        ):
            reasons.append(
                f"Client stop_reason '{client['stop_reason']}' " f"not allowed."
            )
        if (
            "stop_reason" in server
            and server["stop_reason"] not in self.allow_server_stop_reasons
        ):
            reasons.append(
                f"Server stop_reason '{server['stop_reason']}' " f"not allowed."
            )

        # Throughput
        s_bps = float(server.get("bits_per_second", 0.0) or 0.0)
        c_bps = float(client.get("bits_per_second", 0.0) or 0.0)

        # Absolute minimum bps (applies to both modes)
        if self.min_bps is not None:
            if s_bps < self.min_bps and c_bps < self.min_bps:
                reasons.append(
                    f"Throughput below min_bps: server={s_bps:.0f} "
                    f"client={c_bps:.0f} < {self.min_bps:.0f} bps"
                )

        # Absolute minimum bytes (applies to both modes)
        if self.min_bytes is not None:
            if (server.get("bytes", 0) or 0) < self.min_bytes and (
                client.get("bytes", 0) or 0
            ) < self.min_bytes:
                reasons.append(
                    f"Bytes received below min_bytes: "
                    f"server={server.get('bytes', 0)} "
                    f"client={client.get('bytes', 0)} < {self.min_bytes}"
                )

        if mode == "udp":
            # Packet loss (server knows what it received)
            loss_pct = float(server.get("lost_percent", 0.0) or 0.0)
            lost_pkts = int(server.get("lost_packets", 0) or 0)

            if self.max_loss_pct is not None and loss_pct > self.max_loss_pct:
                reasons.append(
                    f"Loss percentage too damn high: {loss_pct:.2f}% > "
                    f"{self.max_loss_pct:.2f}%"
                )

            if self.max_lost_packets is not None and lost_pkts > self.max_lost_packets:
                reasons.append(
                    f"Lost packet count too high: {lost_pkts} > "
                    f"{self.max_lost_packets}"
                )

            if (
                self.min_packets is not None
                and server.get("packets", 0) < self.min_packets
            ):
                reasons.append(
                    f"Received too few packets: "
                    f"{server.get('packets', 0)} < {self.min_packets}"
                )

            # Compare server measured bps to client target (if present)
            target = client.get("target_bandwidth_bps")
            if target is not None and self.server_bps_ratio_of_target is not None:
                expected = float(target) * float(self.server_bps_ratio_of_target)
                if s_bps < expected:
                    reasons.append(
                        f"Server bps below "
                        f"{self.server_bps_ratio_of_target:.2f}× target:"
                        f" server={s_bps:.0f} target={float(target):.0f}"
                    )

        return (len(reasons) == 0), reasons


class IPyrfBuilder:
    """
    Builder for creating IPyrfClient instances and checking test
    results.

    This class provides a convenient way to create and manage ipyrf
    test instances.
    """

    def __init__(self, testdirectory):
        """
        Initialize the builder.

        Args:
            testdirectory: A test directory object (from
                           pytest-testdirectory).
        """
        self.testdirectory = testdirectory

    def build(self, shell=None):
        """
        Build a new IPyrfClient instance.

        Args:
            shell: Optional dummynet shell to run the process in.

        Returns:
            IPyrfClient: A new client instance.
        """
        return IPyrfClient(self.testdirectory, shell)

    @staticmethod
    def check(
        server_client_pair: Tuple[IPyrfClient, IPyrfClient],
        *,
        timeout: float = 30.0,
        criteria: Optional[dict] = None,
        soft_fail: bool = False,
    ):
        """
        Waits for both server and client summaries and evaluates them
        against `criteria`.

        Args:
            server_client_pair: Tuple of (server, client) IPyrfClient
                                instances.
            timeout: Maximum time to wait for summaries (default: 30.0
                     seconds).
            criteria: Optional dictionary of CheckCriteria parameters.
            soft_fail: If True, don't raise an exception on failure
                       (default: False).

        Returns:
            tuple: (ok: bool, info: dict)
                   If soft_fail=True, no exception is raised on failure.

        Raises:
            AssertionError: If the check fails and soft_fail is False.
        """
        import pprint

        if criteria is None:
            criteria = CheckCriteria()
        else:
            criteria = CheckCriteria(**criteria)

        server, client = server_client_pair
        info = {}
        try:
            info["server_summary"] = server.wait_for_summary(timeout=timeout)
            info["client_summary"] = client.wait_for_summary(timeout=timeout)
            ok, reasons = criteria.evaluate(
                info["server_summary"], info["client_summary"]
            )
            info["details"] = "; ".join(reasons) if reasons else "ok"
        except TimeoutError as e:
            ok = False
            info["details"] = f"Timeout waiting for summaries: {e}"
            info["server_output"] = server.output()
            info["client_output"] = client.output()
            if "server_summary" not in info:
                info["server_summary"] = None
            if "client_summary" not in info:
                info["client_summary"] = None
        info["ok"] = ok
        if not soft_fail and not ok:
            log.info(pprint.pformat(info))
            raise AssertionError(info["details"])
        return ok, info


__all__ = [
    "IPyrfClient",
    "CheckCriteria",
    "IPyrfBuilder",
    "wait_for_condition",
]
