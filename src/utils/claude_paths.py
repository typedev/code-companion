"""Central map of Claude Code's on-disk layout under ~/.claude.

Every path derives from ``Path.home()`` so overriding ``HOME`` relocates the
whole tree — this is what the cross-machine sync test harness relies on to
simulate a second machine without a code seam.
"""

from pathlib import Path

from .paths import encode_project_path


def claude_home() -> Path:
    """The ~/.claude root."""
    return Path.home() / ".claude"


def projects_root() -> Path:
    """~/.claude/projects."""
    return claude_home() / "projects"


def project_dir(local_abs_path: str | Path) -> Path:
    """~/.claude/projects/<encoded> for a local project path.

    The encoding mirrors Claude Code (``/`` -> ``-``) applied to the resolved
    absolute path, matching how ``HistoryService`` locates session directories.
    """
    resolved = str(Path(local_abs_path).resolve())
    return projects_root() / encode_project_path(resolved)


def project_memory_dir(local_abs_path: str | Path) -> Path:
    """The per-project memory directory (may not exist)."""
    return project_dir(local_abs_path) / "memory"


def project_sessions(local_abs_path: str | Path) -> list[Path]:
    """The per-project session JSONL files (empty list if none)."""
    directory = project_dir(local_abs_path)
    if not directory.exists():
        return []
    return sorted(directory.glob("*.jsonl"))


def plans_dir() -> Path:
    """~/.claude/plans (global, flat)."""
    return claude_home() / "plans"


def settings_json() -> Path:
    """~/.claude/settings.json (global user settings)."""
    return claude_home() / "settings.json"


def claude_json() -> Path:
    """~/.claude.json (monolithic config; only a per-project slice is synced)."""
    return Path.home() / ".claude.json"
