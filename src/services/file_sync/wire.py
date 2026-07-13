"""Framed byte protocol for streaming a batch of files over one HTTP response.

Per file: ``[4-byte BE rel-length][rel utf-8][8-byte BE data-length][data]``.
The stream ends at EOF (no trailing marker). Shared by the broker (encode) and
the client (decode) so the two can't drift.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator


def encode_file(rel: str, data: bytes) -> bytes:
    """One framed file record."""
    rb = rel.encode("utf-8")
    return struct.pack(">I", len(rb)) + rb + struct.pack(">Q", len(data)) + data


def _read_exact(read: Callable[[int], bytes], n: int) -> bytes:
    """Read exactly ``n`` bytes; return fewer only at EOF."""
    buf = bytearray()
    while len(buf) < n:
        chunk = read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def read_files(read: Callable[[int], bytes]) -> Iterator[tuple[str, bytes]]:
    """Decode a framed stream. ``read(n)`` returns up to ``n`` bytes (fewer at EOF)."""
    while True:
        head = _read_exact(read, 4)
        if len(head) < 4:
            return  # clean EOF at a frame boundary
        (rel_len,) = struct.unpack(">I", head)
        rel = _read_exact(read, rel_len).decode("utf-8", "replace")
        size_bytes = _read_exact(read, 8)
        if len(size_bytes) < 8:
            return  # truncated stream — stop
        (data_len,) = struct.unpack(">Q", size_bytes)
        data = _read_exact(read, data_len)
        if len(data) < data_len:
            return  # truncated payload — stop
        yield rel, data
