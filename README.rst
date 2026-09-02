ipyrf
=====

Minimal iperf3-like network throughput tool with JSON output. Supports TCP and UDP, server and client modes.

Features
--------
- TCP and UDP tests
- JSON or human-readable output
- Optional bandwidth capping (TCP/UDP)
- UDP packet loss estimation
- Optional receive-side recording of UDP datagrams and TCP framed records
- Interactive HTML reports from ``--record`` CSVs (``ipyrf-report``)
- Linux TCP congestion control selection (if available)
- Client ``--bind-dev`` to transmit via a specific network interface (Linux)
- Clock-offset measurement to a remote host over SSH (``ipyrf offset``)

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

The package installs console scripts named ``ipyrf`` and ``ipyrf-report``.

Quick examples
~~~~~~~~~~~~~~

TCP server:

.. code-block:: bash

   ipyrf tcp server 0.0.0.0 --port 12345
   ipyrf tcp server 0.0.0.0 --port 12345 --record records.csv
   ipyrf tcp server 0.0.0.0 --port 12345 --trace-dir /var/traces

TCP client:

.. code-block:: bash

   ipyrf tcp client 127.0.0.1 --port 12345 --time 5
   ipyrf tcp client 127.0.0.1 --port 12345 --time 5 --set-mss 1400
   ipyrf tcp client 127.0.0.1 --port 12345 --traffic-pattern trace.json
   ipyrf tcp client 127.0.0.1 --port 12345 --traffic-pattern trace.json --loops 3
   ipyrf tcp client 127.0.0.1 --port 12345 --time 5 --bind-dev eth0
   ipyrf tcp client 127.0.0.1 --port 12345 --time 5 --bandwidth 50M --reverse
   ipyrf tcp client 127.0.0.1 --port 12345 --traffic-pattern trace.json --reverse

UDP server:

.. code-block:: bash

   ipyrf udp server 0.0.0.0 --port 12345
   ipyrf udp server 0.0.0.0 --port 12345 --record records.csv
   ipyrf udp server 0.0.0.0 --port 12345 --trace-dir /var/traces

UDP client (with bandwidth cap and optional payload size):

.. code-block:: bash

   ipyrf udp client 127.0.0.1 --port 12345 --bandwidth 50M --time 5
   ipyrf udp client 127.0.0.1 --port 12345 --bandwidth 50M --time 5 -l 1200
   ipyrf udp client 127.0.0.1 --port 12345 --traffic-pattern trace.json
   ipyrf udp client 127.0.0.1 --port 12345 --traffic-pattern trace.json --loops 3
   ipyrf udp client 127.0.0.1 --port 12345 --bandwidth 50M --time 5 --bind-dev eth0
   ipyrf udp client 127.0.0.1 --port 12345 --bandwidth 50M --time 5 --reverse
   ipyrf udp client 127.0.0.1 --port 12345 --traffic-pattern trace.json --reverse

Clock offset (this host vs a remote SSH machine):

.. code-block:: bash

   ipyrf offset user@rasp1
   ipyrf offset user@rasp1 --json

Reverse mode
------------

Pass ``-R`` / ``--reverse`` on a TCP or UDP client so the server sends and
the client receives. The client still initiates the connection; the first
packet tells the server to transmit using the client's ``--bandwidth`` and
``--time`` (and UDP ``-l``). This is the same role split as iperf3 ``-R``.

With ``--traffic-pattern FILE``, the client sends only the file name (not
the path) in that first packet so the reverse config always fits in a
single packet. The server loads the file from its working directory, or
from ``--trace-dir DIR`` when that option is given. ``--loops`` and
``--bandwidth`` (egress-rate cap) are forwarded with the pattern. If the
server cannot load the file, it replies with an error and the client
fails with the same reason.

``--reverse`` cannot be combined with ``--interactive``.

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
Sends do not cross event or period boundaries. Each UDP/TCP record carries a
1-based ``event_id`` matching the event or period index in the JSON file
(``0`` when not using a traffic pattern).

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
they may be transmitted. Pass ``--loops N`` to replay the pattern N times;
each repetition starts when the previous one ends (at the pattern's
duration). Event ids refer to the original file and repeat each loop.
``--traffic-pattern`` cannot be combined with ``--interactive``. With
``--reverse``, only the file name is sent to the server (see Reverse
mode).

Examples:

.. code-block:: bash

   ipyrf tcp client 127.0.0.1 --traffic-pattern trace.json
   ipyrf tcp client 127.0.0.1 --traffic-pattern trace.json --loops 3
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

You can run clients in an interactive mode that lets you adjust the pacing live using your keyboard. Use ``--interactive``. When interactive is enabled, the same client logic is used underneath with a dynamic pacing controller.

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

   # TCP interactive with initial pacing
   ipyrf tcp client 127.0.0.1 --port 5201 --bandwidth 200M --set-mss 1400 --interactive

   # UDP interactive (requires initial --bandwidth)
   ipyrf udp client 127.0.0.1 --port 5201 --bandwidth 50M -l 1200 --interactive

CLI overview
------------

Top-level structure:

.. code-block:: text

   ipyrf [tcp|udp] [server|client] [OPTIONS]
   ipyrf generate video -o FILE [OPTIONS]
   ipyrf offset HOST [OPTIONS]
   ipyrf-report RECORD [OPTIONS]

Common options (both protocols, both roles):

- ``--port``: Port (default 5201)
- ``--logfile``: Redirect output to a file
- ``--json_log``: Emit logs in JSON (newline-delimited)
- ``--record PATH``: Write received records to a CSV file after the test
  (UDP datagrams or TCP framed records). On a client, requires ``--reverse``

TCP-specific options:

- ``tcp server ADDRESS``: Start a TCP server on ``ADDRESS``
- ``tcp client ADDRESS``: Start a TCP client to connect to ``ADDRESS``
- ``--congestion-control``: Select Linux TCP CC algorithm if available
- ``--time``: Test duration (seconds), default 10
- ``--bandwidth``: Target rate (e.g., ``50M``); pacing cap, also usable with
  ``--traffic-pattern``
- ``--traffic-pattern FILE``: Drive sends from a JSON traffic-pattern file
  (``trace`` or ``piecewise_rate``). With ``--reverse``, only the file name
  is sent; the server must have the same file
- ``--trace-dir DIR``: TCP server: directory of traffic-pattern files for
  reverse tests (default: the server's working directory)
- ``--loops N``: Replay ``--traffic-pattern`` N times (default 1)
- ``--set-mss``: Set approximate MSS via ``TCP_MAXSEG``
- ``--bind-dev DEVICE``: Bind the client socket to a network interface
  (Linux ``SO_BINDTODEVICE``; typically requires ``CAP_NET_RAW`` or root)
- ``--reverse`` / ``-R``: Server sends, client receives. Forwards
  ``--bandwidth`` and ``--time``, or the name of ``--traffic-pattern``,
  to the server
- ``--interactive``: Enable interactive pacing controls

UDP-specific options:

- ``udp server ADDRESS``: Start a UDP server on ``ADDRESS``
- ``udp client ADDRESS``: Start a UDP client to ``ADDRESS``
- ``--time``: Test duration (seconds), default 10
- ``--bandwidth``: Target rate (e.g., ``50M``); pacing cap, also usable with
  ``--traffic-pattern``
- ``--traffic-pattern FILE``: Drive sends from a JSON traffic-pattern file
  (``trace`` or ``piecewise_rate``). With ``--reverse``, only the file name
  is sent; the server must have the same file
- ``--loops N``: Replay ``--traffic-pattern`` N times (default 1)
- ``-l/--length``: UDP payload length (default 1200)
- ``--inactivity-timeout SECONDS``: UDP server: exit after this many seconds
  without receiving packets (default 2.0)
- ``--trace-dir DIR``: UDP server: directory of traffic-pattern files for
  reverse tests (default: the server's working directory)
- ``--bind-dev DEVICE``: Bind the client socket to a network interface
  (Linux ``SO_BINDTODEVICE``; typically requires ``CAP_NET_RAW`` or root)
- ``--reverse`` / ``-R``: Server sends, client receives. Forwards
  ``--bandwidth``, ``--time``, and ``-l``, or the name of
  ``--traffic-pattern``, to the server
- ``--interactive``: Enable interactive pacing controls

Generate options:

- ``generate video -o FILE``: Write a video-like ``trace`` JSON file
- ``--from MEDIA``: Build the trace from video frames via ``ffprobe``
- ``--ffprobe-json FILE``: Same, from a saved ``ffprobe -show_frames`` dump
- ``--fps`` / ``--gop`` / ``--i-size`` / ``--p-size`` / ``--b-size``:
  Synthetic mode parameters (ignored when using ``--from`` /
  ``--ffprobe-json``)
- ``--duration``: Synthetic length in seconds (default 10), or optional
  truncate when importing from ffprobe

Offset options:

- ``offset HOST``: Measure clock offset to ``HOST`` over SSH
- ``--samples N``: Number of round-trip samples (default 30)
- ``--interval SECONDS``: Delay between samples (default 0.2)
- ``--json``: Write a JSON summary to stdout
- ``--python CMD``: Python interpreter on the remote host (default
  ``python3``)
- ``--ssh-arg ARG``: Extra argument forwarded to ``ssh`` (repeatable)

Report options (``ipyrf-report``):

- ``RECORD``: Record CSV from ``--record``
- ``-o/--output FILE``: HTML output path (default: ``RECORD`` with ``.html``)
- ``--traffic-pattern FILE``: Traffic-pattern JSON used during the test
- ``--json FILE``: Also write analysis results as JSON
- ``--png [DIR]`` / ``--svg [DIR]``: Export static plots (requires kaleido)
- ``--bin-ms MS``: Time-series bin size in milliseconds (default: auto)
- ``--title TITLE``: Report title

Receive recording
-----------------

Pass ``--record PATH`` on the receiving side (a server, or a client with
``--reverse``) to write one CSV row per received UDP datagram or complete
TCP framed record (sequence, sender timestamp, receiver timestamp, size,
and traffic-pattern ``event_id``). UDP uses the sender sequence number so
loss and reordering stay visible. TCP assigns sequence in receive order
(1, 2, 3, …) because the stream has no packet sequence. Records are packed
into fixed-size in-memory chunks during the test so the receive path stays
free of CSV I/O. Chunks are allocated before the test starts (about 20 s of
1 Gbit/s traffic by default) so rotation does not stall the receive loop.
When the test ends, the CSV file is written to ``PATH``. It starts with a
``# ipyrf-record 1`` magic line and a ``# { ... }`` JSON comment describing
the run (protocol, sequence kind, addresses, optional traffic pattern),
then the header row and data (columns ``sequence``, ``size_bytes``,
``transmitted_ns``, ``received_ns``, ``event_id``).
One-way latency in nanoseconds is ``received_ns - transmitted_ns``.
That difference includes any clock offset between sender and receiver;
use ``ipyrf offset`` to measure it. ``event_id`` is ``0`` unless the
sender used ``--traffic-pattern``.
``ipyrf-report`` reads that metadata to label the run and to omit TCP
loss/reorder charts. Pass ``--traffic-pattern`` to override an embedded
pattern. Spreadsheet tools treat the ``#`` lines as extra rows; skip them
or use ``ipyrf-report``.

Quick local test (two terminals):

.. code-block:: bash

   # Terminal 1 — server with recording
   ipyrf udp server 0.0.0.0 --port 12345 --record records.csv
   # or: ipyrf tcp server 0.0.0.0 --port 12345 --record records.csv

   # Terminal 2 — short paced client
   ipyrf udp client 127.0.0.1 --port 12345 --bandwidth 50M --time 2 -l 1200
   # or: ipyrf tcp client 127.0.0.1 --port 12345 --bandwidth 50M --time 2

When the client finishes, the server exits and ``records.csv`` contains the
trace. Use ``--enable-latency`` on the client if you also want latency
stats in the server summary. Turn the CSV into an interactive HTML report
with ``ipyrf-report`` (see below).

Reports (ipyrf-report)
----------------------

``ipyrf-report`` reads a ``--record`` CSV (including the JSON metadata
comment) and writes one self-contained interactive HTML file (Plotly.js is
embedded, so the file works offline). Pass ``--traffic-pattern`` to override
a pattern embedded in the record metadata; otherwise the report uses the
embedded pattern when present.

The report is laid out like a performance trace: KPI overview at the top,
then throughput, latency (p50 / p95 / p99), loss and sequence / reordering
when the recording is UDP, send vs receive spacing, and latency
distribution / ECDF.

.. code-block:: bash

   ipyrf-report records.csv
   ipyrf-report records.csv -o report.html --traffic-pattern trace.json
   ipyrf-report records.csv --json report.json --bin-ms 10
   ipyrf-report records.csv --png --svg

``-o`` defaults to the record path with a ``.html`` suffix. ``--png`` and
``--svg`` write individual plots next to the HTML (or to a directory you
pass); they require the optional ``kaleido`` extra:

.. code-block:: bash

   python3 -m pip install 'ipyrf[report-export]'

Clock offset
------------

``ipyrf offset HOST`` measures the wall-clock offset between this machine
and a remote SSH host. It does not change either clock. Each sample is a
Cristian / NTP-style round trip: a newline is sent over SSH, the remote
answers with ``time.time_ns()``, and the offset is ``remote - (t0 + t1) / 2``.
A positive offset means the remote clock is ahead of this host. The
reported offset is taken from the sample with the smallest RTT. The
first SSH round trip is discarded so handshake, host-key prompts, and
remote Python startup are not counted as a sample.

This is useful when comparing ``transmitted_ns`` and ``received_ns`` in a
``--record`` CSV from a two-host test. One-way latency is
``received_ns - transmitted_ns``. If this host sent, subtract the
offset; if the remote sent, add it.

The remote host needs ``python3`` (or pass ``--python``). SSH uses your
normal configuration and keys.

.. code-block:: bash

   ipyrf offset user@rasp1
   ipyrf offset user@rasp1 --samples 50 --interval 0.1
   ipyrf offset rasp1 --json
   ipyrf offset rasp1 --ssh-arg=-p --ssh-arg=2222

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
- On Linux, ``--bind-dev`` uses ``SO_BINDTODEVICE`` and typically needs
  ``CAP_NET_RAW`` or root.

License
-------

MIT. See ``LICENSE``.
