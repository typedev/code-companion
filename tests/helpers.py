"""Reusable helpers for the sync tests (git repo construction)."""

import os
import subprocess
from pathlib import Path

_GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(cwd: Path, *args: str) -> str:
    """Run a git command in cwd and return stripped stdout (raises on failure)."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_GIT_ENV,
        check=True,
    )
    return result.stdout.strip()


def make_bare(tmp_path: Path, name: str = "remote.git") -> Path:
    """Create an empty bare repo (default branch main) as the private sync remote.

    Real GitHub adopts the first pushed branch as its default; a plain local bare
    keeps its init default, so we pin it to ``main`` to match SyncRepo.
    """
    bare = tmp_path / name
    git(tmp_path, "init", "--bare", "-b", "main", "-q", str(bare))
    return bare


def init_repo(path: Path, *, remote: str | None = None, commit: bool = True) -> Path:
    """Create a git repo at path, optionally with an origin remote and a commit."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    if remote:
        git(path, "remote", "add", "origin", remote)
    if commit:
        (path / "README.md").write_text("hello\n", encoding="utf-8")
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "initial")
    return path
