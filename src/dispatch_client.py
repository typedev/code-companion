"""Laptop side of the PTY channel — the command VTE runs as its terminal child.

Usage: ``python -m src.dispatch_client <host> <port> <token> <session>``

It connects to the desktop broker's PTY bridge, hands over a JSON handshake, and
then relays bytes between this terminal (stdin/stdout, which VTE has wired to its
local PTY) and the socket. Terminal resizes become RESIZE frames; the local tty
is put in raw mode and restored on every exit path so VTE is never left cooked.
"""

from __future__ import annotations

import json
import os
import select
import signal
import socket
import sys
import termios
import tty

from .dispatch.protocol import (
    FRAME_DATA,
    FrameParser,
    encode_data,
    encode_resize,
)

_STDIN = 0
_STDOUT = 1
_CHUNK = 65536


def _term_size() -> tuple[int, int]:
    try:
        size = os.get_terminal_size(_STDOUT)
        return size.columns, size.lines
    except OSError:
        return 80, 24


def _run(host: str, port: int, token: str, session: str) -> int:
    try:
        sock = socket.create_connection((host, port), timeout=10)
    except OSError as exc:
        sys.stderr.write(f"dispatch: cannot reach {host}:{port} ({exc})\r\n")
        return 1
    sock.settimeout(None)

    cols, rows = _term_size()
    handshake = {
        "token": token,
        "session": session,
        "term": os.environ.get("TERM", "xterm-256color"),
        "cols": cols,
        "rows": rows,
    }
    sock.sendall((json.dumps(handshake) + "\n").encode("utf-8"))

    # Self-pipe so SIGWINCH wakes the select loop safely (async-signal-safe).
    winch_r, winch_w = os.pipe()
    os.set_blocking(winch_w, False)
    signal.signal(signal.SIGWINCH, lambda *_: os.write(winch_w, b"\x00"))

    parser = FrameParser()
    stdin_is_tty = os.isatty(_STDIN)
    old_attr = termios.tcgetattr(_STDIN) if stdin_is_tty else None
    if stdin_is_tty:
        tty.setraw(_STDIN)

    try:
        while True:
            rlist, _, _ = select.select([_STDIN, sock, winch_r], [], [])

            if sock in rlist:
                try:
                    data = sock.recv(_CHUNK)
                except OSError:
                    break
                if not data:  # bridge closed / session ended
                    break
                for ftype, payload in parser.feed(data):
                    if ftype == FRAME_DATA and payload:
                        os.write(_STDOUT, payload)

            if _STDIN in rlist:
                try:
                    chunk = os.read(_STDIN, _CHUNK)
                except OSError:
                    chunk = b""
                if not chunk:  # local stdin EOF
                    break
                try:
                    sock.sendall(encode_data(chunk))
                except OSError:
                    break

            if winch_r in rlist:
                try:
                    os.read(winch_r, _CHUNK)  # drain
                except OSError:
                    pass
                c, r = _term_size()
                try:
                    sock.sendall(encode_resize(c, r))
                except OSError:
                    break
    finally:
        if old_attr is not None:
            try:
                termios.tcsetattr(_STDIN, termios.TCSADRAIN, old_attr)
            except termios.error:
                pass
        try:
            sock.close()
        except OSError:
            pass
        os.close(winch_r)
        os.close(winch_w)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 4:
        sys.stderr.write("usage: dispatch_client <host> <port> <token> <session>\r\n")
        return 2
    host, port_s, token, session = argv[0], argv[1], argv[2], argv[3]
    try:
        port = int(port_s)
    except ValueError:
        sys.stderr.write(f"dispatch: bad port {port_s!r}\r\n")
        return 2
    return _run(host, port, token, session)


if __name__ == "__main__":
    raise SystemExit(main())
