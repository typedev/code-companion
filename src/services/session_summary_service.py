"""Per-project session summaries — a user-facing handoff shown in Project Manager.

The agent writes a résumé / next-session plan at session end (via the MCP
``set_session_summary`` tool); the Project Manager surfaces it on the project card. Stored
in the app's own config dir (not Claude Code's ``~/.claude`` tree, so a Claude Code update
can't orphan it) and picked up by the sync/backup global layer alongside ``plans/``.

Format: a tiny frontmatter header (``title`` + ``updated`` ISO timestamp) followed by the
markdown body. Kept deliberately separate from the agent's Claude memory.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..utils.atomic_write import atomic_write_text
from ..utils.paths import encode_project_path
from ..utils.project_identity import resolve_project_identity
from .config_path import get_config_dir


def summaries_dir() -> Path:
    """Directory holding one ``<project_id>.md`` per project."""
    return get_config_dir() / "session-summaries"


def project_key(project_path: str | Path, project_id: str | None = None) -> str:
    """Return the summary key for a project.

    Keyed by the machine-independent ``project_id`` (git identity), so the same
    project follows across machines like the agent's memory does. ``project_id`` may
    be passed to skip git resolution (e.g. from the sync status cache). Falls back to
    the local encoded path for projects with no git identity (local-only, like memory).
    """
    if project_id:
        return project_id
    identity = resolve_project_identity(project_path)
    if identity is not None:
        return identity.project_id
    return encode_project_path(str(Path(project_path).resolve()))


def summary_path(project_path: str | Path, project_id: str | None = None) -> Path:
    return summaries_dir() / f"{project_key(project_path, project_id)}.md"


def save(project_path: str | Path, content: str, title: str = "",
         project_id: str | None = None) -> Path:
    """Write (overwrite) the session summary for ``project_path``; stamp ``updated``."""
    path = summary_path(project_path, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    updated = datetime.now().isoformat(timespec="seconds")
    document = (
        "---\n"
        f"title: {title}\n"
        f"updated: {updated}\n"
        "---\n"
        f"{content}"
    )
    atomic_write_text(path, document)
    return path


def load(project_path: str | Path, project_id: str | None = None) -> dict | None:
    """Return ``{"title", "updated", "content"}`` for the project, or ``None``."""
    path = summary_path(project_path, project_id)
    if not path.is_file():
        return None
    return _parse(path.read_text(encoding="utf-8"))


def _parse(text: str) -> dict:
    """Split the leading ``---``…``---`` frontmatter from the markdown body."""
    title = ""
    updated = ""
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            header = text[4:end]
            body = text[end + len("\n---\n"):]
            for line in header.splitlines():
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                key, value = key.strip(), value.strip()
                if key == "title":
                    title = value
                elif key == "updated":
                    updated = value
    return {"title": title, "updated": updated, "content": body}
