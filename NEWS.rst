News for ipyrf
==============

This file lists the major changes between versions. For a more detailed list of
every change, see the Git log.

Latest
------
* Patch: Added a GitHub Action to run the unit tests.
* Minor: Added ``--loops N`` for TCP/UDP clients to replay a
  ``--traffic-pattern`` N times. Each repetition starts when the previous
  one ends; event ids refer to the original pattern and repeat each loop.
* Minor: Added ``--bind-dev DEVICE`` for TCP/UDP clients to bind the
  transmit socket to a network interface via Linux ``SO_BINDTODEVICE``
  (typically requires ``CAP_NET_RAW`` or root).
* Minor: UDP and TCP record headers now carry a traffic-pattern
  ``event_id`` (1-based event/period index, or ``0`` when unset). Sends
  stay within a single event or period so packets can be attributed via
  ``--packet-record`` CSV (new ``event_id`` column).
* Minor: Added ``ipyrf generate video`` to synthesize encoded-video-like
  ``trace`` traffic patterns from FPS, a GOP frame-type pattern (I/P/B),
  and configurable frame sizes, or from real media via ``--from`` /
  ``--ffprobe-json`` (ffprobe ``-show_frames``). Output is normal
  ``trace`` JSON for ``--traffic-pattern`` (no video-specific TCP/UDP
  behavior).
* Minor: Added ``--inactivity-timeout`` for UDP servers to configure how
  long to wait without packets before exiting (default 2.0 seconds).
* Minor: Added ``piecewise_rate`` traffic patterns: consecutive periods
  with ``duration`` and ``bitrate`` (bits/s, or strings like ``"2M"``).
  Bitrate ``0`` is idle. Loaded via the same ``--traffic-pattern`` JSON
  interface as ``trace`` files.
* Patch: Traffic-pattern sends now coalesce to the requested chunk size
  instead of emitting tiny records as soon as any byte becomes
  available. That was inflating TCP wire rates via per-record headers
  (especially for low continuous bitrates).
* Patch: TCP pacing now accounts for the per-record header so
  ``--bandwidth`` and traffic-pattern bitrates match the measured
  socket rate (UDP was already correct because the header is inside
  the datagram).
* Patch: Traffic-pattern scheduling now uses monotonic deadlines and
  sleeps for most of each wait, with a short final sleep near the
  deadline instead of busy-waiting.
* Minor: ``--bandwidth`` can be combined with ``--traffic-pattern`` as an
  optional egress-rate cap. The pattern still controls when bytes become
  available; the pacer limits how fast those bytes may be sent.
* Minor: Added ``--traffic-pattern FILE`` for TCP/UDP clients. The JSON
  format is ``{"version": 1, "type": "trace", "events": [...]}`` with
  optional ``metadata`` and per-event ``tags``. A traffic-pattern
  controller releases event bytes at their relative timestamps and
  fragments large events into normal send chunks. Existing
  ``--bandwidth`` / ``--time`` behavior is unchanged when no pattern is
  supplied.
* Minor: Added a traffic-pattern abstraction (``TrafficPattern``) with a
  ``TraceTrafficPattern`` that makes bytes available at relative
  timestamps from a list of ``(timestamp, nbytes)`` events.
* Minor: Extended BasePacingController with next_send() so custom
  controllers can decide both when to send and how many bytes to send.
  Static and interactive pacing keep their previous behavior.
* Minor: Updated TCP framing to include the payload length in each
  record so variable-sized sends work. Latency measurements still use
  the per-record timestamp.

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
