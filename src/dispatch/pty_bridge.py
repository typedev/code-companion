"""Desktop side of the PTY channel: attach a tmux session onto a socket.

For an authenticated connection the bridge spawns ``tmux attach`` on a real PTY
and pumps bytes between that PTY and the socket, translating RESIZE frames into
``TIOCSWINSZ`` (which the kernel turns into SIGWINCH for tmux). Closing the
socket only *detaches* the tmux client — the session keeps running, so the
laptop can reconnect and the desktop can re-open it locally.

Transport is the raw framed protocol in :mod:`.protocol` (stdlib only). The
handshake is one newline-terminated JSON line:
``{"token", "session", "term", "cols", "rows"}``.
"""

from __future__ import annotations

import asyncio
import fcntl
import hmac
import json
import os
import struct
import subprocess
import termios
from typing import Callable

from ..utils.claude_session import live_session_names, managed_tmux_conf
from .protocol import (
    FRAME_DATA,
    FRAME_RESIZE,
    MAX_FRAME,
    decode_resize,
    encode_data,
)

_HANDSHAKE_TIMEOUT = 10  # seconds to receive the JSON handshake line
_READ_CHUNK = 65536


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    """Apply a terminal window size to a pty fd (delivers SIGWINCH to tmux)."""
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def _reap(proc: subprocess.Popen) -> None:
    """Detach the tmux client (SIGTERM), then reap it off the event loop."""
    if proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(loop.run_in_executor(None, proc.wait), timeout=5)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except OSError:
            pass
        await loop.run_in_executor(None, proc.wait)


def _spawn_attach(session: str, cols: int, rows: int, term: str, tmux_conf: str):
    """Spawn ``tmux attach`` on a fresh PTY; return (proc, master_fd)."""
    master, slave = os.openpty()
    _set_winsize(master, cols, rows)

    env = os.environ.copy()
    env["TERM"] = term

    proc = subprocess.Popen(
        ["tmux", "-f", tmux_conf, "attach", "-t", f"={session}"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
        # Make the pty the controlling terminal of the new session so tmux
        # receives SIGWINCH on TIOCSWINSZ. preexec runs after setsid and before
        # the fd shuffle, so the slave fd is still valid here.
        preexec_fn=lambda: fcntl.ioctl(slave, termios.TIOCSCTTY, 0),
        env=env,
        close_fds=True,
    )
    os.close(slave)
    return proc, master


async def handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    authorize: Callable[[str], bool],
    tmux_conf: str | None = None,
) -> None:
    """Serve one dispatch PTY connection end to end.

    ``authorize`` is called with the handshake token and must return True to let
    the connection proceed (per-device token lookup, or a fixed-token compare).
    """
    try:
        await _serve(reader, writer, authorize, tmux_conf or managed_tmux_conf())
    finally:
        try:
            writer.close()
        except OSError:
            pass


def _reject(writer: asyncio.StreamWriter, reason: str) -> None:
    """Send a human-readable failure to the client's terminal, then close."""
    try:
        writer.write(encode_data(f"dispatch: {reason}\r\n".encode()))
    except OSError:
        pass


async def _serve(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    authorize: Callable[[str], bool],
    tmux_conf: str,
) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=_HANDSHAKE_TIMEOUT)
    except (asyncio.TimeoutError, ConnectionError):
        return
    if not line:
        return
    try:
        hs = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _reject(writer, "bad handshake")
        return

    token = str(hs.get("token", ""))
    if not token or not authorize(token):
        _reject(writer, "unauthorized")
        return

    session = str(hs.get("session", ""))
    if session not in live_session_names():
        _reject(writer, "no such session")
        return

    try:
        cols = max(1, int(hs.get("cols") or 80))
        rows = max(1, int(hs.get("rows") or 24))
    except (TypeError, ValueError):
        cols, rows = 80, 24
    term = str(hs.get("term") or "xterm-256color")

    await _bridge(reader, writer, session, cols, rows, term, tmux_conf)


async def _bridge(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    session: str,
    cols: int,
    rows: int,
    term: str,
    tmux_conf: str,
) -> None:
    loop = asyncio.get_running_loop()
    proc, master = _spawn_attach(session, cols, rows, term, tmux_conf)
    done = asyncio.Event()

    def on_master_readable() -> None:
        try:
            data = os.read(master, _READ_CHUNK)
        except OSError:
            data = b""
        if not data:  # tmux client exited / detached
            try:
                loop.remove_reader(master)
            except (ValueError, OSError):
                pass
            done.set()
            return
        try:
            writer.write(encode_data(data))
        except OSError:
            done.set()

    loop.add_reader(master, on_master_readable)

    async def socket_to_pty() -> None:
        try:
            while True:
                header = await reader.readexactly(5)
                ftype, length = struct.unpack("!BI", header)
                if length > MAX_FRAME:
                    break
                payload = await reader.readexactly(length) if length else b""
                if ftype == FRAME_DATA:
                    os.write(master, payload)
                elif ftype == FRAME_RESIZE:
                    c, r = decode_resize(payload)
                    _set_winsize(master, c, r)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            done.set()

    task = asyncio.create_task(socket_to_pty())
    try:
        await done.wait()
    finally:
        try:
            loop.remove_reader(master)
        except (ValueError, OSError):
            pass
        task.cancel()
        try:
            os.close(master)
        except OSError:
            pass
        await _reap(proc)


async def serve(host: str, port: int, token: str) -> None:
    """Standalone fixed-token PTY-bridge server (isolation testing)."""
    def authorize(t: str) -> bool:
        return hmac.compare_digest(t, token)

    server = await asyncio.start_server(
        lambda r, w: handle_connection(r, w, authorize=authorize),
        host,
        port,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    import sys

    _host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    _port = int(sys.argv[2]) if len(sys.argv) > 2 else 47101
    _token = sys.argv[3] if len(sys.argv) > 3 else "test-token"
    print(f"pty-bridge listening on {_host}:{_port}")
    asyncio.run(serve(_host, _port, _token))
