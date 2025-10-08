ipyrf
=====

Minimal iperf3-like network throughput tool with JSON output. Supports TCP and UDP, server and client modes.

Features
--------
- TCP and UDP tests
- JSON or human-readable output
- Optional bandwidth capping (TCP/UDP)
- UDP packet loss estimation
- Linux TCP congestion control selection (if available)

Installation
------------

From PyPI (recommended):

.. code-block:: bash

   pip install ipyrf

From source (editable):

.. code-block:: bash

   python -m venv .venv
   source .venv/bin/activate
   pip install -U pip build
   pip install -e .

Usage
-----

The package installs a console script named ``ipyrf``.

TCP server:

.. code-block:: bash

   ipyrf tcp server 0.0.0.0 --port 12345

TCP client:

.. code-block:: bash

   ipyrf tcp client 127.0.0.1 --port 12345 --time 5
   ipyrf tcp client 127.0.0.1 --port 12345 --time 5 --set-mss 1400

UDP server:

.. code-block:: bash

   ipyrf udp server 0.0.0.0 --port 12345

UDP client (with bandwidth cap and optional payload size):

.. code-block:: bash

   ipyrf udp client 127.0.0.1 --port 12345 --bandwidth 50M --time 5
   ipyrf udp client 127.0.0.1 --port 12345 --bandwidth 50M --time 5 -l 1200

Notes
-----

- Output is JSON (newline-delimited for update events) when ``--json_log`` is given; otherwise, a human-readable summary is printed.
- UDP mode sends a FIN marker at the end and the server exits after FIN (or inactivity timeout).
- On Linux, congestion control selection is exposed if ``/proc`` entries are available.

License
-------

MIT. See ``LICENSE``.
