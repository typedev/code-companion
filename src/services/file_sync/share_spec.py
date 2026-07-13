"""Resolve which project files participate in LAN file-sync.

The shared set is an *allowlist* — the opposite sense of ``.gitignore``: we want
the working files git ignores. A file participates iff it is matched by the
project's ``.shared`` file (gitignore syntax, negation supported) **or** lives
under a ``shared/`` folder, and is **not** already tracked by git, and is not in
a heavy/never-sync directory.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pathspec

# Directories never descended into (mirrors problems_service._SKIP_DIRS) plus our
# own trash. ``.git`` is here too, so git internals never enter the shared set.
_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build",
    ".tox", ".idea", ".vscode", "site-packages",
    ".deleted",
}

SHARED_DIR = "shared"
SHARED_FILE = ".shared"
DELETED_DIR = ".deleted"


def load_shared_spec(root: str | os.PathLike) -> pathspec.PathSpec | None:
    """Build a PathSpec from ``<root>/.shared`` (gitignore syntax), or None."""
    shared_file = Path(root) / SHARED_FILE
    if not shared_file.is_file():
        return None
    patterns: list[str] = []
    try:
        with open(shared_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    except OSError:
        return None
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, patterns)


def git_tracked_files(root: str | os.PathLike) -> set[str]:
    """Rel-paths (posix) git already tracks, so file-sync never overlaps git.

    Empty set if the project is not a git repo or git is unavailable.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(root), capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    return {p for p in out.stdout.decode("utf-8", "replace").split("\0") if p}


def is_shared_rel(rel: str, spec: pathspec.PathSpec | None) -> bool:
    """Whether a rel-path belongs to the shared set (before the git/skip filters)."""
    # Everything under the default ``shared/`` folder.
    if rel == SHARED_DIR or rel.startswith(SHARED_DIR + "/"):
        return True
    if spec is not None and spec.match_file(rel):
        return True
    return False


def resolve_shared_files(root: str | os.PathLike) -> set[str]:
    """Return the set of rel-paths (posix) that participate in file-sync.

    = (matched by ``.shared`` allowlist OR under ``shared/``)
      minus git-tracked files and ``.git``/``.deleted``/heavy dirs.
    """
    root = Path(root)
    spec = load_shared_spec(root)
    tracked = git_tracked_files(root)

    result: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune heavy/never-sync dirs in place so we never descend into them.
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            rel = (Path(dirpath) / name).relative_to(root).as_posix()
            if rel in tracked:
                continue
            if is_shared_rel(rel, spec):
                result.add(rel)
    return result
