"""Session notification markers.

An agent session writes a per-session marker file when it finishes a turn or
waits for input; the Project Manager polls these markers to surface a "needs
attention" state + a desktop notification, even for detached sessions with no
open window (there is no live HTTP endpoint to push to, so this is a durable
pull channel).

Provider channels into the same marker files:
- Claude: a ``Notification`` hook (injected at launch via ``--settings``)
  writes the hook stdin JSON; ``UserPromptSubmit``/``Stop`` hooks clear it.
- Codex: the ``notify`` program (``codex-notify.sh``, a stable managed script —
  Codex invokes it for the whole session lifetime, so unlike Claude's read-once
  ``--settings`` file it must survive window teardown) writes the
  ``agent-turn-complete`` payload. Codex hooks are trust-gated (an injected
  hook is silently skipped until the user approves it), so there is no
  hook-based clear — the project window clears markers on terminal focus /
  keypress instead.

Marker file: ``<config>/notify/<session-name>.json`` — the raw provider JSON
(Claude: ``message``, ``notification_type``, ``cwd``, …; Codex:
``type``, ``last-assistant-message``, ``cwd``, …), normalized on read.
"""

import json
import shlex
from pathlib import Path

from .config_path import get_config_dir

# Bumped whenever the managed codex-notify.sh content changes, so existing
# installs regenerate it. The marker is embedded in the script itself.
_CODEX_SCRIPT_VERSION = 1


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
    """Return ``{session_name: {..provider payload.., "_mtime": float}}``.

    Payloads are normalized so consumers can rely on ``message``/``cwd``
    regardless of provider: Codex's ``agent-turn-complete`` JSON carries the
    turn summary in ``last-assistant-message`` instead of ``message``.
    """
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
        if not data.get("message"):
            text = data.get("last-assistant-message")
            if isinstance(text, str) and text.strip():
                data["message"] = text.strip().split("\n", 1)[0][:200]
            elif data.get("type") == "agent-turn-complete":
                data["message"] = "Agent finished a turn"
        out[path.stem] = data
    return out


def clear_command(session_name: str) -> str:
    """Shell command that idempotently drops a session's marker (+ tmp)."""
    marker = str(marker_path(session_name))
    return f"rm -f {shlex.quote(marker)} {shlex.quote(marker + '.tmp')}"


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
    clear_hook = [
        {"hooks": [{"type": "command", "command": clear_command(session_name)}]}
    ]
    return {
        "hooks": {
            "Notification": [
                {"matcher": "", "hooks": [{"type": "command", "command": write_cmd}]}
            ],
            "UserPromptSubmit": clear_hook,
            "Stop": clear_hook,
        }
    }


def ensure_codex_notify_script() -> Path | None:
    """The stable Codex ``notify`` wrapper script, (re)written when stale.

    Codex invokes ``notify = [<script>, <session-name>]`` with the event JSON
    appended as the last argument on every ``agent-turn-complete``, for the
    whole session lifetime — so this must be a stable managed file, NOT a
    read-once launch temp file (those are deleted at window teardown while the
    tmux session lives on). Returns None when the script cannot be written.
    """
    script = notify_dir() / "codex-notify.sh"
    content = (
        "#!/bin/sh\n"
        f"# Managed by Code Companion (v{_CODEX_SCRIPT_VERSION}); regenerated when stale.\n"
        '# $1 = session name, $2 = notify payload JSON appended by Codex.\n'
        'dir="$(dirname "$0")"\n'
        '[ -n "$1" ] && [ -n "$2" ] || exit 0\n'
        'case "$1" in */*|.*) exit 0;; esac\n'
        'tmp="$dir/$1.json.tmp"\n'
        'printf \'%s\' "$2" > "$tmp" && mv "$tmp" "$dir/$1.json"\n'
    )
    try:
        notify_dir().mkdir(parents=True, exist_ok=True)
        if not script.exists() or script.read_text(encoding="utf-8") != content:
            script.write_text(content, encoding="utf-8")
        script.chmod(0o700)
        return script
    except OSError:
        return None
