News for ipyrf
==============

This file lists the major changes between versions. For a more detailed list of
every change, see the Git log.

Latest
------
* Minor: Added UDP packet recording via --packet-record. The server writes
  a compact binary trace (sequence number and send/receive timestamps) without
  blocking the receive path, reports recorded/dropped counts in the summary,
  and can export the trace to CSV.
* Patch: Changed latency calculation to use milliseconds instead of seconds.

1.4.0
-----
* Minor: Updated the test component to include the latency tracking feature.

1.3.0
-----
* Minor: Added latency tracking feature. Clients can enable latency measurement
  with --enable-latency flag. Servers automatically detect client preference
  and calculate latency statistics (min, max, average) when enabled. Works for
  both UDP and TCP protocols.

1.2.0
-----
* Minor: Replaced TokenBucket with a Pacer/LeakyBucket implementation.
* Minor: Improved log output
* Patch: Fixed UDP loss calculation.

1.1.1
-----
* Patch: Removed debug print.

1.1.0
-----
* Minor: Added test utilities to the library.

1.0.1
-----
* Patch: Fixed version number.

1.0.0
-----
* Major: Initial release.
