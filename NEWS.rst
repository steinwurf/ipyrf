News for ipyrf
==============

This file lists the major changes between versions. For a more detailed list of
every change, see the Git log.

Latest
------
* Major: Changes has caused the header format to change, so older
  versions of ipyrf cannot operate with the new version of ipyrf.
* Minor: Added ``--bind-dev DEVICE`` for TCP/UDP clients to bind the
  transmit socket to a network interface.
* Minor: Added ``--inactivity-timeout`` for UDP servers to configure how
  long to wait without packets before exiting.
* Minor: Added ``ipyrf generate video`` to synthesize encoded-video-like
  ``trace`` traffic patterns from FPS, a GOP frame-type pattern (I/P/B),
  and configurable frame sizes, or from real media via ``--from`` /
  ``--ffprobe-json`` (ffprobe ``-show_frames``). Output is normal
  ``trace`` JSON for ``--traffic-pattern``.
* Minor: Added ``--traffic-pattern FILE`` for TCP/UDP clients. JSON types
  are ``trace`` (timestamped byte events, optional ``metadata`` and
  per-event ``tags``) and ``piecewise_rate`` (consecutive periods with
  ``duration`` and ``bitrate`` in bits/s, or strings like ``"2M"``;
  bitrate ``0`` is idle). Optional ``--loops N`` replays the pattern N
  times; ``--bandwidth`` is an optional egress-rate cap. Record headers
  and ``--packet-record`` CSV include ``event_id`` (1-based event/period
  index, or ``0`` when unset). TCP records include the payload length so
  variable-sized sends work.
* Minor: Added ``-R`` / ``--reverse`` for TCP/UDP clients. The client
  still connects; the first packet tells the server to send using the
  client's ``--bandwidth`` and ``--time`` (and UDP ``-l``), or the
  file name of ``--traffic-pattern`` (not the path) so the config
  stays in a single packet. The server loads that file from its
  working directory, or from ``--trace-dir DIR`` when given.
  ``--loops`` and ``--bandwidth`` (egress cap) are forwarded with
  the pattern. If the server cannot load the file, it replies with
  an error and the client fails with the same reason.
  Not supported with ``--interactive``.
* Patch: TCP pacing now accounts for the per-record header so
  ``--bandwidth`` matches the measured socket rate.
* Patch: Ctrl+C now ends a running test with ``stop_reason``
  ``interrupted`` instead of printing a traceback.
* Patch: Added a GitHub Action to run the unit tests.

1.5.1
-----
* Patch: Removed the latency column from --packet-record CSV output. Column
  headers now include units (``sequence``, ``size_bytes``,
  ``transmitted_ns``, ``received_ns``). Derive latency as
  ``received_ns - transmitted_ns``.

1.5.0
-----
* Minor: Added UDP packet recording via --packet-record. The server packs
  traces (sequence, send/receive timestamps, packet size) into fixed-size
  in-memory chunks during the test and writes the CSV file when the test
  ends. Chunks are preallocated so rotation does not stall the receive path.
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
