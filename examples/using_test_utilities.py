"""
Example of using ipyrf.test utilities in your own tests.

This example demonstrates how to use the ipyrf.test module to create
custom tests for network performance.
"""

from ipyrf.test import IPyrfBuilder, CheckCriteria, pick_free_port


# Example 1: Using the utilities directly in a test
def test_custom_tcp_test(testdirectory):
    """Example of a custom TCP test using ipyrf.test utilities."""
    builder = IPyrfBuilder(testdirectory)
    port = pick_free_port()

    # Create server and client
    server = builder.build()
    client = builder.build()

    # Run the test
    server.run_tcp_server("127.0.0.1", port)
    client.run_tcp_client("127.0.0.1", port, duration=1)

    # Check results with custom criteria
    builder.check(
        (server, client),
        timeout=5,
        criteria={
            "min_seconds": 0.8,
            "min_bps": 1000000,  # At least 1 Mbps
        },
    )


# Example 2: Using soft_fail to get detailed info
def test_with_detailed_info(testdirectory):
    """Example showing how to get detailed test results."""
    builder = IPyrfBuilder(testdirectory)
    port = pick_free_port()

    server = builder.build()
    client = builder.build()

    server.run_udp_server("127.0.0.1", port)
    client.run_udp_client("127.0.0.1", port, duration=1, bandwidth="10M")

    # Use soft_fail to get results without raising exceptions
    ok, info = builder.check(
        (server, client),
        timeout=5,
        criteria={
            "max_loss_pct": 1.0,  # Allow up to 1% loss
        },
        soft_fail=True,
    )

    print(f"Test passed: {ok}")
    print(f"Server summary: {info['server_summary']}")
    print(f"Client summary: {info['client_summary']}")
    print(f"Details: {info['details']}")

    assert ok, f"Test failed: {info['details']}"


# Example 3: Custom criteria validation
def test_with_custom_criteria(testdirectory):
    """Example of using CheckCriteria directly for custom validation."""
    builder = IPyrfBuilder(testdirectory)
    port = pick_free_port()

    server = builder.build()
    client = builder.build()

    server.run_udp_server("127.0.0.1", port)
    client.run_udp_client("127.0.0.1", port, duration=1, bandwidth="50M")

    # Wait for summaries
    server_summary = server.wait_for_summary(timeout=5)
    client_summary = client.wait_for_summary(timeout=5)

    # Create custom criteria
    criteria = CheckCriteria(
        mode="udp",
        min_seconds=0.9,
        max_loss_pct=5.0,
        min_packets=100,
        server_bps_ratio_of_target=0.8,  # At least 80% of target
    )

    # Evaluate
    ok, reasons = criteria.evaluate(server_summary, client_summary)
    assert ok, f"Test failed: {'; '.join(reasons)}"


if __name__ == "__main__":
    print("Run these examples with pytest:")
    print("  pytest examples/using_test_utilities.py -v")
