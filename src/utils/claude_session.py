"""Shared naming + discovery for the per-project Claude tmux session.

The session supervisor runs ``claude`` inside a tmux session named
``cc-<hash>``. Both the ProjectWindow (which creates/attaches) and the Project
Manager (which shows a live-session indicator) must agree on the name, so the
logic lives here in one place.
"""

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Callable

_PREFIX = "cc-"


def managed_tmux_conf() -> str:
    """Absolute path to the managed tmux config (loaded via ``tmux -f``).

    Shared by the ProjectWindow supervisor and the dispatch PTY bridge so a
    remote attach uses the exact same tmux config as a local one.
    """
    return str(Path(__file__).resolve().parent.parent / "resources" / "tmux" / "tmux-managed.conf")

# MCP endpoint port range for managed sessions. Chosen below both the Linux
# (32768–60999) and macOS (49152–65535) default ephemeral ranges, so the OS
# never auto-assigns one of our ports to an unrelated socket — the port stays
# reservable across a window-restart gap. Inside IANA user/registered space.
PORT_MIN = 20000
PORT_MAX = 29999


def session_name(path: str) -> str:
    """Deterministic, machine-local tmux session name for a project path.

    Path-keyed (aligns with the one-window-per-project-path lock), so it is
    stable regardless of git state — unlike ``resolve_project_identity``.
    """
    key = os.path.realpath(str(path))
    return _PREFIX + hashlib.sha1(key.encode()).hexdigest()[:12]


def live_session_names() -> set[str]:
    """Names of currently running ``cc-*`` tmux sessions (default socket).

    Returns an empty set on any failure (tmux missing, no server, timeout).
    """
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith(_PREFIX)
    }


def session_cwd(name: str) -> str | None:
    """Working directory of a tmux session's active pane, or None."""
    # NB: no "=" exact-match prefix here — it makes display-message resolve to an
    # empty pane target. Session names are unique hashes, so a plain name is safe.
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", name, "-p", "#{pane_current_path}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = result.stdout.strip()
    return path if result.returncode == 0 and path else None


def session_env(name: str, var: str) -> str | None:
    """Read one environment variable from a tmux session, or None if unset.

    ``tmux show-environment -t <name> <var>`` prints ``VAR=value`` when set,
    or ``-VAR`` when explicitly unset; anything else (missing session, error)
    yields None.
    """
    try:
        result = subprocess.run(
            ["tmux", "show-environment", "-t", name, var],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip()
    prefix = f"{var}="
    if line.startswith(prefix):
        return line[len(prefix):]
    return None


def session_clients(name: str) -> int:
    """Number of tmux clients currently attached to a session (0 if none/error).

    A live session with zero clients is "free" (window closed, still running in
    tmux) and may be attached from another machine; a non-zero count means it is
    held — attached locally or already dispatched to a remote client.
    """
    try:
        result = subprocess.run(
            ["tmux", "list-clients", "-t", f"={name}", "-F", "#{client_name}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if result.returncode != 0:
        return 0
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def reserved_ports(exclude: str | None = None) -> set[int]:
    """MCP ports already claimed by other live ``cc-*`` sessions.

    A detached session (its window closed) is still live in tmux and still owns
    its port, so a fresh session must avoid it even though nothing is listening
    on that port right now.
    """
    ports: set[int] = set()
    for name in live_session_names():
        if name == exclude:
            continue
        value = session_env(name, "CC_MCP_PORT")
        if value and value.isdigit():
            ports.add(int(value))
    return ports


def pick_stable_port(
    name: str, reserved: set[int], is_free: Callable[[int], bool]
) -> int | None:
    """Pick a deterministic, reservation-aware MCP port for session ``name``.

    Starts at a hash-derived offset in [PORT_MIN, PORT_MAX] (so the same project
    tends to reuse the same port across restarts) and probes forward with
    wraparound, skipping ports in ``reserved`` or where ``is_free`` is False.
    Returns None if the whole range is exhausted. I/O is injected via ``is_free``
    so the logic is pure and testable.
    """
    span = PORT_MAX - PORT_MIN + 1
    base = int(hashlib.sha1(name.encode()).hexdigest(), 16) % span
    for i in range(span):
        port = PORT_MIN + (base + i) % span
        if port in reserved:
            continue
        if is_free(port):
            return port
    return None


def kill_session(name: str) -> bool:
    """Kill a tmux session by exact name. Returns True if it is gone afterwards."""
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", f"={name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return name not in live_session_names()
