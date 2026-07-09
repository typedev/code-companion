"""Git worktree awareness helpers (Stage 2).

A *linked* worktree has ``.git`` as a **file** containing ``gitdir: <path>`` that
points into ``<main>/.git/worktrees/<name>``. Per-worktree state (``HEAD``,
``index``, ``logs/HEAD``) lives there; shared refs (``refs/``, ``packed-refs``,
``logs/``) live in the **common** git dir (``<main>/.git``). These helpers resolve
those locations by reading the pointer files — no subprocess needed.
"""
from __future__ import annotations

import re
from pathlib import Path


def slugify(text: str) -> str:
    """A branch/path-safe slug from a task name, e.g. 'Add login!' -> 'add-login'."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "worktree"


def is_linked_worktree(path: str | Path) -> bool:
    """True if ``path`` is the root of a linked git worktree (``.git`` is a file)."""
    return (Path(path) / ".git").is_file()


def resolve_worktree_dirs(path: str | Path) -> tuple[Path, Path] | None:
    """``(git_dir, common_dir)`` for a linked worktree, or ``None`` if not one.

    - ``git_dir``    — the per-worktree dir (``HEAD``/``index``/``logs/HEAD`` here).
    - ``common_dir`` — the shared ``.git`` (``refs/``/``packed-refs``/``logs/`` here).
    """
    path = Path(path)
    dot_git = path / ".git"
    if not dot_git.is_file():
        return None
    try:
        text = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None

    git_dir = Path(text[len("gitdir:"):].strip())
    if not git_dir.is_absolute():
        git_dir = (path / git_dir).resolve()

    # commondir points from the per-worktree dir to the shared .git (usually ../..).
    common_dir = git_dir.parent.parent
    commondir_file = git_dir / "commondir"
    if commondir_file.is_file():
        try:
            rel = commondir_file.read_text(encoding="utf-8").strip()
            cd = Path(rel)
            common_dir = cd if cd.is_absolute() else (git_dir / cd).resolve()
        except OSError:
            pass
    return git_dir, common_dir


def worktree_parent_root(path: str | Path) -> Path | None:
    """The parent repository's working-tree root for a linked worktree, or ``None``."""
    dirs = resolve_worktree_dirs(path)
    if dirs is None:
        return None
    # common_dir is <main>/.git -> the repo root is its parent.
    return dirs[1].parent
