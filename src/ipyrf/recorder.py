from __future__ import annotations

import csv
import json
import struct
from pathlib import Path
from typing import Any, Dict, Optional, Union

RECORD_STRUCT = struct.Struct("<QQIII")
CSV_COLUMNS = (
    "sequence",
    "size_bytes",
    "transmitted_ns",
    "received_ns",
    "event_id",
)

METADATA_FORMAT = "ipyrf-record"
METADATA_VERSION = 1
METADATA_MAGIC = f"{METADATA_FORMAT} {METADATA_VERSION}"

# Defaults target ~1 Gbit/s with 1200-byte payloads (~104k pkt/s): one chunk
# holds ~5 s of traffic; eight chunks cover ~40 s (~96 MiB) without allocating
# on the receive path.
DEFAULT_RECORDS_PER_CHUNK = 262144 * 2
DEFAULT_NUM_CHUNKS = 8


def package_version() -> str:
    try:
        from importlib.metadata import version

        return version("ipyrf")
    except Exception:
        return "unknown"


def parse_record_text(text: str) -> tuple[Dict[str, Any], str]:
    """Split a record file into metadata (from ``#`` JSON comments) and CSV.

    Lines that start with ``#`` are comments. A comment whose remainder is a
    JSON object is merged into the metadata dict. All other comment lines
    (including the ``# ipyrf-record 1`` magic) are skipped.
    """
    metadata: Dict[str, Any] = {}
    data: list[str] = []
    for line in text.splitlines(keepends=True):
        raw = line.lstrip("\ufeff")
        if raw.startswith("#"):
            payload = raw[1:].strip()
            if payload.startswith("{"):
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    metadata.update(obj)
            continue
        data.append(line)
    return metadata, "".join(data)


class Recorder:
    """Record received send units in memory; write CSV when closed.

    Each row is one UDP datagram or one complete TCP framed record:
    ``tx_timestamp_ns, rx_timestamp_ns, sequence, size, event_id`` packed
    with :data:`RECORD_STRUCT` (two little-endian ``uint64`` timestamps
    and three ``uint32`` values). Latency is not stored; derive it as
    ``rx_timestamp_ns - tx_timestamp_ns``. ``event_id`` is ``0`` when
    unset (no traffic-pattern attribution).

    Sequence is assigned in receive order (1, 2, 3, …) unless the caller
    passes ``seq`` (UDP uses the sender sequence so loss and reordering
    remain visible).

    Records are packed into fixed-size chunks during the test. Chunks are
    allocated up front so rotation does not zero a large buffer on the
    receive path. CSV is written only in :meth:`close`. The file starts
    with a magic comment and a JSON metadata comment, then the header
    row and data.
    """

    def __init__(
        self,
        path: Union[str, Path],
        *,
        records_per_chunk: int = DEFAULT_RECORDS_PER_CHUNK,
        num_chunks: int = DEFAULT_NUM_CHUNKS,
        metadata: Optional[Dict[str, Any]] = None,
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
        self.metadata: Dict[str, Any] = _omit_none(metadata or {})

        self._chunks: list[tuple[bytearray, int]] = []
        self._free = [bytearray(self._chunk_bytes) for _ in range(num_chunks)]
        self.current = self._free.pop()
        self.offset = 0

    def update_metadata(self, **fields: Any) -> None:
        """Merge non-None fields into the run metadata written on close."""
        self.metadata.update(_omit_none(fields))

    def add(
        self,
        tx_ns: int,
        rx_ns: int,
        size: int,
        event_id: int = 0,
        *,
        seq: Optional[int] = None,
    ) -> None:
        if seq is None:
            seq = self.recorded + 1
        self._record.pack_into(
            self.current, self.offset, tx_ns, rx_ns, seq, size, event_id
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

        meta = {
            "format": METADATA_FORMAT,
            "version": METADATA_VERSION,
            "ipyrf_version": package_version(),
            **self.metadata,
            "record_count": self.recorded,
            "record_dropped": self.dropped,
        }

        with open(self.path, "w", newline="", encoding="utf-8") as f:
            f.write(f"# {METADATA_MAGIC}\n")
            f.write("# " + json.dumps(meta, separators=(",", ":")) + "\n")
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)
            for chunk, nbytes in self._chunks:
                for tx_ns, rx_ns, seq, size, event_id in self._record.iter_unpack(
                    memoryview(chunk)[:nbytes]
                ):
                    writer.writerow((seq, size, tx_ns, rx_ns, event_id))

        self._chunks.clear()

    def __enter__(self) -> "Recorder":
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


def _omit_none(fields: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}
