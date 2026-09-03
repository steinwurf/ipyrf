import pytest
import dummynet
import logging
import os
import csv
import io
import json

from ipyrf.recorder import CSV_COLUMNS, parse_record_text

log = logging.getLogger(__name__)


def test_ipyrf_tcp_basic_loopback(ipyrf, free_port):
    server = ipyrf.build()
    client = ipyrf.build()

    server.run_tcp_server("127.0.0.1", free_port)

    client.run_tcp_client("127.0.0.1", free_port, duration=2)

    ipyrf.check((server, client), timeout=5)


def test_ipyrf_tcp_reverse_loopback(ipyrf, free_port):
    server = ipyrf.build()
    client = ipyrf.build()

    server.run_tcp_server("127.0.0.1", free_port)
    client.run_tcp_client(
        "127.0.0.1", free_port, duration=2, bandwidth="10M", reverse=True
    )

    ipyrf.check((server, client), timeout=8, criteria={"reverse": True})


def test_ipyrf_udp_reverse_loopback(ipyrf, free_port):
    server = ipyrf.build()
    client = ipyrf.build()

    server.run_udp_server("127.0.0.1", free_port)
    client.run_udp_client(
        "127.0.0.1", free_port, duration=2, bandwidth="10M", reverse=True
    )

    ipyrf.check(
        (server, client),
        timeout=8,
        criteria={
            "reverse": True,
            "max_loss_pct": 5,
        },
    )


def _write_reverse_trace(directory, name="rev.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 1,
                "type": "trace",
                "events": [
                    {"timestamp": 0.0, "nbytes": 4000},
                    {"timestamp": 0.3, "nbytes": 4000},
                    {"timestamp": 0.6, "nbytes": 4000},
                ],
            },
            f,
        )
    return path


def test_ipyrf_tcp_reverse_traffic_pattern(ipyrf, testdirectory, free_port):
    trace_path = _write_reverse_trace(testdirectory.path())
    server = ipyrf.build()
    client = ipyrf.build()

    server.run_tcp_server(
        "127.0.0.1", free_port, trace_dir=testdirectory.path()
    )
    client.run_tcp_client(
        "127.0.0.1",
        free_port,
        reverse=True,
        traffic_pattern=trace_path,
    )

    ipyrf.check(
        (server, client),
        timeout=8,
        criteria={
            "reverse": True,
            "allow_server_stop_reasons": {"traffic-pattern"},
        },
    )


def test_ipyrf_udp_reverse_traffic_pattern(ipyrf, testdirectory, free_port):
    trace_path = _write_reverse_trace(testdirectory.path())
    server = ipyrf.build()
    client = ipyrf.build()

    server.run_udp_server(
        "127.0.0.1", free_port, trace_dir=testdirectory.path()
    )
    client.run_udp_client(
        "127.0.0.1",
        free_port,
        reverse=True,
        traffic_pattern=trace_path,
        bandwidth="50M",
    )

    ipyrf.check(
        (server, client),
        timeout=8,
        criteria={
            "reverse": True,
            "allow_server_stop_reasons": {"traffic-pattern"},
            "max_loss_pct": 5,
        },
    )


def test_ipyrf_udp_basic_loopback(ipyrf, free_port):
    server = ipyrf.build()
    client = ipyrf.build()

    server.run_udp_server("127.0.0.1", free_port)

    client.run_udp_client("127.0.0.1", free_port, duration=2, bandwidth="10M")

    ipyrf.check(
        (server, client),
        timeout=5,
        criteria={
            "max_loss_pct": 5,  # @todo Investigate why we need this. (Allow up to 5% loss)
        },
    )


def test_ipyrf_udp_record_cli(ipyrf, testdirectory, free_port):

    csv_path = os.path.join(testdirectory.path(), "records.csv")

    server = ipyrf.build()
    client = ipyrf.build()

    server.run_udp_server("127.0.0.1", free_port, record=csv_path)
    client.run_udp_client("127.0.0.1", free_port, duration=2, bandwidth="10M")

    ipyrf.check(
        (server, client),
        timeout=5,
        criteria={
            "max_loss_pct": 5,
        },
    )

    summary = server.wait_for_summary(timeout=5)
    assert "record_count" in summary
    assert summary["record_count"] == summary["packets"]
    assert summary["record_dropped"] == 0
    assert summary["record_count"] > 0

    meta, csv_text = parse_record_text(open(csv_path, encoding="utf-8").read())
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert list(rows[0].keys()) == list(CSV_COLUMNS)
    assert len(rows) == summary["record_count"]
    assert int(rows[0]["received_ns"]) >= int(rows[0]["transmitted_ns"])
    assert int(rows[0]["size_bytes"]) > 0
    assert meta["protocol"] == "udp"


def test_ipyrf_tcp_record_cli(ipyrf, testdirectory, free_port):

    csv_path = os.path.join(testdirectory.path(), "records.csv")

    server = ipyrf.build()
    client = ipyrf.build()

    server.run_tcp_server("127.0.0.1", free_port, record=csv_path)
    client.run_tcp_client("127.0.0.1", free_port, duration=2, bandwidth="10M")

    ipyrf.check((server, client), timeout=5)

    summary = server.wait_for_summary(timeout=5)
    assert "record_count" in summary
    assert summary["record_dropped"] == 0
    assert summary["record_count"] > 0

    meta, csv_text = parse_record_text(open(csv_path, encoding="utf-8").read())
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert list(rows[0].keys()) == list(CSV_COLUMNS)
    assert len(rows) == summary["record_count"]
    assert [int(r["sequence"]) for r in rows] == list(range(1, len(rows) + 1))
    assert int(rows[0]["received_ns"]) >= int(rows[0]["transmitted_ns"])
    assert int(rows[0]["size_bytes"]) > 0
    assert meta["protocol"] == "tcp"


def test_ipyrf_run_failed(ipyrf):
    node = ipyrf.build()
    with pytest.raises(RuntimeError):
        node.run_udp_server("bad", 9999)
        log.info(node.output())


def test_ipyrf_fail_check(ipyrf, free_port_func):
    log.info(f"Environment: {os.environ}")
    process_monitor = dummynet.ProcessMonitor(log=log)
    sudo = os.getuid() != 0
    log.info(f"sudo: {sudo}")
    shell = dummynet.HostShell(log=log, process_monitor=process_monitor, sudo=sudo)
    net = dummynet.DummyNet(shell=shell)

    try:
        d0 = net.netns_add("d0")
        d1 = net.netns_add("d1")
        net.link_veth_add(p1_name="d0-eth0", p2_name="d1-eth0")

        net.link_set(namespace="d0", interface="d0-eth0")
        net.link_set(namespace="d1", interface="d1-eth0")

        d0.addr_add(ip="10.0.0.1/24", interface="d0-eth0")
        d1.addr_add(ip="10.0.0.2/24", interface="d1-eth0")

        d0.up(interface="d0-eth0")
        d1.up(interface="d1-eth0")
        d0.up(interface="lo")
        d1.up(interface="lo")

        # Let's add some losses
        d0.tc(loss=5, delay=20, interface="d0-eth0", limit=650535)
        d1.tc(loss=5, delay=20, interface="d1-eth0", limit=650535)

        test_duration = 5
        port = free_port_func(d1)

        ipyrf_server = ipyrf.build(d1)
        ipyrf_server.run_udp_server("0.0.0.0", port)
        ipyrf_client = ipyrf.build(d0)
        ipyrf_client.run_udp_client(
            "10.0.0.2", port, duration=test_duration, bandwidth="50M"
        )
        log.info(ipyrf_client.wait_for_summary(timeout=test_duration + 5))
        log.info(ipyrf_server.wait_for_summary(timeout=test_duration + 5))

        with pytest.raises(AssertionError):
            ipyrf.check((ipyrf_server, ipyrf_client), timeout=test_duration + 5)

    finally:
        net.cleanup()


def test_ipyrf_tcp_bandwidth(ipyrf, free_port_func):
    # This test checks that we can limit the bandwidth using tc and
    # observe that ipyrf tcp client respects this limitation
    process_monitor = dummynet.ProcessMonitor(log=log)
    sudo = os.getuid() != 0
    log.info(f"sudo: {sudo}")
    shell = dummynet.HostShell(log=log, process_monitor=process_monitor, sudo=sudo)
    net = dummynet.DummyNet(shell=shell)

    try:
        d0 = net.netns_add("d0")
        d1 = net.netns_add("d1")
        net.link_veth_add(p1_name="d0-eth0", p2_name="d1-eth0")

        net.link_set(namespace="d0", interface="d0-eth0")
        net.link_set(namespace="d1", interface="d1-eth0")

        d0.addr_add(ip="10.0.0.1/24", interface="d0-eth0")
        d1.addr_add(ip="10.0.0.2/24", interface="d1-eth0")

        d0.up(interface="d0-eth0")
        d1.up(interface="d1-eth0")
        d0.up(interface="lo")
        d1.up(interface="lo")

        test_duration = 5
        delay = 50
        # netem limit is queued packets, not Mbit/s
        limit = 65535
        log.info("Testing without rate limiting")

        # We set a limit regardless to but set it very high, this is to
        # take in the overhead of tc itself
        rate = 1000  # Mbit/s
        d0.tc(rate=rate, delay=delay, limit=limit, interface="d0-eth0")
        d1.tc(rate=rate, delay=delay, limit=limit, interface="d1-eth0")

        port = free_port_func(d1)
        ipyrf_server = ipyrf.build(d1)
        ipyrf_server.run_tcp_server("0.0.0.0", port)
        ipyrf_client = ipyrf.build(d0)
        ipyrf_client.run_tcp_client("10.0.0.2", port, duration=test_duration)
        summary = ipyrf_client.wait_for_summary(timeout=test_duration + 5)

        mbits_per_second_without_limit = summary["bits_per_second"] / 1e6
        log.info(
            f"Bits per second without limit: {mbits_per_second_without_limit} Mbps"
        )

        rate = mbits_per_second_without_limit / 2  # Half the speed
        log.info(f"Testing with rate limiting to {rate} Mbit/s")

        # Let's rate limit the interfaces
        d0.tc(rate=rate, delay=delay, limit=limit, interface="d0-eth0")
        d1.tc(rate=rate, delay=delay, limit=limit, interface="d1-eth0")

        port = free_port_func(d1)
        ipyrf_server = ipyrf.build(d1)
        ipyrf_server.run_tcp_server("0.0.0.0", port)
        ipyrf_client = ipyrf.build(d0)
        ipyrf_client.run_tcp_client("10.0.0.2", port, duration=test_duration)
        summary = ipyrf_client.wait_for_summary(timeout=test_duration + 5)

        mbits_per_second_with_limit = summary["bits_per_second"] / 1e6
        log.info(f"Bits per second with limit: {mbits_per_second_with_limit} Mbps")

        assert mbits_per_second_with_limit < mbits_per_second_without_limit
        # We allow a 20% margin
        assert mbits_per_second_with_limit < rate * 1.2
        assert mbits_per_second_with_limit > rate * 0.8

    finally:
        net.cleanup()
