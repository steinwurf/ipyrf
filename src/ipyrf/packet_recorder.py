from __future__ import annotations

import csv
import struct
from pathlib import Path
from typing import Union

RECORD_STRUCT = struct.Struct("<QQII")
CSV_COLUMNS = ("tx_ns", "rx_ns", "seq", "size", "latency_ns")

# Defaults target ~1 Gbit/s with 1200-byte payloads (~104k pkt/s): one chunk
# holds ~5 s of traffic; eight chunks cover ~40 s (~96 MiB) without allocating
# on the receive path.
DEFAULT_RECORDS_PER_CHUNK = 262144 * 2
DEFAULT_NUM_CHUNKS = 8


class PacketRecorder:
    """Record UDP datagram traces in memory; write CSV when closed.

    Each record is ``tx_timestamp_ns, rx_timestamp_ns, sequence, size`` packed
    with :data:`RECORD_STRUCT` (two little-endian ``uint64`` timestamps and two
    ``uint32`` values). Latency is not stored; derive it as
    ``rx_timestamp_ns - tx_timestamp_ns``.

    Records are packed into fixed-size chunks during the test. Chunks are
    allocated up front so rotation does not zero a large buffer on the receive
    path. CSV is written only in :meth:`close`.
    """

    def __init__(
        self,
        path: Union[str, Path],
        *,
        records_per_chunk: int = DEFAULT_RECORDS_PER_CHUNK,
        num_chunks: int = DEFAULT_NUM_CHUNKS,
    ):
        if records_per_chunk < 1:
            raise ValueError("records_per_chunk must be >= 1")
        if num_chunks < 1:
            raise ValueError("num_chunks must be >= 1")

        self.path = Path(path)
        self._record = RECORD_STRUCT
        self._chunk_bytes = records_per_chunk * RECORD_STRUCT.size
        self.recorded = 0
        self.dropped = 0
        self._closed = False

        self._chunks: list[tuple[bytearray, int]] = []
        self._free = [bytearray(self._chunk_bytes) for _ in range(num_chunks)]
        self.current = self._free.pop()
        self.offset = 0

    def add(self, seq: int, tx_ns: int, rx_ns: int, size: int) -> None:
        self._record.pack_into(
            self.current, self.offset, tx_ns, rx_ns, seq, size
        )
        self.offset += self._record.size
        self.recorded += 1
        if self.offset == self._chunk_bytes:
            self._rotate()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self.current is not None and self.offset > 0:
            self._chunks.append((self.current, self.offset))
        self.current = None
        self.offset = 0
        self._free.clear()

        with open(self.path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)
            for chunk, nbytes in self._chunks:
                for tx_ns, rx_ns, seq, size in self._record.iter_unpack(
                    memoryview(chunk)[:nbytes]
                ):
                    writer.writerow((tx_ns, rx_ns, seq, size, rx_ns - tx_ns))

        self._chunks.clear()

    def __enter__(self) -> "PacketRecorder":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _next_chunk(self) -> bytearray:
        if self._free:
            return self._free.pop()
        return bytearray(self._chunk_bytes)

    def _rotate(self) -> None:
        filled = self.current
        if filled is None:
            return
        nbytes = self.offset
        if nbytes == 0:
            return
        self._chunks.append((filled, nbytes))
        self.current = self._next_chunk()
        self.offset = 0
