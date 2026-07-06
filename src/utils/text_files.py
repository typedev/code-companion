"""Text-file helpers: encoding-safe reads, line-ending detection, binary sniff.

These keep the editor honest about what is on disk: line endings are detected
and preserved (no silent CRLF→LF mangling), non-UTF-8 files are reported instead
of dumped into the buffer, and binary files are recognized before they reach a
text editor.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

# How much of a file to inspect when sniffing for binary content.
_BINARY_SNIFF_BYTES = 8192


class ReadResult(NamedTuple):
    """Result of :func:`read_text_file`.

    Attributes:
        text: File content with all line endings normalized to ``\\n``. Empty
            string when ``ok`` is False.
        line_ending: The dominant line ending detected in the original file
            (``"\\n"``, ``"\\r\\n"`` or ``"\\r"``). Defaults to ``"\\n"``.
        ok: True if the file decoded as UTF-8; False on a decode error.
    """

    text: str
    line_ending: str
    ok: bool


def detect_line_ending(text: str) -> str:
    """Return the dominant line ending in ``text`` (defaults to ``"\\n"``)."""
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    if crlf >= cr and crlf >= lf and crlf > 0:
        return "\r\n"
    if cr > lf:
        return "\r"
    return "\n"


def read_text_file(path: str | os.PathLike) -> ReadResult:
    """Read ``path`` as UTF-8, preserving and reporting its line ending.

    Opens with ``newline=""`` so the original endings are visible, detects the
    dominant one, then normalizes the returned text to ``\\n`` for the editor
    buffer. Raises ``OSError`` for I/O problems; a non-UTF-8 file is reported via
    ``ok=False`` rather than raising.
    """
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            raw = f.read()
    except UnicodeDecodeError:
        return ReadResult("", "\n", False)

    line_ending = detect_line_ending(raw)
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    return ReadResult(normalized, line_ending, True)


def is_binary(path: str | os.PathLike) -> bool:
    """Return True if ``path`` looks binary (a null byte in the first 8 KB)."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return False
    return b"\x00" in chunk


def capture_stat(path: str | os.PathLike) -> tuple[int, int] | None:
    """Return ``(mtime_ns, size)`` for ``path``, or ``None`` if it can't be stat'd."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def stat_differs(path: str | os.PathLike, mtime_ns: int | None, size: int | None) -> bool:
    """True if ``path``'s current stat differs from the given ``(mtime_ns, size)``.

    A missing file counts as "differs". A previously-unknown baseline
    (``mtime_ns`` is None) counts as "does not differ" so a first save isn't
    treated as a conflict.
    """
    if mtime_ns is None:
        return False
    current = capture_stat(path)
    if current is None:
        return True
    return current != (mtime_ns, size)


def human_size(num_bytes: int) -> str:
    """Format a byte count for display (e.g. ``"1.5 MB"``)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def file_name(path: str | os.PathLike) -> str:
    """Return the base name of ``path`` for display."""
    return Path(path).name
