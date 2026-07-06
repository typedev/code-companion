"""Atomic file writes.

Every write goes to a temporary file in the same directory, is flushed and
fsynced, then swapped into place with a single ``os.replace()``. Because the
target is only ever replaced atomically, a crash, ENOSPC, or any exception
mid-write can never truncate or corrupt the existing file — the worst case is
a leftover temp file, which is cleaned up on failure.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: str | os.PathLike, data: bytes, *, mode: int | None = None) -> None:
    """Atomically write ``data`` to ``path``.

    Args:
        path: Destination file path.
        data: Bytes to write.
        mode: Permission bits for the resulting file. If ``None``, the existing
            file's mode is preserved; for a new file the OS default applies.
    """
    path = Path(path)
    directory = path.parent

    # Preserve the original file mode unless one is explicitly requested.
    if mode is None and path.exists():
        try:
            mode = os.stat(path).st_mode
        except OSError:
            mode = None

    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        # The original file is untouched; drop the temp file and re-raise.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    else:
        # Best-effort: fsync the directory so the rename itself is durable.
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass


def atomic_write_text(
    path: str | os.PathLike,
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str = "\n",
    mode: int | None = None,
) -> None:
    """Atomically write ``text`` to ``path`` with explicit newline handling.

    ``\\n`` in ``text`` is translated to ``newline`` (e.g. ``\\r\\n``) so a
    file's original line ending survives an edit. No implicit translation is
    performed beyond this, unlike text-mode ``open()``.
    """
    if newline and newline != "\n":
        text = text.replace("\n", newline)
    atomic_write_bytes(path, text.encode(encoding), mode=mode)
