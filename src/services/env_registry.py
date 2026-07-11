"""Registry of terminal environment activators (project marker → shell command).

Generalizes the old ``.venv``-only auto-activation: on shell spawn, each activator
whose marker is present contributes a line fed to the terminal. ``venv`` covers
Python; ``direnv`` / ``mise`` are the reliable polyglot escape hatches (detected by
a real binary on PATH, unlike nvm which is a shell function).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gi.repository import GLib


@dataclass(frozen=True)
class EnvActivator:
    """Detect a project's environment and return the shell line to activate it."""
    id: str
    detect: Callable[[Path], str | None]


def _venv(d: Path) -> str | None:
    activate = d / ".venv" / "bin" / "activate"
    if activate.is_file():
        return f"source {GLib.shell_quote(str(activate))}"
    return None


def _direnv(d: Path) -> str | None:
    if (d / ".envrc").is_file() and shutil.which("direnv"):
        return 'eval "$(direnv hook bash)"'
    return None


def _mise(d: Path) -> str | None:
    if (d / ".tool-versions").is_file() and shutil.which("mise"):
        return 'eval "$(mise activate bash)"'
    return None


ACTIVATORS: list[EnvActivator] = [
    EnvActivator("venv", _venv),
    EnvActivator("direnv", _direnv),
    EnvActivator("mise", _mise),
]


def activation_commands(project_dir: str | Path) -> list[str]:
    """Shell lines to activate every environment detected in ``project_dir``."""
    d = Path(project_dir)
    commands = []
    for activator in ACTIVATORS:
        try:
            cmd = activator.detect(d)
        except Exception:
            cmd = None
        if cmd:
            commands.append(cmd)
    return commands
