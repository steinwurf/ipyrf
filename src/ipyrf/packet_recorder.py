from __future__ import annotations

import argparse
import csv
import queue
import struct
import sys
import threading
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, Optional, Union

RECORD_STRUCT = struct.Struct("<IQQ")
CSV_COLUMNS = ("seq", "tx_ns", "rx_ns", "latency_ns")


class PacketRecorder:
    """Record UDP datagram traces without blocking the receive path.

    Each record is ``seq, tx_timestamp_ns, rx_timestamp_ns`` packed with
    :data:`RECORD_STRUCT`. Latency is not stored; derive it as
    ``rx_timestamp_ns - tx_timestamp_ns``.
    """

    def __init__(
        self,
        path: Union[str, Path],
        *,
        records_per_buffer: int = 65536,
        num_buffers: int = 4,
        start_writer: bool = True,
    ):
        if records_per_buffer < 1:
            raise ValueError("records_per_buffer must be >= 1")
        if num_buffers < 2:
            raise ValueError("num_buffers must be >= 2")

        self.path = Path(path)
        self._record = RECORD_STRUCT
        self._buffer_size = records_per_buffer * RECORD_STRUCT.size
        self.recorded = 0
        self.dropped = 0

        self._pool: queue.Queue[bytearray] = queue.Queue(maxsize=num_buffers)
        self._write_queue: queue.Queue[Optional[tuple[bytearray, int]]] = queue.Queue(
            maxsize=num_buffers - 1
        )
        for _ in range(num_buffers):
            self._pool.put(bytearray(self._buffer_size))

        self.current = self._pool.get()
        self.offset = 0

        self._file = open(self.path, "wb")
        self._closed = False
        self._writer_started = start_writer
        self._writer_thread: Optional[threading.Thread] = None
        if start_writer:
            self._writer_thread = threading.Thread(
                target=self._writer,
                name="ipyrf-packet-record",
                daemon=True,
            )
            self._writer_thread.start()

    def add(self, seq: int, tx_ns: int, rx_ns: int) -> None:
        buf = self.current
        if buf is None:
            try:
                buf = self._pool.get_nowait()
            except queue.Empty:
                self.dropped += 1
                return
            self.current = buf
            self.offset = 0

        self._record.pack_into(buf, self.offset, seq & 0xFFFFFFFF, tx_ns, rx_ns)
        self.offset += self._record.size
        self.recorded += 1
        if self.offset == self._buffer_size:
            self._rotate()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        remaining = None
        if self.current is not None and self.offset > 0:
            remaining = (self.current, self.offset)
            self.current = None
            self.offset = 0

        if self._writer_started:
            if remaining is not None:
                self._write_queue.put(remaining)
            self._write_queue.put(None)
            if self._writer_thread is not None:
                self._writer_thread.join()
        else:
            self._drain_write_queue()
            if remaining is not None:
                self._write_buffer(remaining)

        self._file.close()

    def __enter__(self) -> "PacketRecorder":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _rotate(self) -> None:
        filled = self.current
        nbytes = self.offset
        try:
            self._write_queue.put_nowait((filled, nbytes))
        except queue.Full:
            nrec = nbytes // self._record.size
            self.recorded -= nrec
            self.dropped += nrec
            self.offset = 0
            return

        try:
            self.current = self._pool.get_nowait()
        except queue.Empty:
            self.current = None
        self.offset = 0

    def _writer(self) -> None:
        while True:
            item = self._write_queue.get()
            if item is None:
                break
            self._write_buffer(item)
            self._write_queue.task_done()

    def _drain_write_queue(self) -> None:
        while True:
            try:
                item = self._write_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                continue
            self._write_buffer(item)

    def _write_buffer(self, item: tuple[bytearray, int]) -> None:
        buf, nbytes = item
        self._file.write(memoryview(buf)[:nbytes])
        self._pool.put(buf)


def iter_records(data: Union[bytes, bytearray, memoryview]) -> Iterator[tuple[int, int, int]]:
    yield from RECORD_STRUCT.iter_unpack(data)


def records_from_file(path: Union[str, Path, BinaryIO]) -> Iterator[tuple[int, int, int]]:
    if hasattr(path, "read"):
        data = path.read()
    else:
        with open(path, "rb") as f:
            data = f.read()
    yield from iter_records(data)


def export_csv(
    record_path: Union[str, Path],
    csv_path: Union[str, Path, None] = None,
    *,
    fileobj=None,
) -> None:
    """Write CSV with columns seq, tx_ns, rx_ns, latency_ns."""
    rows: Iterable[tuple[int, int, int]] = records_from_file(record_path)
    close_out = False
    if fileobj is not None:
        out = fileobj
    elif csv_path is not None:
        out = open(csv_path, "w", newline="")
        close_out = True
    else:
        out = sys.stdout

    try:
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        for seq, tx_ns, rx_ns in rows:
            writer.writerow((seq, tx_ns, rx_ns, rx_ns - tx_ns))
    finally:
        if close_out:
            out.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export an ipyrf UDP packet record to CSV"
    )
    parser.add_argument("record", help="Binary packet-record file")
    parser.add_argument(
        "csv",
        nargs="?",
        help="Output CSV path (default: stdout)",
    )
    args = parser.parse_args(argv)
    export_csv(args.record, args.csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
