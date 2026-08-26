ipyrf
=====

Minimal iperf3-like network throughput tool with JSON output. Supports TCP and UDP, server and client modes.

Features
--------
- TCP and UDP tests
- JSON or human-readable output
- Optional bandwidth capping (TCP/UDP)
- UDP packet loss estimation
- Optional per-datagram UDP packet recording
- Linux TCP congestion control selection (if available)

Installation
------------

From PyPI (recommended):

.. code-block:: bash

   python3 -m pip install ipyrf

From source (editable):

.. code-block:: bash

   python3 -m venv .venv
   source .venv/bin/activate
   python3 -m pip install -U pip build
   python3 -m pip install -e .

Test Utilities (ipyrf.test)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``ipyrf.test`` module provides test utilities for writing your own tests:

.. code-block:: python

   from ipyrf.test import IPyrfBuilder, CheckCriteria

   def test_my_network(testdirectory):
       builder = IPyrfBuilder(testdirectory)
       port = 12345

       server = builder.build()
       client = builder.build()

       server.run_tcp_server("127.0.0.1", port)
       client.run_tcp_client("127.0.0.1", port, duration=2)

       # Check with custom criteria
       builder.check(
           (server, client),
           timeout=5,
           criteria={"min_bps": 1000000}
       )

Available classes and functions:

- ``IPyrfClient``: Run and monitor ipyrf instances
- ``IPyrfBuilder``: Builder pattern for creating test instances
- ``CheckCriteria``: Configurable criteria for evaluating test results

See ``examples/using_test_utilities.py`` for more examples.

Running Tests
~~~~~~~~~~~~~

The project includes a comprehensive test suite using pytest:

.. code-block:: bash

   # Run all tests
   python3 waf --run_tests

Usage
-----

The package installs a console script named ``ipyrf``.

Quick examples
~~~~~~~~~~~~~~

TCP server:

.. code-block:: bash

   ipyrf tcp server 0.0.0.0 --port 12345

TCP client:

.. code-block:: bash

   ipyrf tcp client 127.0.0.1 --port 12345 --time 5
   ipyrf tcp client 127.0.0.1 --port 12345 --time 5 --set-mss 1400
   ipyrf tcp client 127.0.0.1 --port 12345 --traffic-pattern trace.json

UDP server:

.. code-block:: bash

   ipyrf udp server 0.0.0.0 --port 12345
   ipyrf udp server 0.0.0.0 --port 12345 --packet-record packets.csv

UDP client (with bandwidth cap and optional payload size):

.. code-block:: bash

   ipyrf udp client 127.0.0.1 --port 12345 --bandwidth 50M --time 5
   ipyrf udp client 127.0.0.1 --port 12345 --bandwidth 50M --time 5 -l 1200
   ipyrf udp client 127.0.0.1 --port 12345 --traffic-pattern trace.json

Traffic patterns
----------------

Pass ``--traffic-pattern FILE`` to a TCP or UDP client to drive sends from a
JSON pattern instead of ``--time``. Two pattern types are supported.

**Trace** (``"type": "trace"``): discrete byte arrivals at timestamps:

.. code-block:: json

   {
     "version": 1,
     "type": "trace",
     "metadata": {"name": "example"},
     "events": [
       {"timestamp": 0.0, "nbytes": 1200},
       {"timestamp": 0.1, "nbytes": 4800, "tags": ["burst"]},
       {"timestamp": 1.0, "nbytes": 1200}
     ]
   }

Each event makes ``nbytes`` available at relative time ``timestamp`` (seconds
from test start). Budgets are in socket bytes as counted by ipyrf (for TCP that
includes the per-record header). Large events are fragmented into normal
TCP/UDP send chunks without changing when those bytes become available.

**Piecewise rate** (``"type": "piecewise_rate"``): consecutive periods with a
duration and bitrate (bits/s; numeric or strings like ``"2M"``). A bitrate of
``0`` is idle:

.. code-block:: json

   {
     "version": 1,
     "type": "piecewise_rate",
     "metadata": {"name": "example"},
     "periods": [
       {"duration": 2.0, "bitrate": "2M"},
       {"duration": 1.0, "bitrate": 0},
       {"duration": 3.0, "bitrate": "10M", "tags": ["burst"]}
     ]
   }

Bytes become available continuously at ``bitrate / 8`` bytes per second within
each period (socket bytes, matching measured rates). ``metadata`` and
per-event/period ``tags`` are optional. Optionally
pass ``--bandwidth`` together with ``--traffic-pattern`` to cap egress rate: the
pattern decides when bytes become available, and the pacer limits how quickly
they may be transmitted. ``--traffic-pattern`` cannot be combined with
``--interactive``.

Examples:

.. code-block:: bash

   ipyrf tcp client 127.0.0.1 --traffic-pattern trace.json
   ipyrf udp client 127.0.0.1 --traffic-pattern rates.json --bandwidth 50M -l 1200

Synthetic video traces
~~~~~~~~~~~~~~~~~~~~~~

Generate a normal ``trace`` file that resembles encoded video. This does not
add video-specific TCP/UDP behavior — use the resulting file with
``--traffic-pattern`` like any other trace.

**Synthetic** (FPS + GOP + fixed I/P/B sizes):

.. code-block:: bash

   ipyrf generate video -o video.json --fps 30 --gop IPBB \
     --i-size 40000 --p-size 8000 --b-size 2000 --duration 10

**From real media** (requires ``ffprobe`` on ``PATH``), or from a saved
ffprobe frames dump:

.. code-block:: bash

   ipyrf generate video -o video.json --from clip.mp4
   ipyrf generate video -o video.json --from clip.mp4 --duration 5
   ffprobe -v quiet -select_streams v:0 -show_frames -print_format json \
     clip.mp4 > frames.json
   ipyrf generate video -o video.json --ffprobe-json frames.json

   ipyrf udp client 127.0.0.1 --traffic-pattern video.json -l 1200

Each event is tagged with the frame type (``I``, ``P``, or ``B`` when known).
Generation parameters / source path are stored under ``metadata``.

Interactive mode
----------------

You can run clients in an interactive mode that lets you adjust the pacing live using your keyboard. Use ``--interactive`` and optionally ``--interval`` (seconds between stats updates). When interactive is enabled, the same client logic is used underneath with a dynamic pacing controller.

Controls shown in the terminal:

- ``←``: -1 Mbps
- ``→``: +1 Mbps
- ``↓``: -10%
- ``↑``: +10%
- ``0``: reset to initial bandwidth (or unlimited for TCP if none was provided)
- ``u``: unlimited (disable pacing)
- ``q``: quit

Examples:

.. code-block:: bash

   # TCP interactive (unlimited unless you pass --bandwidth)
   ipyrf tcp client 127.0.0.1 --port 5201 --interactive

   # TCP interactive with initial pacing and custom interval
   ipyrf tcp client 127.0.0.1 --port 5201 --bandwidth 200M --set-mss 1400 --interactive --interval 0.5

   # UDP interactive (requires initial --bandwidth)
   ipyrf udp client 127.0.0.1 --port 5201 --bandwidth 50M -l 1200 --interactive

CLI overview
------------

Top-level structure:

.. code-block:: text

   ipyrf [tcp|udp] [server|client] [OPTIONS]
   ipyrf generate video -o FILE [OPTIONS]

Common options (both protocols, both roles):

- ``--port``: Port (default 5201)
- ``--logfile``: Redirect output to a file
- ``--json_log``: Emit logs in JSON (newline-delimited)

TCP-specific options:

- ``tcp server ADDRESS``: Start a TCP server on ``ADDRESS``
- ``tcp client ADDRESS``: Start a TCP client to connect to ``ADDRESS``
- ``--congestion-control``: Select Linux TCP CC algorithm if available
- ``--time``: Test duration (seconds), default 10
- ``--bandwidth``: Target rate (e.g., ``50M``); pacing cap, also usable with
  ``--traffic-pattern``
- ``--traffic-pattern FILE``: Drive sends from a JSON traffic-pattern file
  (``trace`` or ``piecewise_rate``)
- ``--set-mss``: Set approximate MSS via ``TCP_MAXSEG``
- ``--interactive``: Enable interactive pacing controls
- ``--interval``: Stats interval in seconds for interactive mode (default 1.0)

UDP-specific options:

- ``udp server ADDRESS``: Start a UDP server on ``ADDRESS``
- ``udp client ADDRESS``: Start a UDP client to ``ADDRESS``
- ``--time``: Test duration (seconds), default 10
- ``--bandwidth``: Target rate (e.g., ``50M``); pacing cap, also usable with
  ``--traffic-pattern``
- ``--traffic-pattern FILE``: Drive sends from a JSON traffic-pattern file
  (``trace`` or ``piecewise_rate``)
- ``-l/--length``: UDP payload length (default 1200)
- ``--packet-record PATH``: UDP server: write received packet traces to a CSV file after the test
- ``--inactivity-timeout SECONDS``: UDP server: exit after this many seconds
  without receiving packets (default 2.0)
- ``--interactive``: Enable interactive pacing controls
- ``--interval``: Stats interval in seconds for interactive mode (default 1.0)

Generate options:

- ``generate video -o FILE``: Write a video-like ``trace`` JSON file
- ``--from MEDIA``: Build the trace from video frames via ``ffprobe``
- ``--ffprobe-json FILE``: Same, from a saved ``ffprobe -show_frames`` dump
- ``--fps`` / ``--gop`` / ``--i-size`` / ``--p-size`` / ``--b-size``:
  Synthetic mode parameters (ignored when using ``--from`` /
  ``--ffprobe-json``)
- ``--duration``: Synthetic length in seconds (default 10), or optional
  truncate when importing from ffprobe

UDP packet recording
--------------------

Pass ``--packet-record PATH`` to a UDP server to record one trace entry per
received datagram (sequence number, sender timestamp, receiver timestamp,
packet size). Records are packed into fixed-size in-memory chunks during the
test so the receive path stays free of CSV I/O. Chunks are allocated before
the test starts (about 20 s of 1 Gbit/s traffic by default) so rotation does
not stall the receive loop. When the test ends, the CSV file (columns
``sequence``, ``size_bytes``, ``transmitted_ns``, ``received_ns``) is written to
``PATH``. Per-packet latency in nanoseconds is ``received_ns - transmitted_ns``.

Quick local test (two terminals):

.. code-block:: bash

   # Terminal 1 — server with recording
   ipyrf udp server 0.0.0.0 --port 12345 --packet-record packets.csv

   # Terminal 2 — short paced client
   ipyrf udp client 127.0.0.1 --port 12345 --bandwidth 50M --time 2 -l 1200

When the client finishes, the server exits and ``packets.csv`` contains the
trace. Use ``--enable-latency`` on the client if you also want latency
stats in the server summary.

JSON logging
------------

Add ``--json_log`` to switch all output to newline-delimited JSON objects. This is useful for machine parsing or dashboards. Example:

.. code-block:: bash

   ipyrf tcp client 127.0.0.1 --time 5 --json_log | jq

Notes
-----

- Output is JSON (newline-delimited for update events) when ``--json_log`` is given; otherwise, a human-readable summary is printed.
- UDP mode sends a FIN marker at the end and the server exits after FIN (or inactivity timeout).
- On Linux, congestion control selection is exposed if ``/proc`` entries are available.

License
-------

MIT. See ``LICENSE``.
