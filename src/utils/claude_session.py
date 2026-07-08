"""Shared naming + discovery for the per-project Claude tmux session.

The session supervisor runs ``claude`` inside a tmux session named
``cc-<hash>``. Both the ProjectWindow (which creates/attaches) and the Project
Manager (which shows a live-session indicator) must agree on the name, so the
logic lives here in one place.
"""

import hashlib
import os
import subprocess

_PREFIX = "cc-"


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
