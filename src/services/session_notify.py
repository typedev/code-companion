"""Session notification markers.

A Claude session's ``Notification`` hook (injected at launch via ``--settings``)
writes a per-session marker file when Claude finishes or waits for input. The
Project Manager polls these markers to surface a "needs attention" state + a
desktop notification, even for detached sessions with no open window (there is
no live HTTP endpoint to push to, so this is a durable pull channel).

Marker file: ``<config>/notify/<session-name>.json`` — the raw hook stdin JSON
(``message``, ``notification_type``, ``cwd``, …).
"""

import json
import shlex
from pathlib import Path

from .config_path import get_config_dir


def notify_dir() -> Path:
    return get_config_dir() / "notify"


def marker_path(session_name: str) -> Path:
    return notify_dir() / f"{session_name}.json"


def clear_marker(session_name: str) -> None:
    """Drop a session's marker (e.g. once the user has attended it)."""
    try:
        marker_path(session_name).unlink()
    except OSError:
        pass


def read_markers() -> dict[str, dict]:
    """Return ``{session_name: {..hook payload.., "_mtime": float}}``."""
    out: dict[str, dict] = {}
    directory = notify_dir()
    if not directory.exists():
        return out
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            data["_mtime"] = path.stat().st_mtime
        except (OSError, json.JSONDecodeError):
            continue
        out[path.stem] = data
    return out


def hook_settings(session_name: str) -> dict:
    """A Claude Code ``--settings`` payload driving this session's marker file.

    - ``Notification`` writes the event JSON to the marker (Claude is waiting).
    - ``UserPromptSubmit`` / ``Stop`` remove it — the user has answered or Claude
      resumed, so the "needs attention" state is over. Without these clear hooks
      the marker (and the PM's amber dot) would stick until the session dies.

    Marker writes are atomic (tmp + ``mv``); clears are idempotent (``rm -f``).
    No ``jq`` dependency.
    """
    marker = str(marker_path(session_name))
    tmp = marker + ".tmp"
    write_cmd = (
        f"mkdir -p {shlex.quote(str(notify_dir()))} && "
        f"cat > {shlex.quote(tmp)} && mv {shlex.quote(tmp)} {shlex.quote(marker)}"
    )
    clear_cmd = f"rm -f {shlex.quote(marker)} {shlex.quote(tmp)}"
    clear_hook = [{"hooks": [{"type": "command", "command": clear_cmd}]}]
    return {
        "hooks": {
            "Notification": [
                {"matcher": "", "hooks": [{"type": "command", "command": write_cmd}]}
            ],
            "UserPromptSubmit": clear_hook,
            "Stop": clear_hook,
        }
    }
