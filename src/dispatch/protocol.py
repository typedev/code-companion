"""Wire protocol for the dispatch PTY channel (raw framed TCP, stdlib only).

Both ends (the desktop ``pty_bridge`` and the laptop ``dispatch_client``) are
ours, so the framing is deliberately tiny — no WebSocket/library dependency to
vendor. The channel opens with a single newline-terminated JSON handshake line,
after which every message is a length-prefixed frame:

    ┌────────┬──────────────┬───────────────┐
    │ type   │ length       │ payload       │
    │ 1 byte │ 4 bytes (BE) │ <length> bytes│
    └────────┴──────────────┴───────────────┘

``DATA`` frames carry terminal bytes in both directions. ``RESIZE`` frames flow
client→bridge only and carry ``cols``/``rows`` as two big-endian uint16.
"""

from __future__ import annotations

import struct

FRAME_DATA = 0
FRAME_RESIZE = 1

_HEADER = struct.Struct("!BI")
_RESIZE = struct.Struct("!HH")

# Cap a single frame so a malformed/hostile length can't force a huge allocation.
MAX_FRAME = 1 << 20  # 1 MiB


def encode_frame(ftype: int, payload: bytes) -> bytes:
    """Serialize one frame."""
    return _HEADER.pack(ftype, len(payload)) + payload


def encode_data(payload: bytes) -> bytes:
    return encode_frame(FRAME_DATA, payload)


def encode_resize(cols: int, rows: int) -> bytes:
    return encode_frame(FRAME_RESIZE, _RESIZE.pack(cols, rows))


def decode_resize(payload: bytes) -> tuple[int, int]:
    """Return ``(cols, rows)`` from a RESIZE payload."""
    cols, rows = _RESIZE.unpack(payload)
    return cols, rows


class FrameParser:
    """Incremental parser: feed bytes, get back whole ``(type, payload)`` frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self._buf.extend(data)
        frames: list[tuple[int, bytes]] = []
        while True:
            if len(self._buf) < _HEADER.size:
                break
            ftype, length = _HEADER.unpack_from(self._buf, 0)
            if length > MAX_FRAME:
                raise ValueError(f"dispatch frame too large: {length}")
            end = _HEADER.size + length
            if len(self._buf) < end:
                break
            payload = bytes(self._buf[_HEADER.size:end])
            del self._buf[:end]
            frames.append((ftype, payload))
        return frames
